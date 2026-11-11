"""细则 7.5 7.6 13.8 14.2 17.12: the review step, its marks, and how they reach the conclusion."""

from pathlib import Path

import cv_check as C
import cv_flow as FLOW
import cv_grammar as G
import cv_report as RP
import cv_state as S
from test_flow import goal_run

REQ = "取款后余额减少取款金额。例如余额 100 时取 30，余额变成 60。"
PROPS = {
    "R1": {"id": "R1", "source": "翻译", "basis": "「取款后余额减少取款金额」", "sentence": "s1"},
    "R2": {"id": "R2", "source": "翻译", "basis": "「余额 100 时取 30，余额变成 60」", "sentence": "s2"},
    "R3": {"id": "R3", "source": "推理", "basis": "〔边界〕余额为 0 时取款报错", "sentence": "s3"},
    "R4": {"id": "R4", "source": "推理", "basis": "〔动作的效果〕按「取款后余额减少取款金额」推出", "sentence": "s4"},
}
TWO_READINGS = "需求没说余额为 0 时怎样，可以报错，也可以返回 0，这里按报错处理"


def parse(answer):
    return FLOW.parse_review(answer, PROPS, REQ)


def test_review_answer_format():
    """细则 7.5 the answer format: separators, decorations, and 无."""
    assert parse("无") == ([], []) and parse("无。\n") == ([], [])
    marks, problems = parse(
        f"- **R3**  〔需求未说明〕  {TWO_READINGS}\n```\nR1  〔需求矛盾〕  R2  例子是 60，按规则算是 70"
    )
    assert problems == [] and [m["id"] for m in marks] == ["R3", "R1"] and marks[1]["with"] == ["R2"]
    marks, _ = parse("R1\t〔需求矛盾〕\tR2、R4\t三处说法不同")
    assert marks[0]["with"] == ["R2", "R4"]


def test_review_rejects_what_it_may_not_mark():
    """细则 7.5 7.3 unstated only on reasoning, with two readings; conflicts only between quoted sides."""
    cases = {
        f"R1  〔需求未说明〕  {TWO_READINGS}": "只能标推理命题",
        "R3  〔需求未说明〕  需求没说": "两种读法",
        "R9  〔需求未说明〕  x": "不是通过检查的命题编号",
        "R3  〔需求不清〕  x": "标记只能是",
        "R1  〔需求矛盾〕  R1  自相矛盾": "其他通过检查的命题编号",
        "R1  〔需求矛盾〕  R2": "理由不能为空",
        # A reasoned side without a quote of the requirement: the reasoning is wrong, not the requirement.
        "R1  〔需求矛盾〕  R3  规则和推理不一致": "没有需求原文的逐字引文",
        # Both sides quote the same words: no contradiction in the requirement.
        "R1  〔需求矛盾〕  R4  同一句话": "同样的原话",
    }
    for line, why in cases.items():
        marks, problems = parse(line)
        assert marks == [] and len(problems) == 1 and why in problems[0][1], (line, problems)
    # A reasoned side may stand on a verbatim quote of another sentence.
    marks, problems = parse("R2  〔需求矛盾〕  R4  例子是 60，按规则推出 70")
    assert problems == [] and marks[0]["with"] == ["R4"]


def _reviewed(home, tmp_path):
    state, _ = goal_run(home, tmp_path)
    run = Path(state["run"])
    accepted = [p["id"] for p in FLOW._accepted(run)]
    return state, run, accepted


def test_review_round_trip(home, tmp_path):
    """细则 7.5 5.2 marks are written, the rewrite only replaces the lines sent back, and the file is refrozen."""
    state, run, _ = _reviewed(home, tmp_path)
    assert FLOW.review_prompt(state) and "实现贪吃蛇" in FLOW.review_prompt(state)
    first = f"R5  〔需求未说明〕  {TWO_READINGS}\nR99  〔需求未说明〕  x\nR6  〔需求未说明〕  需求没说"
    state, again = FLOW.review_check(state, first, 1)
    assert again and "R99" in again and "R6" in again and "R5" not in again.split("你上一次的输出")[0]
    rewrite = f"R6  〔需求未说明〕  {TWO_READINGS}\nR7  〔需求未说明〕  {TWO_READINGS}"
    state, again = FLOW.review_check(state, rewrite, 2)
    assert again is None
    props = {p["id"]: p for p in S.read_json(run / "propositions.json")["propositions"]}
    assert props["R5"]["unstated_by"] == "review" and props["R6"]["unstated"] and not props["R7"].get("unstated")
    assert "复查：〔需求未说明〕" in props["R5"]["basis"]
    assert state["digests"]["propositions.json"] == S.sha256_file(run / "propositions.json")


