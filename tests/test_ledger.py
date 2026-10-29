import json
import threading

import cv_ledger as L


def msgs(*texts):
    return [{"id": f"M{i}", "text": t, "dropped": False, "replaced": False} for i, t in enumerate(texts, 1)]


def test_append_ids_and_lock(home, tmp_path):
    """细则 15.2 one line per message, ids increase, appends hold the file lock."""
    project = tmp_path / "p"
    project.mkdir()
    threads = [threading.Thread(target=L.append_message, args=(project, "s", f"消息{i}", False)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = L.read_messages(L.ledger_file(project))
    assert sorted(int(m["id"][1:]) for m in got) == list(range(1, 21))
    assert set(got[0]) == {"id", "time", "session", "injected", "text"}


def test_preprocess():
    """细则 15.3 pasted code, stack traces and paths are replaced; pasted reports are dropped."""
    out = L.preprocess(
        msgs(
            "改成满 200 减 30\n```python\nprint(1)\n```",
            '报错了 Traceback (most recent call last):\n  File "/a/b.py", line 3',
            "看一下 src/app/main.py 和 /etc/hosts，比例 1/2 不变",
            "# 交叉验证可信度报告\n...",
            "接口叫 apply_discount",
        )
    )
    assert out[0]["text"].startswith("改成满 200 减 30") and L.PLACEHOLDER in out[0]["text"] and out[0]["replaced"]
    assert "Traceback" not in out[1]["text"] and "line 3" not in out[1]["text"]
    assert "main.py" not in out[2]["text"] and "/etc/hosts" not in out[2]["text"] and "1/2" in out[2]["text"]
    assert out[3]["dropped"] and out[3]["text"] == ""
    assert out[4]["text"] == "接口叫 apply_discount" and not out[4]["replaced"]


def answer(reqs, messages):
    return json.dumps({"requirement": reqs, "messages": messages}, ensure_ascii=False)


LEDGER = msgs("下单时满 100 元减 20 元。", "继续", "改成满 200 减 30", "再加一条：会员打九折", "会员那条不要了")


def test_consolidation_ok():
    """细则 15.4 dispositions, quotes verbatim, required quotes, earlier targets."""
    ok = answer(
        [
            {
                "text": "下单时总价满 200 元减 30 元。",
                "quotes": [
                    {"from": "M1", "text": "下单时满 100 元减 20 元"},
                    {"from": "M3", "text": "改成满 200 减 30"},
                ],
            }
        ],
        {"M1": "已被更正", "M2": "非需求", "M3": "更正 M1", "M4": "已被更正", "M5": "撤销 M4"},
    )
    data, errors = L.check_consolidation(ok, LEDGER)
    assert errors == [] and data["messages"]["M5"] == "撤销 M4"
    req, txt = L.build_requirement(data["entries"], data["messages"], "积累")
    assert txt == "下单时总价满 200 元减 30 元。" and req["entries"][0]["start"] == 0


def test_consolidation_errors():
    """细则 15.4 every rule the program checks."""
    bad = answer(
        [{"text": "会员 VIP 打九折", "quotes": [{"from": "M4", "text": "会员打 9 折"}]}, {"text": "空", "quotes": []}],
        {"M1": "需求", "M2": "闲聊", "M3": "更正 M5", "M4": "补充", "M5": "已被更正"},
    )
    _, errors = L.check_consolidation(bad, LEDGER)
    joined = "\n".join(errors)
    for expect in (
        "不是 M4 的逐字子串",
        "没有原话片段",
        "M2 的去向不合法",
        "不是更早的消息",
        "M1（需求）没有被 R 的任何一条引用",
        "没有后来的更正或撤销指向它",
        "VIP 没有出现在它的原话片段中",
    ):
        assert expect in joined, expect


def test_consolidation_input_skips_dropped():
    """细则 15.3 15.4 dropped messages are not shown and are 非需求."""
    m = L.preprocess(msgs("需求一", "交叉验证可信度报告 ..."))
    assert L.consolidation_input(m) == "M1：需求一"
    data, errors = L.check_consolidation(
        answer([{"text": "需求一", "quotes": [{"from": "M1", "text": "需求一"}]}], {"M1": "需求"}), m
    )
    assert errors == [] and data["messages"]["M2"] == "非需求"


def test_new_ledger(home, tmp_path):
    """细则 15.6 cv new starts a new ledger and keeps the old one."""
    project = tmp_path / "p"
    project.mkdir()
    L.append_message(project, "s", "旧需求", False)
    assert L.new_ledger(project) == "Q2"
    assert L.current_id(project) == "Q2" and L.read_messages(L.ledger_file(project, "Q1"))
    assert L.read_messages(L.ledger_file(project)) == []
