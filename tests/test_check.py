from pathlib import Path

import cv_check as C
import cv_text as T

ROOT = Path(__file__).resolve().parents[1]
SUBTASK = (ROOT / "examples" / "snake" / "subtask.txt").read_text(encoding="utf-8")
GOAL = [{"id": "S1", "text": "实现贪吃蛇。", "labels": ["无"], "quotable": ["实现贪吃蛇。"]}]


def check(extra: str, sentences=GOAL, original="实现贪吃蛇。", decls=""):
    base = SUBTASK.replace("数量  长度(蛇)", decls + "数量  长度(蛇)") if decls else SUBTASK
    parsed = T.parse_subtask(base + "\n\n" + extra, original)
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
    assert any("句式 从不 在引文中没有对应" in x for x in out["R12"]["problems"])
    assert any("数 2" in x for x in out["R13"]["problems"])
    # 细则 17.9: a rule sentence is its own basis, whatever part of it is quoted.
    assert out["R14"]["problems"] == []
    assert any("「」引用原话" in x for x in out["R16"]["problems"])
    unlabeled = check("编号  R11\n来源  翻译\n依据  「实现贪吃蛇」\n句子  存在一个 x，使 x 是 蛇")
    assert any("带标签句子" in x for x in unlabeled["R11"]["problems"])


def test_pattern_basis():
    """细则 9.1 第 5 项 keywords and labels allow patterns; they never forbid one."""
    assert C.pattern_basis("只要就", "如果超时就重试", ["条件"])
    assert C.pattern_basis("只要下一步", "蛇吃到食物时长度加一", ["规则"])
    assert not C.pattern_basis("只要就", "初始时为空", ["示例"])
    # 细则 17.9: 规则、条件、数量约束 allow these without a keyword; 从不 still needs a negation.
    assert C.pattern_basis("只要就", "空栈调用 pop 抛出 IndexError", ["规则"])
    assert C.pattern_basis("总是", "pop 返回最后压入的元素", ["条件"])
    assert not C.pattern_basis("从不", "不需要界面", ["规则"])
    assert C.pattern_basis("调用得到", "例如 add(1, 2) 得到 3", ["示例"])
    assert not C.pattern_basis("调用得到", "例如 add(1, 2) 得到 3", ["规则"])
    assert C.pattern_basis("结构：签名", "函数 add 接受两个整数", ["接口规定"])
    # "时间" contains 时, but that only allows 只要就; it does not forbid 总是.
    assert C.pattern_basis("总是", "游戏时间总是不超过 60 秒", ["规则"])
    assert C.pattern_basis("具体事实", "格子总数为 100", ["规则"])


def test_quotes_and_numbers():
    """细则 9.1 第 5 项 nested 「」 quotes; numbers read by value, inside JSON literals too."""
    assert C.quotes_in("依据「按「开始」键后游戏运行」与「x」") == ["按「开始」键后游戏运行", "x"]
    assert C.quote_numbers("长度为 10，宽１２，十二格，两百分，一个蛇") == [10.0, 12.0, 12.0, 200.0, 1.0]
    sentences = [
        {
            "id": "S1",
            "text": "例如 move(1, 2) 得到 [3]。",
            "labels": ["示例"],
            "quotable": ["例如 move(1, 2) 得到 [3]。"],
        }
    ]
    out = check(
        "编号  R11\n来源  翻译\n依据  「例如 move(1, 2) 得到 [3]」\n句子  调用 `move`(1, 2) 得到 [3]\n\n"
        "编号  R12\n来源  翻译\n依据  「例如 move(1, 2) 得到 [3]」\n句子  调用 `move`(1, 2) 得到 [99]\n\n"
        "编号  R13\n来源  翻译\n依据  「例如 move(1, 2) 得到 [3]」\n句子  调用 `move`(1, 20) 得到 [3]",
        sentences,
        "例如 move(1, 2) 得到 [3]。",
        decls="接口  move  需求中的函数\n",
    )
    assert out["R11"]["problems"] == []
    assert any("数 99" in x for x in out["R12"]["problems"])
    assert any("数 20" in x for x in out["R13"]["problems"])


def test_reasoning_item():
    """细则 9.1 第 6 项 the reasoning item is one of the nine and lists the proposition."""
    out = check(
        "编号  R11\n来源  推理\n依据  〔颜色〕x\n句子  总是 蛇 移动\n\n编号  R12\n来源  推理\n依据  〔边界〕x\n句子  总是 蛇 移动"
    )
    assert any("不是九项之一" in x for x in out["R11"]["problems"])
    assert any("没有列出 R12" in x for x in out["R12"]["problems"])
