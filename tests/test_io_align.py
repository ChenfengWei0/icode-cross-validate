"""io alignment (细则 11.12): the program side, with hand-written modules instead of a model."""

import shutil
import textwrap

import pytest

import cv_formula as F
import cv_io as IO
import cv_monitor as M
import cv_run as RUN
import cv_state as S

pytestmark = pytest.mark.skipif(shutil.which("bwrap") is None, reason="io alignment runs in bwrap")

REQ = """Given N and a sequence A, print the minimum of A.

Sample Input 1

3
5 2 7

Sample Output 1

2
"""


def decls() -> F.Decls:
    d = F.Decls()
    for name, cat in (("答案", "数量"), ("最小值", "数量"), ("个数", "数量")):
        d.category[name], d.params[name] = cat, []
    return d


def prop(pid: str, formula: str, source: str = "推理") -> dict:
    tree = F.parse(formula, decls())
    return {"id": pid, "source": source, "basis": "", "tree": tree, "formula": formula, "class": F.classify(tree)}


MODULE = textwrap.dedent("""
    def examples():
        return [("3\\n5 2 7\\n", "2\\n")]

    def valid_input(text):
        n, *rest = text.split()
        return [] if int(n) == len(rest) and int(n) >= 1 else ["N 与个数不符"]

    def generate(rng, scale):
        a = [rng.randint(-5, 5) for _ in range(rng.randint(1, scale + 1))]
        return f"{len(a)}\\n{' '.join(map(str, a))}\\n"

    def derive(input_text):
        a = list(map(int, input_text.split()[1:]))
        return {"值": {"最小值": min(a), "个数": len(a)}}

    def trace(input_text, output_text):
        s = output_text.split()
        if len(s) != 1:
            return {"无法读出": "应当只有一个数"}
        try:
            return [{"值": {"答案": int(s[0])}}]
        except ValueError:
            return {"无法读出": "不是整数"}

    def skip(input_text):
        return []

    EXAMPLE_OF = {}
""")

PROPS = {"R1": prop("R1", "G(答案 = 最小值)"), "R2": prop("R2", "G(个数 ≥ 1)")}


@pytest.fixture
def module(tmp_path):
    (tmp_path / IO.MODULE).write_text(MODULE)
    m = IO.Module(tmp_path / IO.MODULE, IO.settings({}, {}))
    yield m
    m.close()


def test_trial_passes_on_a_faithful_module(module):
    """细则 11.12 冻结前试跑: a faithful module passes the trial on the examples."""
    report = IO.trial(module, PROPS, set(), REQ, set(), IO.settings({}, {}))
    assert report["problems"] == [], report
    assert report["contradicted"] == {}
    assert report["derived"] == ["个数", "最小值"]


def test_trial_reports_a_contradiction_on_the_examples(module):
    """细则 11.12 试跑第 5 项: a proposition violated on an example is reported."""
    bad = {"R3": prop("R3", "G(答案 > 最小值)")}
    report = IO.trial(module, bad, set(), REQ, set(), IO.settings({}, {}))
    assert "R3" in report["contradicted"]


def test_example_lines_must_come_from_the_requirement(tmp_path):
    (tmp_path / IO.MODULE).write_text(MODULE.replace('"2\\n"', '"9\\n"'))
    m = IO.Module(tmp_path / IO.MODULE, IO.settings({}, {}))
    try:
        report = IO.trial(m, PROPS, set(), REQ, set(), IO.settings({}, {}))
    finally:
        m.close()
    assert any("不在需求原文中" in p for p in report["problems"])


def test_mutants_change_one_number_and_cut():
    out = IO.mutants("2 3\n4\n", ["9\n"], 20)
    assert "3 3\n4\n" in out and "2 4\n4\n" in out and "2 3\n5\n" in out
    assert "" in out and "2 3\n" in out and "9\n" in out


def test_mutation_kills_only_count_discriminating_propositions(module):
    """细则 11.12 变异对照: only propositions that stop a wrong output count."""
    examples = [["3\n5 2 7\n", "2\n"]]
    kills = IO.mutation_kills(module, PROPS, set(), examples, {}, set(), IO.settings({}, {}))
    assert kills["R1"] > 0
    assert kills["R2"] == 0


