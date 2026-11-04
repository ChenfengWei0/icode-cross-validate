"""Regressions from the adversarial review (2026-10-30). Each test names the rule it holds the code to."""

import itertools
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

import pytest

import cv_check as C
import cv_flow as FLOW
import cv_formula as F
import cv_grammar as G
import cv_monitor as M
import cv_run as RUN
import cv_state as S
import cv_text as T
from test_align_run import LIMITS, frozen_for, props
from test_check import check
from test_flow import goal_run
from test_monitor import ENT, st

# ---------------------------------------------------------------------------------------------------
# Parsing the subtask's answer
# ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["长度(蛇) = ３", "长度(蛇) = 03", "长度(蛇) = ²", "长度(蛇) = -05", "长度(蛇) = " + "9" * 40]
)
def test_bad_numbers_in_formulas(decls, text):
    """细则 8.1 4.8 numbers that are not ASCII decimals are a parse error, never a crash."""
    with pytest.raises(F.FormulaError):
        F.parse(text, decls)


@pytest.mark.parametrize("sentence", ["初始时 蛇的长度 等于 03", "调用 `move`(007) 得到 7", "总是 蛇的长度 大于 １"])
def test_bad_numbers_in_sentences(decls, sentence):
    """细则 8.1 4.8 the same in controlled sentences."""
    with pytest.raises(F.FormulaError):
        G.parse_sentence(sentence, decls)


def test_unreadable_propositions_are_not_crashes():
    """细则 4.8 9.1 第 3 项 structure arity, deep nesting: a problem on the proposition, not an exception."""
    out = check(
        "编号  R11\n来源  推理\n依据  〔结构〕x\n句子  见公式\n公式  未套用句式：表示()\n\n"
        "编号  R12\n来源  推理\n依据  〔结构〕x\n句子  见公式\n公式  未套用句式："
        + "(" * 300
        + "结束(游戏)"
        + ")" * 300
    )
    assert out["R11"]["problems"] and out["R12"]["problems"]


def test_decorated_fields_and_lost_blocks():
    """细则 7.3 bold field names and full-width colons are read; a block without a readable id is kept."""
    out = check(
        "**编号**：R11\n**来源**：推理\n**依据**：〔不变式〕x\n**句子**：总是 蛇的长度 不小于 1\n\n"
        "号码  R12\n来源  推理\n依据  〔不变式〕x\n句子  总是 蛇的长度 不小于 1"
    )
    assert out["R11"]["problems"] == [] or all("清单段" in x for x in out["R11"]["problems"])
    assert any("读不出编号" in x for x in out[""]["problems"])


def test_formula_must_not_contradict_sentence():
    """细则 9.1 第 2 项 a sentence that fits a pattern takes no formula."""
    out = check("编号  R11\n来源  推理\n依据  〔终态〕x\n句子  总是 游戏 不结束\n公式  未套用句式：F 结束(游戏)")
    assert any("不应再写公式" in x for x in out["R11"]["problems"])


def test_types_and_arity(decls):
    """细则 8.7 9.1 第 3 项 untyped variables, wrong structure arguments, predicates used as terms."""
    for text in ("∃x x = 3", "存在()", "存在(蛇)", "只由(长度(蛇))", "长度(蛇) = 吃到(蛇, 食物)", "∀x 长度(蛇) > x"):
        try:
            tree = F.parse(text, decls)
        except F.FormulaError:
            continue
        assert F.type_errors(tree, decls), text


def test_implementation_names():
    """细则 9.1 第 4 项 subscripts are not names; quotes in the text do not hide names."""
    assert C._ascii_names("长度(蛇)_0 = 3") == set()
    assert C._ascii_names('["\\""] 等于 snake_len') == {"snake_len"}
    assert C._ascii_names('「snake_len」 等于 ["abc"]') == set()


def test_glued_ambiguity(decls):
    """细则 7.1 8.3 a declared 不p next to a declared p is refused."""
    _, errors = T.parse_declarations(["谓词  动(蛇)  动了", "      不动(蛇)  没动", "实体  蛇"], "")
    assert any("冲突" in e for e in errors)
    decls.category["不移动"], decls.params["不移动"] = "谓词", ["蛇"]
    with pytest.raises(F.FormulaError, match="歧义"):
        G.parse_sentence("总是 蛇 不移动", decls)


def test_only_modifies_needs_a_term(decls):
    """细则 8.5 只有 `M` 修改 D: D is a code name or a declared position or quantity."""
    with pytest.raises(F.FormulaError):
        G.parse_sentence("只有 `move` 修改 蛇", decls)


def test_names_round_trip(decls):
    """细则 7.1 10.2 printing then parsing gives the same tree, also after W and with negative literals."""
    _, errors = T.parse_declarations(["实体  蛇", "数量  血量-上限  x", "      F  y"], "")
    assert sum("名称不合法" in e for e in errors) == 2
    tree = F.parse("¬结束(游戏) W -1 < 长度(蛇)", decls)
    assert F.parse(F.to_text(tree), decls) == tree


def test_next_step_pushes_negation(decls):
    """细则 8.6 下一步 不 (P 并且 Q) means the same as 下一步 不 P 或者 不 Q; a literal left side is next too."""
    a, _ = G.parse_sentence("下一步 不 (蛇 移动 并且 蛇的长度 等于 3)", decls)
    b, _ = G.parse_sentence("下一步 蛇 不移动 或者 蛇的长度 不等于 3", decls)
    assert a == b
    c, _ = G.parse_sentence("下一步 3 小于 蛇的长度", decls)
    assert c == F.node("X", F.node("lt", F.lit(3), F.term("长度", F.const("蛇"))))


def test_checklist_ids_exist():
    """细则 7.2 9.2 ids listed in the checklist must be propositions of the answer."""
    text = Path(__file__).resolve().parents[1].joinpath("examples/snake/subtask.txt").read_text(encoding="utf-8")
    parsed = T.parse_subtask(text.replace("终态            R7", "终态            R7、R99"), "实现贪吃蛇。")
    assert any("R99" in e for e in parsed["errors"])


# ---------------------------------------------------------------------------------------------------
# Rewriting the propositions (细则 9.3)
# ---------------------------------------------------------------------------------------------------

SNAKE = Path(__file__).resolve().parents[1] / "examples" / "snake"


def _two_rounds(home, tmp_path, first: str, second: str, decl: str = ""):
    state, _ = goal_run(home, tmp_path)
    base = (SNAKE / "subtask.txt").read_text(encoding="utf-8")
    state, revise = FLOW.propose_check(state, base + "\n\n" + first, 1)
    assert revise
    revised = base.replace("数量  长度(蛇)", decl + "数量  长度(蛇)") if decl else base
    state, _ = FLOW.propose_check(state, revised + "\n\n" + second, 2)
    data = S.read_json(Path(state["run"]) / "propositions.json")
    return state, {p["id"]: p for p in data["propositions"]}, data


