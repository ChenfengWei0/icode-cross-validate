from pathlib import Path

import cv_check as C
import cv_text as T

ROOT = Path(__file__).resolve().parents[1]
SUBTASK = (ROOT / "examples" / "snake" / "subtask.txt").read_text(encoding="utf-8")
GOAL = [{"id": "S1", "text": "实现贪吃蛇。", "labels": ["无"], "quotable": ["实现贪吃蛇。"]}]


def check(extra: str, sentences=GOAL, original="实现贪吃蛇。"):
    parsed = T.parse_subtask(SUBTASK + "\n\n" + extra, original)
    return {p["id"]: p for p in C.check_propositions(parsed, sentences, original)}


def test_snake_example_passes():
    """细则 9.1 D-8 the document's ten propositions pass every check."""
    out = check("")
    assert all(not p["problems"] for p in out.values()) and len(out) == 10


def test_fields_and_ids():
    """细则 9.1 第 1、7 项 fields, values and unique ids."""
    parsed = T.parse_subtask(
        SUBTASK + "\n\n编号  R3\n来源  猜测\n依据  〔存在〕x\n句子  总是 蛇的长度 不小于 1", "实现贪吃蛇。"
    )
    problems = [x for p in C.check_propositions(parsed, GOAL, "实现贪吃蛇。") if p["id"] == "R3" for x in p["problems"]]
    assert any("重复" in x for x in problems) and any("来源只能是" in x for x in problems)
    assert any(
        "编号应是" in x
        for x in check("编号  第一条\n来源  推理\n依据  〔存在〕x\n句子  总是 蛇 移动")["第一条"]["problems"]
    )


def test_parse_and_unpatterned():
    """细则 9.1 第 2 项 a sentence parses, or 未套用句式 with a parsable formula."""
    out = check(
        "编号  R11\n来源  推理\n依据  〔存在〕x\n句子  蛇 很长\n\n"
        "编号  R12\n来源  推理\n依据  〔不变式〕食物总在蛇外\n句子  所有食物总不在蛇身上\n公式  未套用句式：∀x:食物 G ¬位于蛇身(x)\n\n"
        "编号  R13\n来源  推理\n依据  〔不变式〕x\n句子  总是 蛇 移动\n公式  G 移动(蛇)"
    )
    assert any("无法解析" in x for x in out["R11"]["problems"])
    assert out["R12"]["pattern"] == "未套用句式" and not any("解析" in x for x in out["R12"]["problems"])
    assert any("未套用句式" in x for x in out["R13"]["problems"])


def test_declared_and_bound():
    """细则 9.1 第 3 项 declared names, arity, types, bound variables."""
    out = check(
        "编号  R11\n来源  推理\n依据  〔存在〕x\n句子  长度(食物) 等于 1\n\n"
        "编号  R12\n来源  推理\n依据  〔存在〕x\n公式  未套用句式：G 移动(x)\n句子  x"
    )
    assert any("应是 蛇" in x for x in out["R11"]["problems"])
    assert any("没有被量词约束" in x for x in out["R12"]["problems"])


def test_no_implementation_names():
    """细则 9.1 第 4 项 ASCII names must come from the requirement's own words."""
    out = check(
        "编号  R11\n来源  推理\n依据  〔结构〕x\n句子  蛇的头 等于 「head」\n公式  未套用句式：G 头(蛇) = snake_head"
    )
    assert any("snake_head" in x for x in out["R11"]["problems"])


def test_translation():
    """细则 9.1 第 5 项 D-6 quote, keyword → pattern, numbers from the quote, labels."""
    sentences = [
        {
            "id": "S1",
            "text": "蛇的长度总是不小于 1。",
            "labels": ["规则", "示例"],
            "quotable": ["蛇的长度总是不小于 1。"],
        },
        {"id": "S2", "text": "蛇撞墙时游戏结束。", "labels": ["条件"], "quotable": ["蛇撞墙时游戏结束。"]},
    ]
    original = "蛇的长度总是不小于 1。蛇撞墙时游戏结束。"
    out = check(
        "编号  R11\n来源  翻译\n依据  「蛇的长度总是不小于 1」\n句子  总是 蛇的长度 不小于 1\n\n"
        "编号  R12\n来源  翻译\n依据  「蛇的长度总是不小于 1」\n句子  从不 蛇的长度 小于 1\n\n"
        "编号  R13\n来源  翻译\n依据  「蛇的长度总是不小于 1」\n句子  总是 蛇的长度 不小于 2\n\n"
        "编号  R14\n来源  翻译\n依据  「蛇撞墙」\n句子  只要 蛇 撞到自身，就 游戏 结束\n\n"
        "编号  R15\n来源  翻译\n依据  「蛇撞墙时游戏结束」\n句子  只要 蛇 撞到自身，就 游戏 结束\n\n"
        "编号  R16\n来源  翻译\n依据  蛇撞墙时游戏结束\n句子  只要 蛇 撞到自身，就 游戏 结束",
        sentences,
        original,
    )
    assert out["R11"]["problems"] == [] and out["R15"]["problems"] == []
    assert any("要求句式为" in x and "命题用的是 从不" in x for x in out["R12"]["problems"])
    assert any("数 2" in x for x in out["R13"]["problems"])
    assert out["R14"]["problems"] == []
    assert any("「」引用原话" in x for x in out["R16"]["problems"])
    unlabeled = check("编号  R11\n来源  翻译\n依据  「实现贪吃蛇」\n句子  存在一个 x，使 x 是 蛇")
    assert any("带标签句子" in x for x in unlabeled["R11"]["problems"])


def test_allowed_patterns():
    """细则 9.1 第 5 项 示例 and 接口规定 labels open the example and structure patterns."""
    assert C.allowed_patterns("如果超时就重试", ["条件"]) == {"只要就"}
    assert "调用得到" in C.allowed_patterns("例如 add(1, 2) 得到 3", ["示例"])
    assert "结构：签名" in C.allowed_patterns("函数 add 接受两个整数", ["接口规定"])
    assert C.allowed_patterns("初始时为空", ["规则"]) == set()


def test_reasoning_item():
    """细则 9.1 第 6 项 the reasoning item is one of the nine and lists the proposition."""
    out = check(
        "编号  R11\n来源  推理\n依据  〔颜色〕x\n句子  总是 蛇 移动\n\n编号  R12\n来源  推理\n依据  〔边界〕x\n句子  总是 蛇 移动"
    )
    assert any("不是九项之一" in x for x in out["R11"]["problems"])
    assert any("没有列出 R12" in x for x in out["R12"]["problems"])