def test_derive_and_trace_must_not_give_the_same_key():
    item = {"skip": {"ok": []}, "derive": {"ok": {"值": {"答案": 1}}}, "trace": {"ok": [{"值": {"答案": 1}}]}}
    states, _, err = IO.build_trace(item)
    assert states is None and "同一个键" in err


def test_presumed_results_are_final_at_the_end_of_a_run():
    """细则 11.12 轨迹的结束就是运行的结束."""
    p = prop("R5", "F(答案 = 1)")
    assert IO.judge(p, [{"值": {"答案": 2}}], set(), 1e-6)["result"] == M.VIOLATED
    assert IO.judge(prop("R6", "G(答案 = 2)"), [{"值": {"答案": 2}}], set(), 1e-6)["result"] == M.SATISFIED


def test_tolerance_is_relative():
    p = prop("R7", "G(答案 = 最小值)")
    states = [{"值": {"答案": 1000000.0004, "最小值": 1000000.0}}]
    assert IO.judge(p, states, set(), 1e-6)["result"] == M.SATISFIED
    assert M.judge(p["tree"], states, set())["result"] == M.VIOLATED


def test_structural_from_python_files(tmp_path):
    (tmp_path / "main.py").write_text("class Solution:\n    def f(self, a: int) -> int:\n        return a\n")
    d = F.Decls()
    d.category["Solution"], d.params["Solution"] = "接口", []
    d.category["f"], d.params["f"] = "接口", []
    sig = {"tree": F.node("struct", F.code("Solution.f"), F.lit("f(self, a: int) -> int"), kind="signature")}
    assert IO.structural(sig, tmp_path)[0] == M.PASSED
    bare = {"tree": F.node("struct", F.code("f"), F.lit("(self, a: int) → int"), kind="signature")}
    assert IO.structural(bare, tmp_path)[0] == M.PASSED
    wrong = {"tree": F.node("struct", F.code("f"), F.lit("(self, a: str) → int"), kind="signature")}
    assert IO.structural(wrong, tmp_path)[0] == M.CHECK_FAILED
    has = {"tree": F.node("struct", F.code("Solution"), F.code("f"), kind="contains")}
    assert IO.structural(has, tmp_path)[0] == M.PASSED
    missing = {"tree": F.node("struct", F.code("Solver"), kind="exists")}
    assert IO.structural(missing, tmp_path)[0] == M.CHECK_FAILED


@pytest.mark.parametrize(
    ("program", "expected"),
    [
        ("a = list(map(int, open(0).read().split()[1:]))\nprint(min(a))\n", M.SATISFIED),
        ("a = list(map(int, open(0).read().split()[1:]))\nprint(max(a))\n", M.VIOLATED),
        ("print('x')\n", M.CHECK_FAILED),
        ("raise SystemExit(3)\n", M.CHECK_FAILED),
    ],
)
def test_runs_judge_the_formula_on_the_program(tmp_path, module, program, expected):
    run = tmp_path / "run"
    S.ensure_private(run / "align")
    shutil.copy(module.dir / IO.MODULE, run / "align" / IO.MODULE)
    w = tmp_path / "w"
    w.mkdir()
    (w / "main.py").write_text(program)
    examples = [["3\n5 2 7\n", "2\n"]]
    S.write_json(run / "align" / IO.INPUTS, IO.freeze_inputs(module, examples, 20))
    frozen = {
        "kind": "io",
        "checks": [],
        "align_failures": {},
        "excluded": {},
        "io": {
            "settings": {"command": "python3 -I main.py"},
            "examples": examples,
            "example_of": {},
            "example_props": [],
            "derived": [],
            "kills": {"R1": 3, "R2": 0},
        },
    }
    recs = IO.run(run, w, PROPS, frozen, set(), {"budget": 600})
    summary = RUN.summarize(recs, PROPS, frozen)["propositions"]
    assert summary["R1"]["result"] == expected
    assert summary["R2"].get("vacuous") is True  # 细则 11.12: it stopped no mutant