def test_rewrite_checks_new_declarations(home, tmp_path):
    """细则 9.3 9.2 a declaration added in the rewrite is checked like the first ones."""
    first = "编号  R11\n来源  推理\n依据  〔结构〕x\n句子  代码中存在 函数 `推进一格`"
    state, props, data = _two_rounds(home, tmp_path, first, first, decl="接口  推进一格  一格\n")
    assert "推进一格" not in data["decls"]["category"]
    assert props["R11"]["problems"]
    assert any("推进一格" in n for n in state.get("notes", []))


def test_rewrite_keeps_every_proposition(home, tmp_path):
    """细则 9.3 duplicate ids and unnumbered blocks are not lost when the rewrite renumbers them."""
    first = (
        "编号  R11\n来源  推理\n依据  〔不变式〕x\n句子  总是 蛇的长度 不小于 1\n\n"
        "编号  R11\n来源  推理\n依据  〔不变式〕x\n句子  总是 蛇的长度 不小于 2\n\n"
        "号码  R13\n来源  推理\n依据  〔不变式〕x\n句子  总是 蛇的长度 不小于 3"
    )
    second = (
        "编号  R11\n来源  推理\n依据  〔不变式〕x\n句子  总是 蛇的长度 不小于 1\n\n"
        "编号  R12\n来源  推理\n依据  〔不变式〕x\n句子  总是 蛇的长度 不小于 2\n\n"
        "编号  R13\n来源  推理\n依据  〔不变式〕x\n句子  总是 蛇的长度 不小于 3"
    )
    _, props, _ = _two_rounds(home, tmp_path, first, second)
    assert {"R11", "R12", "R13"} <= set(props) and "" not in props
    assert sorted(p["sentence"][-1] for k, p in props.items() if k in ("R11", "R12", "R13")) == ["1", "2", "3"]


# ---------------------------------------------------------------------------------------------------
# Judging (细则 10.3, 12, 13)
# ---------------------------------------------------------------------------------------------------


def _sat(f, w, i):
    """A reference LTLf evaluator with weak X, written independently of the monitor."""
    n, op = len(w), f["op"]

    def val(t, j):
        if t["op"] == "lit":
            return t["value"]
        if t["op"] == "add":
            return val(t["args"][0], j) + val(t["args"][1], j)
        return w[j]["值"][t["name"]]

    if op == "pred":
        return w[i]["值"][f["name"]]
    if op in F.COMPARISONS:
        cmp = {"eq": int.__eq__, "lt": int.__lt__, "gt": int.__gt__, "ne": int.__ne__}[op]
        left, right = f["args"]
        if left["op"] == "next":
            return True if i + 1 >= n else cmp(val(left["args"][0], i + 1), val(right, i))
        return cmp(val(left, i), val(right, i))
    if op == "not":
        return not _sat(f["args"][0], w, i)
    if op == "and":
        return all(_sat(a, w, i) for a in f["args"])
    if op == "or":
        return any(_sat(a, w, i) for a in f["args"])
    if op == "X":
        return True if i + 1 >= n else _sat(f["args"][0], w, i + 1)
    if op == "G":
        return all(_sat(f["args"][0], w, j) for j in range(i, n))
    if op == "F":
        return any(_sat(f["args"][0], w, j) for j in range(i, n))
    a, b = f["args"]
    for j in range(i, n):
        if _sat(b, w, j):
            return True
        if not _sat(a, w, j):
            return False
    return op == "W"


def _formula(rng, d):
    if d == 0 or rng.random() < 0.3:
        c = rng.random()
        if c < 0.5:
            return F.pred(rng.choice("pq"))
        if c < 0.75:
            return F.node(
                rng.choice(["eq", "gt", "ne"]), F.node("next", F.term("n")), F.node("add", F.term("n"), F.lit(1))
            )
        return F.node(rng.choice(["eq", "lt"]), F.term("n"), F.lit(rng.choice([0, 1, 2])))
    k = rng.choice(["not", "and", "or", "X", "G", "F", "U", "W"])
    if k in ("not", "X", "G", "F"):
        return F.node(k, _formula(rng, d - 1))
    return F.node(k, _formula(rng, d - 1), _formula(rng, d - 1))


def test_progression_matches_reference():
    """细则 13.1 13.2 presumptive results are the finite semantics; definite ones hold for every finite
    continuation, including stopping right there."""
    rng = random.Random(7)
    states = [
        {"值": {"p": p, "q": q, "n": n}} for p, q, n in itertools.product([False, True], [False, True], [0, 1, 2])
    ]
    for _ in range(3000):
        f = _formula(rng, rng.randint(1, 5))
        if F.classify(f) != "时序":
            continue
        w = [rng.choice(states) for _ in range(rng.randint(1, 5))]
        res = M.judge(f, w, set())
        r, k = res["result"], res["state"]
        if r in (M.PRESUMED_TRUE, M.PRESUMED_FALSE):
            assert (r == M.PRESUMED_TRUE) == _sat(f, w, 0), F.to_text(f)
            continue
        want = r == M.SATISFIED
        for _ in range(10):
            ext = w[: k + 1] + [rng.choice(states) for _ in range(rng.randint(0, 3))]
            assert _sat(f, ext, 0) == want, (F.to_text(f), r)


def test_no_contradiction_is_true(decls):
    """细则 10.3 12.3 a primed comparison on an absent individual: φ ∧ ¬φ is never true."""
    f = F.parse("长度(「s2」)′ = 长度(「s2」) ∧ ¬(长度(「s2」)′ = 长度(「s2」))", decls)
    one = [{"实体": {"蛇": ["s1"]}, "值": {"长度(s1)": 1}}]
    assert M.judge(f, one, ENT)["result"] == M.PRESUMED_FALSE
    assert M.judge(f, one * 2, ENT)["result"] == M.VIOLATED


def test_big_integers_exact(decls):
    """细则 12.3 integers compare exactly."""
    tree = F.parse("G(长度(蛇)′ = 长度(蛇) + 1)", decls)
    assert M.judge(tree, [st(2**53), st(2**53)], ENT)["result"] == M.VIOLATED


def test_listed_entities(decls):
    """细则 12.4 an individual named like its entity is not always present once the entity is listed."""
    tree = F.parse("∀x:蛇 X 移动(x)", decls)
    trace = [{"实体": {"蛇": ["蛇"]}, "值": {"移动(蛇)": True}}, {"实体": {}, "值": {}}]
    assert M.judge(tree, trace, ENT)["result"] == M.VIOLATED
    with pytest.raises(M.AlignError):
        M.judge(tree, [{"实体": {"蛇": [{"id": 1}]}, "值": {}}], ENT)