def test_review_without_answer(home, tmp_path):
    """细则 4.8 7.5 no answer: no marks, and a note."""
    state, run, _ = _reviewed(home, tmp_path)
    before = S.sha256_file(run / "propositions.json")
    state, again = FLOW.review_check(state, None, 1)
    assert again is None and any("复查" in n and "没有回答" in n for n in state["notes"])
    assert S.sha256_file(run / "propositions.json") == before


def test_conflict_groups_are_connected(home, tmp_path):
    """细则 7.5 R1–R2 and R2–R3 make one group of three."""
    _, run, accepted = _reviewed(home, tmp_path)
    a, b, c = accepted[:3]
    FLOW._apply_review(run, [{"id": a, "mark": "〔需求矛盾〕", "with": [b], "reason": "x"}])
    FLOW._apply_review(run, [{"id": c, "mark": "〔需求矛盾〕", "with": [b], "reason": "y"}])
    props = {p["id"]: p for p in S.read_json(run / "propositions.json")["propositions"]}
    assert props[a]["conflict"] == [b, c] and props[b]["conflict"] == [a, c] and props[c]["conflict"] == [a, b]


def _data(accepted, results, submitted=None):
    return {
        "invalid": [],
        "stopped": None,
        "stats": {"submitted": submitted or len(accepted), "discarded": 0},
        "results": results,
        "accepted": accepted,
    }


def test_conflict_group_fails_only_when_every_side_fails():
    """细则 7.5 13.8 14.2 17.12 correct code meets one side; code meeting neither is still caught."""
    accepted = [
        {"id": "R1", "class": "具体事实", "conflict": ["R2"]},
        {"id": "R2", "class": "具体事实", "conflict": ["R1"]},
        {"id": "R3", "class": "时序"},
        {"id": "R4", "class": "时序"},
    ]
    one_side = {"R1": {"result": "失败（检查）"}, "R2": {"result": "通过"}, "R3": {"result": "推定为真"}}
    one_side["R4"] = {"result": "推定为真"}
    assert RP.conclusion(_data(accepted, one_side), 0.5)[0] == RP.PASSED
    both = {**one_side, "R2": {"result": "确定违反"}}
    assert RP.conclusion(_data(accepted, both), 0.5)[0] == RP.FAILED
    # 细则 17.12: without the review the one-sided failure would have failed the code.
    assert RP.conclusion(_data(accepted, one_side), 0.5, ignore_review=True)[0] == RP.FAILED


def test_too_many_marks_give_no_conclusion_but_a_failure_still_fails():
    """细则 14.2 marks over max_confirm_ratio give no certificate; a counted failure is reported first."""
    accepted = [
        {"id": "R1", "class": "时序", "unstated": True, "unstated_by": "review"},
        {"id": "R2", "class": "时序", "unstated": True},
        {"id": "R3", "class": "时序"},
    ]
    results = {k: {"result": "推定为真"} for k in ("R1", "R2", "R3")}
    assert RP.conclusion(_data(accepted, results), 0.5)[0] == RP.NO_CONCLUSION
    assert RP.conclusion(_data(accepted, results), 0.5, ignore_review=True)[0] == RP.PASSED
    failing = {**results, "R3": {"result": "推定为真", "vacuous": True}, "R4": {"result": "失败（检查）"}}
    with_r4 = [*accepted, {"id": "R4", "class": "具体事实"}]
    assert RP.conclusion(_data(with_r4, failing), 0.5)[0] == RP.FAILED


def test_translated_signature_is_written_as_the_requirement(decls):
    """细则 7.6 9.1 第 5 项 annotations and return types not in the quote are not a translation."""

    def problems(sentence, quote):
        return C.signature_problems(G.parse_sentence(sentence, decls)[0], [quote])

    assert problems("`move` 的签名是 「(self, d)」", "def move(self, d):") == []
    assert problems("`move` 的签名是 「(self, d: str) -> bool」", "def move(self, d: str) -> bool:") == []
    assert "返回类型" in problems("`move` 的签名是 「(self, d) → None」", "def move(self, d):")[0]
    assert "参数表" in problems("`move` 的签名是 「(self, d: str)」", "def move(self, d):")[0]


def test_ungrounded_side_is_dropped():
    """细则 7.5 a side with no verbatim quote leaves the line; the grounded sides are kept."""
    marks, problems = parse("R2  〔需求矛盾〕  R1、R3  例子是 60，按规则算是 70")
    assert problems == [] and marks[0]["with"] == ["R1"] and marks[0]["dropped"] == ["R3"]
    marks, problems = parse("R1  〔需求矛盾〕  R3  只有推理")
    assert marks == [] and "没有需求原文的逐字引文" in problems[0][1]