CALL_MODULE = MODULE.replace(
    "EXAMPLE_OF = {}",
    textwrap.dedent("""
    EXAMPLE_OF = {}

    def call_input(name, args):
        a = args[0]
        return f"{len(a)}\\n{'  '.join(map(str, a))}\\n"  # spaced differently from the example

    def call_output(name, output_text):
        try:
            return int(output_text.strip())
        except ValueError:
            return {"无法读出": "不是整数"}
    """),
)


def call_prop(pid: str, formula: str) -> dict:
    d = decls()
    d.category["smallest"], d.params["smallest"] = "接口", []
    tree = F.parse(formula, d)
    return {"id": pid, "source": "推理", "basis": "", "tree": tree, "formula": formula, "class": F.classify(tree)}


@pytest.mark.parametrize(("program", "expected"), [("print(2)\n", M.PASSED), ("print(5)\n", M.CHECK_FAILED)])
def test_call_propositions_run_the_program_once(tmp_path, program, expected):
    (tmp_path / IO.MODULE).write_text(CALL_MODULE)
    cfg = IO.settings({}, {"command": "python3 -I main.py"})
    m = IO.Module(tmp_path / IO.MODULE, cfg)
    props = {"R8": call_prop("R8", "`smallest`([5, 2, 7]) = 2"), "R9": call_prop("R9", "`smallest`([4]) = 4")}
    try:
        report = IO.trial(m, props, set(), REQ, set(), cfg)
        assert report["problems"] == [] and report["unmapped"] == {} and report["contradicted"] == {}
        examples = report["examples"]
        kills = IO.mutation_kills(m, props, set(), examples, {}, set(), cfg, report["call_inputs"])
        assert kills["R8"] > 0 and kills["R9"] == 0  # R9's input is not an example
        run = tmp_path / "run"
        S.ensure_private(run / "align")
        shutil.copy(tmp_path / IO.MODULE, run / "align" / IO.MODULE)
        S.write_json(run / "align" / IO.INPUTS, IO.freeze_inputs(m, examples, 5))
    finally:
        m.close()
    w = tmp_path / "w"
    w.mkdir()
    (w / "main.py").write_text(program)
    frozen = {
        "kind": "io",
        "checks": [],
        "align_failures": {},
        "excluded": {},
        "io": {
            "settings": {"command": "python3 -I main.py"},
            "examples": examples,
            "example_of": {},
            "example_props": [],
            "derived": [],
            "kills": kills,
            "call_inputs": report["call_inputs"],
        },
    }
    out = RUN.summarize(IO.run(run, w, props, frozen, set(), {"budget": 600}), props, frozen)["propositions"]
    assert out["R8"]["result"] == expected


def test_a_call_the_module_cannot_map_is_set_aside(module):
    props = {"R8": call_prop("R8", "`smallest`([5, 2, 7]) = 2"), **PROPS}
    report = IO.trial(module, props, set(), REQ, set(), IO.settings({}, {}))
    assert "R8" in report["unmapped"] and report["problems"] == []


def test_accumulation_mode_shows_the_cited_messages(tmp_path):
    """细则 11.12: a consolidated sentence may drop an example; the cited messages are shown in full."""
    import cv_flow as FLOW

    run = tmp_path / "run"
    run.mkdir()
    S.write_json(run / "run.json", {"mode": FLOW.MODE_ACCUM})
    S.write_text(run / "requirement.txt", "输出最小值。")
    S.write_json(
        run / "requirement.json",
        {
            "entries": [{"text": "输出最小值。", "quotes": [{"from": "M2", "text": "最小值"}]}],
            "messages": {"M1": "已被更正", "M2": "更正 M1"},
        },
    )
    (run / "ledger.jsonl").write_text(
        '{"id": "M1", "text": "输出最大值"}\n{"id": "M2", "text": "改成最小值。Input:\\n3\\n5 2 7\\nOutput:\\n2"}\n'
    )
    text = FLOW._io_requirement(run)
    assert "M2（更正 M1）" in text and "5 2 7" in text
    assert "最大值" not in text