def test_liveness_mark(decls):
    """细则 13.5 a pure safety property under ∀ is not marked as liveness; ∃! is checked both ways."""
    assert not M.is_liveness(F.parse("∀x:食物 G ¬位于蛇身(x)", decls))
    assert M.is_liveness(F.parse("G F 结束(游戏)", decls))


def test_missing_key_after_violation(decls):
    """细则 12.3 13.3 a key missing after the result was decided does not hide the violation."""
    tree, _ = G.parse_sentence("总是 蛇的长度 不小于 4", decls)
    trace = [st(3), {"值": {}}]
    assert M.judge(tree, trace, ENT)["result"] == M.VIOLATED


def test_primes_anywhere(decls):
    """细则 10.1 10.3 ′ may stand on any term of a comparison."""
    tree = F.parse("G(长度(蛇) + 1 = 长度(蛇)′)", decls)
    assert M.judge(tree, [st(3), st(4), st(5)], ENT)["result"] == M.PRESUMED_TRUE
    assert M.judge(tree, [st(3), st(3)], ENT)["result"] == M.VIOLATED


# ---------------------------------------------------------------------------------------------------
# Running checks (细则 5.5, 11.8, 12.6, 13.3, 13.4)
# ---------------------------------------------------------------------------------------------------


def _run(tmp_path, decls, checks, limits=None):
    run, w = tmp_path / "run", tmp_path / "w"
    (run / "align").mkdir(parents=True, exist_ok=True)
    w.mkdir(exist_ok=True)
    p = props(decls)
    return RUN.run_checks(run, w, p, frozen_for(checks), ENT, {**LIMITS, **(limits or {})})


def test_no_process_survives(tmp_path, decls):
    """细则 5.5 a background process left by a check is killed when the check ends."""
    marker = tmp_path / "late"
    out = _run(
        tmp_path,
        decls,
        [{"proposition": "R9", "kind": "command", "command": f'(sleep 1; echo x > "{marker}") & exit 0'}],
    )
    assert out["propositions"]["R9"]["result"] == M.PASSED
    time.sleep(1.5)
    assert not marker.exists()


@pytest.mark.skipif(not os.path.exists("/proc/self/stat"), reason="subreaper needs Linux")
def test_setsid_does_not_escape(tmp_path, decls):
    """细则 5.5 a process that left the process group does not hold the checker past the timeout."""
    start = time.monotonic()
    out = _run(
        tmp_path,
        decls,
        [{"proposition": "R9", "kind": "command", "command": "setsid sleep 30 & sleep 30", "timeout": 1}],
    )
    assert time.monotonic() - start < 8 and out["propositions"]["R9"]["result"] == M.ALIGN_FAILED


def test_trace_must_be_a_regular_file(tmp_path, decls):
    """细则 12.6 a FIFO or a link to a device as the trace is an alignment failure, not a hang."""
    for k, cmd in enumerate(('mkfifo "$CV_TRACE"', 'ln -s /dev/zero "$CV_TRACE"')):
        (tmp_path / str(k)).mkdir()
        out = _run(tmp_path / str(k), decls, [{"proposition": "R5", "kind": "trace", "command": cmd, "timeout": 5}])
        assert out["propositions"]["R5"]["result"] == M.ALIGN_FAILED


