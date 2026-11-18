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


MODULE = textwrap.dedent('''
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
''')

PROPS = {"R1": prop("R1", "G(答案 = 最小值)"), "R2": prop("R2", "G(个数 ≥ 1)")}


@pytest.fixture
def module(tmp_path):
    (tmp_path / IO.MODULE).write_text(MODULE)
    m = IO.Module(tmp_path / IO.MODULE, IO.settings({}, {}))
    yield m
    m.close()


def test_trial_passes_on_a_faithful_module(module):
    report = IO.trial(module, PROPS, set(), REQ, set(), IO.settings({}, {}))
    assert report["problems"] == [], report
    assert report["contradicted"] == {}
    assert report["derived"] == ["个数", "最小值"]


def test_trial_reports_a_contradiction_on_the_examples(module):
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
    examples = [["3\n5 2 7\n", "2\n"]]
    kills = IO.mutation_kills(module, PROPS, set(), examples, {}, set(), IO.settings({}, {}))
    assert kills["R1"] > 0
    assert kills["R2"] == 0


def test_derive_and_trace_must_not_give_the_same_key():
    item = {"skip": {"ok": []}, "derive": {"ok": {"值": {"答案": 1}}}, "trace": {"ok": [{"值": {"答案": 1}}]}}
    states, unreadable, err = IO.build_trace(item)
    assert states is None and "同一个键" in err


def test_presumed_results_are_final_at_the_end_of_a_run():
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
        "io": {"settings": {"command": "python3 -I main.py"}, "examples": examples, "example_of": {},
               "example_props": [], "derived": [], "kills": {"R1": 3, "R2": 0}},  # fmt: skip
    }
    recs = IO.run(run, w, PROPS, frozen, set(), {"budget": 600})
    summary = RUN.summarize(recs, PROPS, frozen)["propositions"]
    assert summary["R1"]["result"] == expected
    assert summary["R2"].get("vacuous") is True  # 细则 11.12: it stopped no mutant
