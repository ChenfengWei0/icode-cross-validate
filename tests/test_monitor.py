import pytest

import cv_formula as F
import cv_grammar as G
import cv_monitor as M

ENT = {"蛇", "食物", "游戏"}


def st(length=3, eat=False, hit=False, over=False, moved=True, action="右", **more):
    values = {"长度(蛇)": length, "吃到(蛇,食物)": eat, "撞到自身(蛇)": hit, "结束(游戏)": over, "移动(蛇)": moved}
    values.update(more)
    return {"值": values, "动作": action}


def judge(sentence, states, decls):
    return M.judge(G.parse_sentence(sentence, decls)[0], states, ENT)["result"]


R4 = "只要 蛇 吃到 食物，下一步 蛇的长度 等于 蛇的长度 加 1"
R5 = "只要 蛇 撞到自身，下一步 游戏 结束"


def test_time_convention(decls):
    """细则 12.2 an event at state i is the step i→i+1; R4 compares length at i+1 with length at i."""
    assert judge(R4, [st(3, eat=True), st(4)], decls) == M.PRESUMED_TRUE
    assert judge(R4, [st(3, eat=True), st(3)], decls) == M.VIOLATED


def test_four_values(decls):
    """细则 13.1 13.2 D-15 确定违反, 推定为假, 推定为真, 确定成立."""
    assert judge("总是 蛇的长度 不小于 4", [st(4), st(3)], decls) == M.VIOLATED
    assert judge("只要 游戏 没有结束，最终 游戏 结束", [st(), st()], decls) == M.PRESUMED_FALSE
    assert judge("总是 蛇的长度 不小于 1", [st(), st()], decls) == M.PRESUMED_TRUE
    tree = F.parse("F 结束(游戏)", decls)
    assert M.judge(tree, [st(), st(over=True)], ENT) == {"result": M.SATISFIED, "state": 1, "truncated": False}


def test_weak_next(decls):
    """细则 10.3 17.3 D-16 X is weak: a trace ending right after the event leaves R4, R5 推定为真."""
    assert judge(R4, [st(3), st(3, eat=True)], decls) == M.PRESUMED_TRUE
    assert judge(R5, [st(3, hit=True)], decls) == M.PRESUMED_TRUE
    # The negation of a weak next is a strong next, false at the end of the trace.
    assert M.judge(F.parse("¬X 结束(游戏)", decls), [st()], ENT)["result"] == M.PRESUMED_FALSE
    assert M.judge(F.parse("¬(长度(蛇)′ = 3)", decls), [st()], ENT)["result"] == M.PRESUMED_FALSE


def test_nnf_duals(decls):
    """细则 10.3 negation through G F U W gives F G R M."""
    n = M.nnf(F.parse("¬G 结束(游戏) ∧ ¬(移动(蛇) U 结束(游戏)) ∧ ¬(移动(蛇) W 结束(游戏))", decls))
    assert [a["op"] for a in n["args"]] == ["F", "R", "M"]
    assert M.judge(F.parse("¬(移动(蛇) W 结束(游戏))", decls), [st(), st()], ENT)["result"] == M.PRESUMED_FALSE
    assert M.judge(F.parse("¬(移动(蛇) U 结束(游戏))", decls), [st(), st()], ENT)["result"] == M.PRESUMED_TRUE


def test_values(decls):
    """细则 12.3 a missing key is an alignment failure; an absent individual makes the atom false."""
    with pytest.raises(M.AlignError, match="缺少"):
        judge(R5, [{"值": {"撞到自身(蛇)": False}}], decls)
    tree = F.parse("∀x:食物 ¬位于蛇身(x)", decls)
    states = [{"实体": {"食物": ["f1"], "蛇": ["蛇"]}, "值": {"位于蛇身(f1)": False}}]
    assert M.judge(tree, states, ENT)["result"] == M.PASSED
    absent = F.parse("位于蛇身(「f2」)", decls)
    assert M.judge(absent, states, ENT)["result"] == M.CHECK_FAILED
    assert M.judge(F.parse("¬位于蛇身(「f2」)", decls), states, ENT)["result"] == M.PASSED


def test_entity_constant(decls):
    """细则 12.4 an entity used as a constant needs exactly one individual in every state."""
    tree = F.parse("位于蛇身(食物)", decls)
    good = [{"实体": {"食物": ["f1"]}, "值": {"位于蛇身(f1)": True}}]
    assert M.judge(tree, good, ENT)["result"] == M.PASSED
    with pytest.raises(M.AlignError, match="恰有一个"):
        M.judge(tree, [{"实体": {"食物": ["f1", "f2"]}, "值": {}}], ENT)


def test_scenario(decls):
    """细则 12.5 a 从 S 出发 proposition is judged only on traces whose actions match."""
    tree, _ = G.parse_sentence("从 蛇的长度 等于 3 出发，依次执行 右、下，之后 游戏 结束", decls)
    match = [st(3, action="右"), st(3, action="下"), st(3, over=True, action=None)]
    other = [st(3, action="左"), st(3, action="下"), st(3, action=None)]
    assert M.scenario_matches(tree, match, ENT) and not M.scenario_matches(tree, other, ENT)
    assert not M.scenario_matches(tree, match[:2], ENT)
    assert M.judge(tree, match, ENT)["result"] == M.PASSED


def test_order():
    """细则 13.3 失败（检查）＝确定违反 ＜ 失败（对齐）＜ 推定为假 ＜ 推定为真 ＜ 确定成立 ＜ 通过."""
    chain = [M.VIOLATED, M.ALIGN_FAILED, M.PRESUMED_FALSE, M.PRESUMED_TRUE, M.SATISFIED, M.PASSED]
    assert [M.ORDER[c] for c in chain] == sorted(M.ORDER[c] for c in chain)
    assert M.ORDER[M.CHECK_FAILED] == M.ORDER[M.VIOLATED]
    assert M.worst([M.PRESUMED_TRUE, M.ALIGN_FAILED, M.VIOLATED]) == M.VIOLATED


def test_liveness(decls):
    """细则 13.5 F and U parts mark a proposition as liveness."""
    live = {s: M.is_liveness(G.parse_sentence(s, decls)[0]) for s in ("只要 游戏 没有结束，最终 蛇 移动", R4, R5)}
    assert live == {"只要 游戏 没有结束，最终 蛇 移动": True, R4: False, R5: False}


def test_facts_at_state_zero(decls):
    """细则 13.2 第 6 步 a non-temporal proposition is judged at state 0: 通过 or 失败."""
    assert judge("存在一个 x，使 x 是 蛇", [{"实体": {"蛇": ["s"]}, "值": {}}], decls) == M.PASSED
    assert judge("存在一个 x，使 x 是 蛇", [{"实体": {"蛇": []}, "值": {}}], decls) == M.CHECK_FAILED
    assert judge("初始时 蛇的长度 等于 3", [st(3), st(4)], decls) == M.PASSED


def test_empty_trace(decls):
    """细则 12.6 an empty trace is an alignment failure."""
    with pytest.raises(M.AlignError, match="轨迹为空"):
        judge(R5, [], decls)