def test_partial_line_after_timeout(tmp_path, decls):
    """细则 12.6 the half-written last line of a run that timed out is dropped; the prefix is judged."""
    states = [
        {"值": {"撞到自身(蛇)": True, "结束(游戏)": False}},
        {"值": {"撞到自身(蛇)": False, "结束(游戏)": False}},
    ]
    script = tmp_path / "writer.py"
    script.write_text(
        "import os, time\n"
        f"lines = {[json.dumps(s, ensure_ascii=False) for s in states]!r}\n"
        "f = open(os.environ['CV_TRACE'], 'wb')\n"
        "f.write(('\\n'.join(lines) + '\\n').encode() + '{\"值\": {\"撞'.encode()[:-1])\n"
        "f.flush()\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    cmd = f'{sys.executable} "{script}"'
    out = _run(tmp_path, decls, [{"proposition": "R5", "kind": "trace", "command": cmd, "timeout": 1}])
    assert out["propositions"]["R5"]["result"] == M.VIOLATED


def test_budget_cut_does_not_pass(tmp_path, decls):
    """细则 11.8 a trace check cut short by the budget is at best an alignment failure."""
    good = json.dumps({"值": {"撞到自身(蛇)": False, "结束(游戏)": False}}, ensure_ascii=False)
    cmd = f"echo '{good}' > \"$CV_TRACE\"; sleep 0.6"
    out = _run(tmp_path, decls, [{"proposition": "R5", "kind": "trace", "command": cmd, "runs": 50}], {"budget": 1.0})
    assert out["propositions"]["R5"]["result"] == M.ALIGN_FAILED


def test_crash_counterexample_kept(tmp_path, decls):
    """细则 13.4 the counterexample of a crash is not replaced by a later, better one."""
    cmd = '[ "$CV_RUN" = 0 ] && { echo boom >&2; exit 2; }; echo \'{"值": {"撞到自身(蛇)": false, "结束(游戏)": false}}\' > "$CV_TRACE"'
    out = _run(tmp_path, decls, [{"proposition": "R5", "kind": "trace", "command": cmd, "runs": 2}])
    r = out["propositions"]["R5"]
    assert r["result"] == M.CHECK_FAILED and r["counterexample"]["exit"] == 2


def test_coverage_failure_does_not_hide_a_failure(tmp_path, decls):
    """细则 13.3 a missing trace check does not replace what a command check found."""
    run, w = tmp_path / "run", tmp_path / "w"
    (run / "align").mkdir(parents=True)
    w.mkdir()
    frozen = frozen_for([{"proposition": "R5", "kind": "command", "command": "echo bad; exit 1"}])
    frozen["align_failures"] = {"R5": "时序命题缺少 trace 检查（细则 11.5）"}
    out = RUN.run_checks(run, w, props(decls), frozen, ENT, LIMITS)
    assert out["propositions"]["R5"]["result"] == M.CHECK_FAILED


# ---------------------------------------------------------------------------------------------------
# Second review (2026-10-30)
# ---------------------------------------------------------------------------------------------------


def _aligned_by(state, src, during=None):
    FLOW.align_prompt(state)
    run = Path(state["run"])
    for f in src.iterdir():
        shutil.copy2(f, run / "align" / f.name)
    if during:
        during(run)
    state, _ = FLOW.align_check(state, "已完成", 1, FLOW.load_config())
    return state


def test_w_before_frozen_before_alignment(home, tmp_path, light_align):
    """细则 5.2 5.4 the alignment agent cannot fix W and re-baseline w-before.json."""
    state, w = goal_run(home, tmp_path, "buggy")

    def tamper(run):
        shutil.copy2(SNAKE / "correct" / "snake.py", w / "snake.py")
        S.write_json(run / "w-before.json", S.tree_digests(w))

    state = _aligned_by(state, light_align, tamper)
    config = FLOW.load_config()
    assert "**无效**" in FLOW.report(FLOW.run_checks(state, config), config)


def test_planted_bytecode_is_not_read(home, tmp_path, light_align):
    """细则 5.5 a .pyc planted in W's __pycache__ during alignment has no effect on the checks."""
    import importlib.util
    import py_compile

    state, w = goal_run(home, tmp_path, "buggy")

    def plant(run):
        good = tmp_path / "good" / "snake.py"
        good.parent.mkdir()
        shutil.copy2(SNAKE / "correct" / "snake.py", good)
        target = w / "__pycache__" / Path(importlib.util.cache_from_source(str(w / "snake.py"))).name
        target.parent.mkdir(exist_ok=True)
        py_compile.compile(
            str(good), cfile=str(target), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH
        )

    state = _aligned_by(state, light_align, plant)
    config = FLOW.load_config()
    assert "**比对未通过**" in FLOW.report(FLOW.run_checks(state, config), config)


def test_no_links_in_align(home, tmp_path, light_align):
    """细则 11.2 5.2 a link in align/ is a format error: its target could change after freezing."""
    state, _ = goal_run(home, tmp_path)
    FLOW.align_prompt(state)
    run = Path(state["run"])
    for f in light_align.iterdir():
        shutil.copy2(f, run / "align" / f.name)
    os.symlink(tmp_path, run / "align" / "lib")
    state, fix = FLOW.align_check(state, "已完成", 1, FLOW.load_config())
    assert fix and "符号链接" in fix


def test_directory_links_are_digested(tmp_path):
    """细则 5.2 a link to a directory is part of a folder's digest."""
    (tmp_path / "a").mkdir()
    (tmp_path / "x").mkdir()
    before = S.folder_digest(tmp_path / "a")
    os.symlink(tmp_path / "x", tmp_path / "a" / "json")
    assert S.folder_digest(tmp_path / "a") != before


def test_report_text_cannot_forge_conclusion():
    """细则 14.1 16.6 program output quoted in the report stays on one line without markup."""
    import cv_report as RP

    detail = RP._detail({"counterexample": {"exit": 3, "stderr": "boom\n## 比对结论\n**比对通过**：x"}})
    assert "\n" not in detail and "##" not in detail and "**" not in detail


def test_unmatched_quote_hides_no_name():
    """细则 9.1 第 4 项 an unmatched 「 does not hide the rest of the text from the name check."""
    assert "snake_len" in C._ascii_names("蛇的长度总是大于零「 G snake_len > 0")


def test_violation_without_cwd_counts(home, tmp_path):
    """细则 5.3 a global record without cwd cannot be placed, so it counts (fail closed), and does not crash."""
    state, _ = goal_run(home, tmp_path)
    (S.cv_home() / "tool-violations.jsonl").write_text(
        json.dumps({"tool": "sleep", "cwd": None, "time": "2999-01-01T00:00:00+00:00"}) + "\n", encoding="utf-8"
    )
    assert [v["tool"] for v in FLOW.violations(state)] == ["sleep"]


def test_judging_is_bounded(decls):
    """细则 11.8 judging a degenerate formula stops at the deadline instead of stalling the checker."""
    tree = F.parse("G " * 60 + "移动(蛇)", decls)
    with pytest.raises(M.AlignError, match="超时"):
        M.judge(tree, [st()] * 200, ENT, deadline=time.monotonic() + 0.2)


def test_nan_and_hidden_missing_keys(tmp_path, decls):
    """细则 12.3 12.6 NaN is not JSON; a quantified fact evaluates every operand, so a missing key is found."""
    path = tmp_path / "t.jsonl"
    path.write_text('{"值": {"长度(蛇)": NaN}}\n', encoding="utf-8")
    with pytest.raises(M.AlignError):
        RUN.read_trace(path, 10)
    tree = F.parse("∀x:食物 (位于蛇身(x) ∨ 新生成(x))", decls)
    state = {"实体": {"食物": ["f1"]}, "值": {"位于蛇身(f1)": True}}
    with pytest.raises(M.AlignError):
        M.judge(tree, [state], ENT)


def test_primed_atoms_and_formula_gaps(decls):
    """细则 10.1 10.3 9.1 第 3 项 p(a)′ is X p(a); ′′, 表示(3) and predicates under ′ are type errors."""
    tree = F.parse("G(吃到(蛇, 食物)′ → 结束(游戏))", decls)
    assert F.parse(F.to_text(tree), decls) == tree and F.type_errors(tree, decls) == []
    for text in ("长度(蛇)′′ = 1", "表示(3)", "只由(`move`, 动作)", "吃到(蛇, 食物)′ = 1"):
        assert F.type_errors(F.parse(text, decls), decls), text
    q = F.parse("∃x:食物 -1 < 长度(蛇)", decls)
    assert F.parse(F.to_text(q), decls) == q


def test_words_are_not_numbers():
    """细则 9.1 第 5 项 一直, 一旦, 千万, 十分 are words, not numbers."""
    assert C.quote_numbers("蛇一直向前，一旦撞墙千万不要十分慌张") == []


def test_renumbered_duplicate_keeps_the_other(home, tmp_path):
    """细则 9.3 two R1s, the rewrite renumbers the second to R2: the first R1 is kept as it was."""
    first = (
        "编号  R1\n来源  推理\n依据  〔动作的效果〕x\n句子  只要 蛇 吃到 食物，下一步 蛇的长度 等于 蛇的长度 加 1\n\n"
        "编号  R1\n来源  推理\n依据  〔终态〕y\n句子  一旦 游戏 结束，此后一直 游戏 结束"
    )
    second = "编号  R2\n来源  推理\n依据  〔终态〕y\n句子  一旦 游戏 结束，此后一直 游戏 结束"
    state, _ = goal_run(home, tmp_path)
    decls = (SNAKE / "subtask.txt").read_text(encoding="utf-8").split("\n清单\n")[0]
    items = ("存在", "状态", "不变式", "动作的效果", "事件的后果", "边界", "终态", "最终会发生的事", "结构")

    def checklist(listed):
        return "\n".join(f"{i}  {listed.get(i, '无：例子')}" for i in items)

    one = decls + "\n清单\n" + checklist({"动作的效果": "R1", "终态": "R1"}) + "\n\n命题\n" + first
    state, revise = FLOW.propose_check(state, one, 1)
    assert revise
    two = decls + "\n清单\n" + checklist({"终态": "R2"}) + "\n\n命题\n" + second
    state, _ = FLOW.propose_check(state, two, 2)
    props = S.read_json(Path(state["run"]) / "propositions.json")["propositions"]
    assert sorted((p["id"], p["problems"]) for p in props) == [("R1", []), ("R2", [])]


# ---------------------------------------------------------------------------------------------------
# The first full live run and the mutants replayed on it (2026-11-02)
# ---------------------------------------------------------------------------------------------------

STACK = "实体  栈、值\n谓词  弹出(栈, 值)  这一步弹出了该值\n      位于栈中(栈, 值)  该值处于在栈中的状态\n"
STACK += "位置  栈顶(栈)  最后压入的值\n数量  深度(栈)  元素个数\n      总数  容量\n动作  压入1、弹出一次\n接口  `Stack`、push\n\n"
STACK_R = "类 Stack 提供 push。"


def stack_checked(blocks, sentences=None, original=STACK_R):
    ids = "、".join(b.split()[1] for b in blocks if b.split()[3] == "推理")
    items = [f"{name}  无：无" for name in T.CHECKLIST if name != "不变式"]
    text = f"声明\n{STACK}清单\n不变式  {ids or '无：无'}\n" + "\n".join(items) + "\n\n命题\n" + "\n\n".join(blocks)
    parsed = T.parse_subtask(text, original)
    assert parsed["errors"] == []
    return {p["id"]: p for p in C.check_propositions(parsed, sentences or [], original)}


def reasoned(pid, sentence, formula=None):
    block = f"编号  {pid}\n来源  推理\n依据  〔不变式〕理由\n句子  {sentence}"
    return block + (f"\n公式  未套用句式：{formula}" if formula else "")


def test_state_dependent_needs_a_time():
    """细则 17.10 9.1 第 8 项 a formula judged only on the first state passed on wrong code (R20 of the live run)."""
    out = stack_checked(
        [
            reasoned("R1", "对所有 值 v，如果 弹出(栈, v) 那么 栈顶(栈) 等于 v"),
            reasoned("R2", "深度(栈) 等于 3"),
            reasoned("R3", "有值在栈里时深度大于 0", "(∃x:值 位于栈中(栈, x)) → 深度(栈) > 0"),
            reasoned("R4", "每次弹出的都是栈顶", "∀v:值 G(弹出(栈, v) → 栈顶(栈) = v)"),
            reasoned("R5", "总是 深度(栈) 不小于 0"),
            reasoned("R6", "初始时 深度(栈) 等于 0"),
            reasoned("R7", "存在一个 x，使 x 是 栈"),
            reasoned("R8", "总数 等于 100"),
            reasoned("R9", "深度(「a」) 等于 3"),
        ]
    )
    for pid in ("R1", "R2", "R3"):
        assert any("细则 9.1 第 8 项" in x for x in out[pid]["problems"]), pid
    for pid in ("R4", "R5", "R6", "R7", "R8", "R9"):
        assert out[pid]["problems"] == [], pid


def test_translation_of_a_rule_and_an_example():
    """细则 17.9 9.1 第 5 项 the three direct translations the live run lost; 从不 and Q stay strict."""
    original = "空栈调用 pop 抛出错误。不需要界面。依次操作后，深度等于 1。类 Stack 提供 push。"
    sentences = [
        {
            "id": "S1",
            "text": "空栈调用 pop 抛出错误。",
            "labels": ["规则", "条件"],
            "quotable": ["空栈调用 pop 抛出错误。"],
        },
        {"id": "S2", "text": "不需要界面。", "labels": ["规则"], "quotable": ["不需要界面。"]},
        {"id": "S3", "text": "依次操作后，深度等于 1。", "labels": ["示例"], "quotable": ["依次操作后，深度等于 1。"]},
    ]
    start = "从 深度(栈) 等于 0 出发，依次执行 压入1、弹出一次，之后 深度(栈) 等于"
    block = "编号  {}\n来源  翻译\n依据  「{}」\n句子  {}"
    out = stack_checked(
        [
            block.format("R1", "空栈调用 pop 抛出错误", "只要 深度(栈) 等于 0，就 深度(栈) 不大于 0"),
            block.format("R2", "不需要界面", "从不 深度(栈) 小于 0"),
            block.format("R3", "依次操作后，深度等于 1", f"{start} 1"),
            block.format("R4", "依次操作后，深度等于 1", f"{start} 2"),
        ],
        sentences,
        original,
    )
    assert out["R1"]["problems"] == [] and out["R3"]["problems"] == []
    assert any("句式 从不" in x for x in out["R2"]["problems"])
    assert any("数 2" in x for x in out["R4"]["problems"])


def test_interfaces_on_one_line():
    """细则 7.1 the live run wrote 接口 as a list without meanings and every name was refused."""
    decls, errors = T.parse_declarations(["接口  Stack、`push`、size"], "类 Stack 提供 push 和 size")
    assert errors == [] and decls.of("接口") == {"Stack", "push", "size"}
    assert any("pop" in e for e in T.parse_declarations(["接口  Stack、pop"], "类 Stack")[1])


def _subtask(checklist_ids, blocks):
    items = [f"{name}  无：无" for name in T.CHECKLIST if name != "不变式"]
    return f"声明\n实体  栈\n\n清单\n不变式  {checklist_ids}\n" + "\n".join(items) + "\n\n命题\n" + blocks


def test_interrupted_output_is_named():
    """细则 7.2 a cut-off answer is reported as such, not as ids that do not exist."""
    whole = "编号  R1\n来源  推理\n依据  〔不变式〕理由\n句子  存在一个 x，使 x 是 栈"
    cut_inside = T.parse_subtask(_subtask("R1、R2、R3", whole + "\n\n编号  R2\n来源  推理\n依据  〔不变式〕"), "x")
    assert "中断" in cut_inside["errors"][0] and "max_output_tokens" in cut_inside["errors"][0]
    cut_between = T.parse_subtask(_subtask("R1、R2", whole), "x")
    assert "中断" in cut_between["errors"][0]
    no_section = T.parse_subtask("声明\n实体  栈\n\n清单\n", "x")
    assert "中断" in no_section["errors"][0]
    # A wrong id below the highest one written is an ordinary mistake.
    wrong = whole + "\n\n编号  R5\n来源  推理\n依据  〔不变式〕理由\n句子  存在一个 x，使 x 是 栈"
    assert not any("中断" in e for e in T.parse_subtask(_subtask("R1、R2、R5", wrong), "x")["errors"])
    assert not any("中断" in e for e in T.parse_subtask(_subtask("R1", whole), "x")["errors"])


def test_antecedent_never_fired(decls, tmp_path):
    """细则 13.7 a pass whose antecedent never held is named in the results; the result does not change."""
    tree, pattern = G.parse_sentence("只要 蛇的长度 大于 5，就 蛇的长度 大于 4", decls)
    p = {"R1": {"id": "R1", "tree": tree, "pattern": pattern, "class": F.classify(tree)}}
    state = {"值": {"长度(蛇)": 3}}
    assert M.triggered(tree, [state], ENT) is False
    assert M.triggered(tree, [state, {"值": {"长度(蛇)": 6}}], ENT) is True
    assert M.triggered(G.parse_sentence("总是 蛇的长度 大于 1", decls)[0], [state], ENT) is None
    quantified = F.parse("∀x:食物 G(新生成(x) → 长度(蛇) > 0)", decls)
    food = {"实体": {"蛇": ["蛇"], "食物": ["f1"]}, "值": {"新生成(f1)": False, "长度(蛇)": 1}}
    assert M.triggered(quantified, [food], {"蛇", "食物"}) is False
    food2 = {"实体": food["实体"], "值": {"新生成(f1)": True, "长度(蛇)": 1}}
    assert M.triggered(quantified, [food, food2], {"蛇", "食物"}) is True

    run = tmp_path / "run"
    (run / "align").mkdir(parents=True)
    w = tmp_path / "w"
    w.mkdir()
    script = run / "align" / "t.py"
    script.write_text("import json, os\nopen(os.environ['CV_TRACE'], 'w').write(json.dumps({'值': {'长度(蛇)': 3}}))\n")
    check = {
        "proposition": "R1",
        "form": "t",
        "kind": "trace",
        "command": f"{sys.executable} {script}",
        "runs": 2,
        "timeout": 30,
    }
    out = RUN.run_checks(run, w, p, frozen_for([check]), ENT, LIMITS)["propositions"]["R1"]
    assert out["vacuous"] is True and RUN.VACUOUS in out["notes"] and out["result"] == M.PRESUMED_TRUE
    assert "vacuous" not in RUN.run_checks(run, w, {}, frozen_for([]), ENT, LIMITS)["propositions"]


def test_alignment_prompt_asks_for_strict_checks_that_reach_the_hard_cases():
    """细则 11.10 the mutants that escaped: a renamed method, a depth of 6, a duplicate value."""
    text = (Path(FLOW.__file__).parent / "prompts" / "align.md").read_text(encoding="utf-8")
    for phrase in ("严格按声明的接口名检查", "不要换别的名字代替", "明显超过示例", "允许重复的值", "前件都要真的发生"):
        assert phrase in text


def test_only_individual_may_be_keyed_by_entity_name(decls):
    """细则 12.4 a live run listed 栈 as ["S"] and wrote 栈顶(栈): correct code failed on every temporal check."""
    tree, _ = G.parse_sentence("总是 蛇的长度 不小于 1", decls)
    listed = {"蛇": ["S"], "食物": [], "游戏": []}
    by_name = [{"实体": listed, "值": {"长度(蛇)": 3}}]
    by_id = [{"实体": listed, "值": {"长度(S)": 3}}]
    assert M.judge(tree, by_name, ENT)["result"] == M.PRESUMED_TRUE
    assert M.judge(tree, by_id, ENT)["result"] == M.PRESUMED_TRUE
    # The individual's own key wins, and a wrong value under the name is still found.
    both = [{"实体": listed, "值": {"长度(S)": 3, "长度(蛇)": 0}}]
    assert M.judge(tree, both, ENT)["result"] == M.PRESUMED_TRUE
    assert M.judge(tree, [{"实体": listed, "值": {"长度(蛇)": 0}}], ENT)["result"] == M.VIOLATED
    # Still a missing key when neither form is there, or when the entity has several individuals.
    with pytest.raises(M.AlignError, match="缺少"):
        M.judge(tree, [{"实体": listed, "值": {}}], ENT)
    quantified = F.parse("∀x:蛇 G(长度(x) ≥ 1)", decls)
    assert M.judge(quantified, by_name, ENT)["result"] == M.PRESUMED_TRUE
    with pytest.raises(M.AlignError):
        M.judge(quantified, [{"实体": {"蛇": ["a", "b"]}, "值": {"长度(蛇)": 3}}], ENT)


def _trace_script(tmp_path, name, states, exit_code=0):
    path = tmp_path / name
    body = "\n".join(json.dumps(s, ensure_ascii=False) for s in states)
    path.write_text(
        f"import os, sys\nopen(os.environ['CV_TRACE'], 'w', encoding='utf-8').write({body!r})\nsys.exit({exit_code})\n"
    )
    return f"{sys.executable} {path}"


def test_preflight_reports_the_format_and_nothing_else(decls, tmp_path):
    """细则 11.7 a key mistake goes back before freezing; a verdict, a crash or a timeout never does."""
    tree, pattern = G.parse_sentence("总是 蛇的长度 大于 1", decls)
    p = {"R1": {"id": "R1", "tree": tree, "pattern": pattern, "class": F.classify(tree)}}

    def problems(command, timeout=30):
        check = {"proposition": "R1", "kind": "trace", "command": command, "timeout": timeout, "runs": 1}
        return RUN.preflight(tmp_path, tmp_path, p, [check], ENT, LIMITS)

    assert any("缺少 长度(蛇)" in x for x in problems(_trace_script(tmp_path, "a.py", [{"值": {"宽度(蛇)": 3}}])))
    assert problems(_trace_script(tmp_path, "b.py", [{"值": {"长度(蛇)": 3}}])) == []
    # The code breaks the proposition: that is a verdict and must not reach the agent.
    assert problems(_trace_script(tmp_path, "c.py", [{"值": {"长度(蛇)": 0}}])) == []
    # A script that exits non-zero may have found a defect: not said either, even with a bad trace.
    assert problems(_trace_script(tmp_path, "d.py", [{"值": {"宽度(蛇)": 3}}], 2)) == []
    assert problems(f"{sys.executable} -c 'import time; time.sleep(9)'", timeout=1) == []
    assert any("轨迹" in x for x in problems(f"{sys.executable} -c 'pass'"))
    assert list(tmp_path.glob("*.jsonl")) == []


def test_preflight_goes_back_once(home, tmp_path, light_align, monkeypatch):
    """细则 11.7 trace problems take the format-correction path once; trouble in the trial is skipped."""
    state, _ = goal_run(home, tmp_path)
    FLOW.align_prompt(state)
    for f in light_align.iterdir():
        shutil.copy2(f, Path(state["run"]) / "align" / f.name)
    calls = []
    monkeypatch.setattr(RUN, "preflight", lambda *a: calls.append(a) or ["R5（x）：状态 0 缺少 长度(蛇)"])
    state, again = FLOW.align_check(state, "已完成", 1, FLOW.load_config())
    assert "缺少 长度(蛇)" in again and "脚本写出的轨迹" in again
    state, again = FLOW.align_check(state, "已修正", 2, FLOW.load_config())
    assert (
        again is None
        and len(calls) == 1
        and "align_failures" in S.read_json(Path(state["run"]) / "align" / "checks.frozen.json")
    )
    assert S.read_json(Path(state["run"]) / "align" / "checks.frozen.json")["checks"]


def test_preflight_error_is_skipped(home, tmp_path, light_align, monkeypatch):
    """细则 4.8 11.7 an error in the trial run neither stops the run nor sends the agent back."""
    state, _ = goal_run(home, tmp_path)
    FLOW.align_prompt(state)
    for f in light_align.iterdir():
        shutil.copy2(f, Path(state["run"]) / "align" / f.name)

    def broken(*_):
        raise OSError("x")

    monkeypatch.setattr(RUN, "preflight", broken)
    state, again = FLOW.align_check(state, "已完成", 1, FLOW.load_config())
    assert again is None and any("试跑出错" in n for n in state["notes"])


def test_exception_name_survives_translation():
    """细则 9.1 第 5 项 抛出 IndexError abstracted to 报错 let code raising another exception pass."""
    original = "空栈时抛出 IndexError。类 Stack 提供 push。"
    sentences = [
        {"id": "S1", "text": "空栈时抛出 IndexError。", "labels": ["规则"], "quotable": ["空栈时抛出 IndexError。"]}
    ]
    block = "编号  {}\n来源  翻译\n依据  「空栈时抛出 IndexError」\n句子  {}"
    extra = (
        "谓词  报错(栈)  这一步调用失败\n      抛错(栈)  这一步抛出 IndexError\n      抛出IndexError(栈)  这一步抛错\n"
    )
    out = stack_checked_with(
        extra,
        [
            block.format("R1", "只要 深度(栈) 等于 0，就 栈 报错"),
            block.format("R2", "只要 深度(栈) 等于 0，就 栈 抛错"),
            block.format("R3", "只要 深度(栈) 等于 0，就 栈 抛出IndexError"),
        ],
        sentences,
        original,
    )
    assert any("异常名 IndexError" in x for x in out["R1"]["problems"])
    assert out["R2"]["problems"] == [] and out["R3"]["problems"] == []
    assert C.lost_exceptions("抛出 ValueError 或 KeyError", "ValueError", "", None, F.Decls()) == ["KeyError"]
    assert C.lost_exceptions("Errors are bad", "", "", None, F.Decls()) == []


def stack_checked_with(extra, blocks, sentences, original):
    items = [f"{name}  无：无" for name in T.CHECKLIST]
    head = f"声明\n{extra}{STACK}清单\n" + "\n".join(items) + "\n\n命题\n" + "\n\n".join(blocks)
    parsed = T.parse_subtask(head, original)
    assert parsed["errors"] == []
    return {p["id"]: p for p in C.check_propositions(parsed, sentences, original)}


def test_crash_report_shows_what_the_script_printed(decls, tmp_path):
    """细则 12.6 a trace script that aborts says why, on stdout or stderr."""
    tree, pattern = G.parse_sentence("总是 蛇的长度 大于 1", decls)
    p = {"R1": {"id": "R1", "tree": tree, "pattern": pattern, "class": F.classify(tree)}}
    (tmp_path / "w").mkdir()
    run = tmp_path / "run"
    (run / "align").mkdir(parents=True)
    command = f"{sys.executable} -c \"print('push did not add an item'); raise SystemExit(2)\""
    check = {"proposition": "R1", "form": "t", "kind": "trace", "command": command, "runs": 1, "timeout": 30}
    out = RUN.run_checks(run, tmp_path / "w", p, frozen_for([check]), ENT, LIMITS)["propositions"]["R1"]
    assert out["result"] == M.CHECK_FAILED
    import cv_report as RP

    detail = RP._detail(out)
    assert "退出码 2" in detail and "push did not add an item" in detail


def test_example_may_end_with_a_call():
    """细则 8.5 the natural translation of an example was refused, and with it the only direct test of size()."""
    original = "依次操作后，size() 等于 1。类 Stack 提供 push。空的 size。"
    sentences = [
        {
            "id": "S1",
            "text": "依次操作后，size() 等于 1。",
            "labels": ["示例"],
            "quotable": ["依次操作后，size() 等于 1。"],
        }
    ]
    extra = "接口  size\n"
    start = "从 深度(栈) 等于 0 出发，依次执行 压入1、弹出一次，之后"
    block = "编号  {}\n来源  翻译\n依据  「依次操作后，size() 等于 1」\n句子  {}"
    out = stack_checked_with(
        extra,
        [
            block.format("R1", f"{start} 调用 `size`() 得到 1"),
            block.format("R2", f"{start} 调用 `size`() 得到 2"),
            block.format("R3", f"{start} 调用 `nope`() 得到 1"),
            block.format("R4", f"{start} 调用 `size`() 失败"),
        ],
        sentences,
        original,
    )
    assert out["R1"]["problems"] == [] and out["R1"]["pattern"] == "从出发" and out["R1"]["class"] == "具体事实"
    assert "`size`() = 1" in out["R1"]["formula"] and "动作₁" in out["R1"]["formula"]
    assert any("数 2" in x for x in out["R2"]["problems"])
    assert any("nope" in x for x in out["R3"]["problems"])
    assert out["R4"]["problems"] == []
    # A trace cannot make the call, and the trial run says so before freezing.
    with pytest.raises(M.AlignError, match="command"):
        M.judge(
            out["R1"]["tree"],
            [
                {"值": {"深度(栈)": 0}, "动作": "压入1"},
                {"值": {"深度(栈)": 1}, "动作": "弹出一次"},
                {"值": {"深度(栈)": 0}},
            ],
            {"栈"},
        )


def test_entity_with_no_individual_now_is_false_not_an_error(decls):
    """细则 12.4 a live run listed no food once it was eaten; every proposition naming food failed to align."""
    tree, _ = G.parse_sentence("只要 蛇 吃到 食物，下一步 蛇的长度 等于 蛇的长度 加 1", decls)

    def state(food, values):
        return {"实体": {"蛇": ["蛇"], "食物": food, "游戏": ["游戏"]}, "值": values}

    eaten = [
        state(["f"], {"吃到(蛇,f)": False, "长度(蛇)": 2}),
        state([], {"长度(蛇)": 2}),
        state(["g"], {"吃到(蛇,g)": True, "长度(蛇)": 2}),
        state([], {"长度(蛇)": 3}),
    ]
    assert M.judge(tree, eaten, ENT)["result"] == M.PRESUMED_TRUE
    wrong = [*eaten[:3], state([], {"长度(蛇)": 2})]
    assert M.judge(tree, wrong, ENT)["result"] == M.VIOLATED
    # Several individuals are still a mistake when the entity is used as a constant.
    with pytest.raises(M.AlignError, match="恰有一个个体"):
        M.judge(tree, [state(["f", "g"], {"长度(蛇)": 2})], ENT)


# ---------------------------------------------------------------------------------------------------
# Correct snake code failed in all three rounds of the accumulation evaluation (2026-11-03)
# ---------------------------------------------------------------------------------------------------


def test_unstated_reading_is_marked():
    """细则 7.3 9.1 a reading the requirement does not decide is marked, with what it chose; only for reasoning."""

    def block(pid, source, basis):
        return f"编号  {pid}\n来源  {source}\n依据  {basis}\n句子  总是 深度(栈) 不小于 0"

    out = stack_checked(
        [
            block("R1", "推理", "〔不变式〕〔需求未说明〕需求没说空栈的深度，这里按 0 处理"),
            block("R2", "推理", "〔不变式〕〔需求未说明〕 "),
            block("R3", "翻译", "「类 Stack 提供 push」〔需求未说明〕"),
            block("R4", "推理", "〔不变式〕理由"),
        ]
    )
    assert out["R1"]["problems"] == [] and out["R1"]["unstated"] is True
    assert any("之后要写明" in x for x in out["R2"]["problems"])
    assert any("只有推理命题" in x for x in out["R3"]["problems"])
    assert out["R4"]["problems"] == [] and "unstated" not in out["R4"]


def test_flagged_checks_cover_nothing(decls):
    """细则 11.2 11.6 a check on inputs the requirement leaves open is extra; it never stands in for a check."""
    import cv_align as A

    data = {
        "bindings": {},
        "checks": [
            {"proposition": "R1", "kind": "command", "command": "x", "unstated_inputs": True},
            {"proposition": "R9", "kind": "command", "command": "x"},
            {"proposition": "R9", "kind": "command", "command": "x", "unstated_inputs": "yes"},
            {"proposition": "R5", "kind": "trace", "command": "x", "runs": 1},
            {"proposition": "R5", "kind": "trace", "command": "y", "runs": 1, "unstated_inputs": True},
        ],
    }
    out = A.validate(data, props(decls), LIMITS)
    assert any("unstated_inputs" in e for e in out["format_errors"])
    assert "unstated_inputs" in out["align_failures"]["R1"]
    assert "R9" not in out["align_failures"] and "R5" not in out["align_failures"]
    assert [c["unstated_inputs"] for c in out["checks"]] == [True, False, False, True]


def test_what_the_requirement_leaves_open_is_for_a_person(decls, tmp_path):
    """细则 13.8 14.2 17.11 a marked reading or an input the requirement leaves open is shown, not counted."""
    import cv_report as REPORT

    run, w = tmp_path / "run", tmp_path / "w"
    (run / "align").mkdir(parents=True)
    w.mkdir()
    p = props(decls)
    p["R1"] = {**p["R1"], "unstated": True}
    checks = [
        {"proposition": "R1", "kind": "command", "command": "echo into the tail cell; exit 1"},
        {"proposition": "R9", "kind": "command", "command": "exit 0"},
        {"proposition": "R9", "kind": "command", "command": "echo empty board; exit 1", "unstated_inputs": True},
        {"proposition": "R5", "kind": "command", "command": "exit 0"},
    ]
    out = RUN.run_checks(run, w, p, frozen_for(checks), ENT, LIMITS)["propositions"]
    assert out["R1"]["result"] == M.CHECK_FAILED and out["R1"]["unstated"] is True
    assert out["R9"]["result"] == M.PASSED and out["R9"]["unstated_inputs"][0]["result"] == M.CHECK_FAILED
    assert "unstated" not in out["R9"] and "unstated_inputs" not in out["R5"]
    data = {"results": out, "stats": {"submitted": 3, "discarded": 0}}
    assert REPORT.conclusion(data, 0.5)[0] == REPORT.PASSED

    accepted = [
        {"id": pid, "source": "推理", "basis": "〔存在〕〔需求未说明〕没说", "sentence": f"句{pid}", "formula": "f"}
        | ({"unstated": True} if pid == "R1" else {})
        for pid in ("R1", "R5", "R9")
    ]
    lines = REPORT.to_confirm(out, accepted)
    assert len(lines) == 2 and "into the tail cell" in lines[0] and "empty board" in lines[1]
    S.write_json(run / "propositions.json", {"propositions": [{**a, "problems": []} for a in accepted]})
    S.write_json(run / "results.json", {"propositions": out})
    text = REPORT.render(REPORT.build(run, {}, {"max_discard_ratio": 0.5}))
    assert "## 需要人确认" in text and "**比对通过**" in text and "需求未说明 1 条" in text
    assert "推理（需求未说明）" in text and "超出需求的输入的检查 1 项" in text

    # An ordinary failure still decides.
    out["R5"]["result"] = M.VIOLATED
    assert REPORT.conclusion(data, 0.5)[0] == REPORT.FAILED


def test_prompts_ask_for_reachable_states_and_meaning_not_names():
    """细则 11.10 7.3 a script searched the code for 撞墙, put food on the snake and built boards of 0 cells."""
    prompts = Path(FLOW.__file__).parent / "prompts"
    align = (prompts / "align.md").read_text(encoding="utf-8")
    for phrase in ("看含义，不看名字", "只走正常使用能到达的状态", '"unstated_inputs": true', "不要直接改内部字段"):
        assert phrase in align
    assert "〔需求未说明〕" in (prompts / "propose.md").read_text(encoding="utf-8")


def test_prompt_shows_where_an_event_goes():
    """细则 12.2 a script wrote each event on the line after its step, and correct code failed 8 propositions."""
    align = (Path(FLOW.__file__).parent / "prompts" / "align.md").read_text(encoding="utf-8")
    for phrase in ("事件写在发生之前的那一行", "载客数还是接到前的 2", "交答案前运行一次脚本"):
        assert phrase in align
