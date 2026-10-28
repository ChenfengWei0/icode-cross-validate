"""The other two forms (reference model, concrete cases), random testing, and conflicts."""

from __future__ import annotations

import json

import cv_protocol as P
from test_selfcheck import ONCE, PRE, answer, harness, p1

MODEL = PRE + (
    "long cv_ref_discount(long total) {\n    long paid = total;\n"
    "    if (total >= 100) paid = total - 20;\n    return paid;\n}\n"
)
MODEL_HARNESS = (
    "int main(void) {\n    long total = __VERIFIER_nondet_long();\n    __VERIFIER_assume(cv_pre_discount(total));\n"
    "#ifdef CHECK_REACH\n    assert(0);\n#else\n    assert(discount(total) == cv_ref_discount(total));\n#endif\n"
    "    return 0;\n}\n"
)


def run(project, artifact: dict, impl: str, *, requirements: str = "requirements.json") -> dict:
    (project.root / "a.txt").write_text(answer(artifact), encoding="utf-8")
    state = project.plan(requirements, "generator/segments.txt", "a.txt", None)
    return project.complete(state, [impl])


def test_reference_model_by_differential_testing(make_project):
    model = {"item": "R1", "segment": "S1", "form": "reference_model", "reading": "只减一次",
             "functions": ["discount"], "model": MODEL, "harness": MODEL_HARNESS}  # fmt: skip
    ok = run(make_project("discount", "ref-ok"), model, "src/once.c")
    assert ok["signal"] == P.SIGNAL_PASS and ok["report"]["segments"][0]["strength"] == "difftest"
    bad = run(make_project("discount", "ref-bad"), model, "src/wrong.c")
    assert bad["signal"] == P.SIGNAL_BLOCK and "discount(total=" in bad["feedback"]


def test_concrete_cases_run_directly(make_project):
    cases = {"item": "R1", "segment": "S1", "form": "cases", "reading": "只减一次", "functions": ["discount"],
             "cases": [{"call": "discount(200)", "expect": "180"}, {"call": "discount(99)", "expect": "99"}]}  # fmt: skip
    ok = run(make_project("discount", "cases-ok"), cases, "src/once.c")
    assert ok["signal"] == P.SIGNAL_PASS and ok["report"]["segments"][0]["strength"] == "cases"
    bad = run(make_project("discount", "cases-bad"), cases, "src/every.c")
    assert bad["signal"] == P.SIGNAL_BLOCK and "discount(200) returned 160, expected 180" in bad["feedback"]


def test_cases_need_a_constant_expectation(make_project):
    project = make_project("discount")
    cases = {"item": "R1", "segment": "S1", "form": "cases", "reading": "x", "functions": ["discount"],
             "cases": [{"call": "discount(200)", "expect": "discount(100) + 100"}]}  # fmt: skip
    (project.root / "a.txt").write_text(answer(cases), encoding="utf-8")
    project.plan("requirements.json", "generator/segments.txt", "a.txt", None)
    assert project.segment()["status"] == P.SEG_UNCOVERED


def test_min_strength_turns_weak_passes_into_unknown(make_project):
    project = make_project("discount")
    (project.root / ".cv/config.json").write_text(json.dumps({"min_strength": "bounded"}), encoding="utf-8")
    cases = {"item": "R1", "segment": "S1", "form": "cases", "reading": "x", "functions": ["discount"],
             "cases": [{"call": "discount(200)", "expect": "180"}]}  # fmt: skip
    state = run(project, cases, "src/once.c")
    assert state["signal"] == P.SIGNAL_PASS  # "未知"默认不阻止放行
    assert state["report"]["functions"]["discount"]["result"] == "unknown"


def test_unknown_blocks_when_configured(make_project):
    project = make_project("discount")
    (project.root / ".cv/config.json").write_text(
        json.dumps({"min_strength": "proof", "unknown_blocks": True}), encoding="utf-8"
    )
    cases = {"item": "R1", "segment": "S1", "form": "cases", "reading": "x", "functions": ["discount"],
             "cases": [{"call": "discount(200)", "expect": "180"}]}  # fmt: skip
    assert run(project, cases, "src/once.c")["signal"] == P.SIGNAL_BLOCK


def test_random_testing_finds_the_counterexample(make_project):
    import judge
    from checker.common import Limits
    from checker.compose import property_file
    from checker.fuzz import fuzz

    project = make_project("discount")
    (project.root / "a.txt").write_text(
        answer(p1("只减一次", ONCE, harness("cv_post_discount(total, paid)"))), encoding="utf-8"
    )
    project.plan("requirements.json", "generator/segments.txt", "a.txt", None)
    store = judge.Store(project.root)
    artifact = project.segment()["readings"][0]["artifacts"][0]
    results = {}
    for impl in ("once", "wrong"):
        ctx = judge._context(
            store, judge.load_config(project.root), store.load("interface", None), judge._workdir(store, impl)
        )
        ctx.limits = Limits(fuzz_iterations=500, fuzz_seed=7)
        ctx.impl_files = [project.root / f"src/{impl}.c"]
        results[impl] = fuzz(ctx, property_file(ctx, artifact), [], name=impl, check_reach=True)
    assert results["once"]["status"] == "pass"
    assert results["wrong"]["status"] == "fail"
    assert results["wrong"]["counterexample"]["calls"][0].startswith("discount(total=")


def test_contradicting_requirements_are_marked_as_conflict(make_project):
    project = make_project("discount")
    (project.root / "req.json").write_text(
        json.dumps({"id": "Q-c", "items": [
            {"id": "R1", "text": "下单时总价满 100 元减 20 元。"},
            {"id": "R2", "text": "实付金额总是高于总价。"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )  # fmt: skip
    (project.root / "seg.txt").write_text(
        "```json\n" + json.dumps({"items": [
            {"item": "R1", "segments": [{"id": "S1", "span": "下单时总价满 100 元减 20 元", "functions": ["discount"]}]},
            {"item": "R2", "segments": [{"id": "S1", "span": "实付金额总是高于总价", "functions": ["discount"]}]},
        ]}, ensure_ascii=False) + "\n```",
        encoding="utf-8",
    )  # fmt: skip
    higher = PRE + "int cv_post_discount(long total, long paid) { return paid > total; }\n"
    r2 = dict(p1("高于总价", higher, harness("cv_post_discount(total, paid)")), item="R2")
    (project.root / "a.txt").write_text(
        answer(p1("只减一次", ONCE, harness("cv_post_discount(total, paid)")), r2), encoding="utf-8"
    )
    state = project.plan("req.json", "seg.txt", "a.txt", None)
    assert project.segment("R1")["status"] == P.SEG_CONFLICT
    assert project.segment("R2")["status"] == P.SEG_CONFLICT
    assert project.plan_record()["conflicts"][0]["subject"] == "discount"
    state = project.complete(state, ["src/once.c"], set_id="Q-c")
    assert state["signal"] == P.SIGNAL_PASS  # 冲突的要求不参与汇总，只写入报告
    assert state["report"]["conflicts"]
