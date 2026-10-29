import json
from pathlib import Path

import cv_check as C
import cv_text as T

ROOT = Path(__file__).resolve().parents[1]
SUBTASK = (ROOT / "examples" / "snake" / "subtask.txt").read_text(encoding="utf-8")
R = "余额不能为负。转账时，转出等于转入！\n例如 100 转出 30。"


def test_split_must_cover_r_verbatim():
    """细则 6.1 the split is verbatim, in order, and covers R."""
    ok = json.dumps(
        [
            {"id": "S1", "text": "余额不能为负。", "labels": ["规则"]},
            {"id": "S2", "text": "转账时，转出等于转入！", "labels": ["条件", "规则"]},
            {"id": "S3", "text": "例如 100 转出 30。", "labels": ["示例"]},
        ],
        ensure_ascii=False,
    )
    sentences, errors = T.check_split("```json\n" + ok + "\n```", R)
    assert errors == [] and [s["id"] for s in sentences] == ["S1", "S2", "S3"]
    missing = json.dumps(
        [{"text": "余额不能为负。", "labels": ["规则"]}, {"text": "例如 100 转出 30。", "labels": ["示例"]}],
        ensure_ascii=False,
    )
    assert any("没有被任何句子覆盖" in e for e in T.check_split(missing, R)[1])
    reworded = json.dumps([{"text": "余额不可为负。", "labels": ["规则"]}], ensure_ascii=False)
    assert any("逐字片段" in e for e in T.check_split(reworded, R)[1])


def test_split_fallback():
    """细则 6.1 17.1 the fallback splits at 。！？； and newlines and labels everything 无."""
    sentences = T.fallback_split(R)
    assert [s["text"] for s in sentences] == ["余额不能为负。", "转账时，转出等于转入！", "例如 100 转出 30。"]
    assert all(s["labels"] == ["无"] for s in sentences)


def test_labels():
    """细则 6.2 labels come from the five classes or 无."""
    bad = json.dumps([{"text": R, "labels": ["需求"]}], ensure_ascii=False)
    assert any("labels" in e for e in T.check_split(bad, R)[1])


def test_quotable():
    """细则 6.3 a sentence may quote the verbatim fragments of the R entries it overlaps."""
    entries = [
        {"start": 0, "end": 5, "quotes": [{"from": "M1", "text": "满100减20"}]},
        {"start": 6, "end": 12, "quotes": [{"from": "M3", "text": "改成满200减30"}]},
    ]
    assert C.quotable_for({"start": 0, "end": 5, "text": "x"}, entries) == ["满100减20"]
    assert C.quotable_for({"start": 3, "end": 9, "text": "x"}, entries) == ["满100减20", "改成满200减30"]
    assert C.quotable_for({"start": 0, "end": 5, "text": "原句"}, None) == ["原句"]


def test_declarations():
    """细则 7.1 9.2 categories, continuation lines, names, parameters, interface names."""
    parsed = T.parse_subtask(SUBTASK, "实现贪吃蛇。")
    d = parsed["decls"]
    assert parsed["errors"] == []
    assert d.params["吃到"] == ["蛇", "食物"] and d.kind("尾") == "位置" and d.params["格子总数"] == []
    lines = [
        "实体  蛇",
        "谓词  结束          没有参数",
        "数量  长度(鱼)  x",
        "接口  `move`  移动",
        "实体  不",
        "颜色  红",
    ]
    _, errors = T.parse_declarations(lines, "需求原话")
    joined = "\n".join(errors)
    for expect in ("至少要有一个参数", "不是已声明的实体", "没有逐字出现在需求原话中", "保留字", "类别不合法"):
        assert expect in joined


def test_checklist():
    """细则 7.2 nine items, each with ids or 无： and a reason."""
    parsed = T.parse_subtask(SUBTASK, "实现贪吃蛇。")
    assert parsed["checklist"]["不变式"]["ids"] == ["R3", "R6"]
    _, errors = T.parse_checklist(["存在  R1", "状态  无", "颜色  R2"])
    joined = "\n".join(errors)
    assert "没有写理由" in joined and "清单项不合法：颜色" in joined and "清单缺少一项：终态" in joined


def test_propositions():
    """细则 7.3 blocks of field lines separated by blank lines."""
    parsed = T.parse_subtask(SUBTASK, "实现贪吃蛇。")
    props = parsed["propositions"]
    assert [p["编号"] for p in props] == [f"R{i}" for i in range(1, 11)]
    assert props[3]["依据"] == "〔动作的效果〕吃到食物是蛇变长的方式"
    p, _ = T.parse_propositions(["编号  R1", "来源  推理", "颜色  红"])
    assert p[0]["problems"] == ["无法识别的行：颜色  红"]
