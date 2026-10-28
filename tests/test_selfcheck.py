"""Self-check (§3 流程 5) and the rewrite-once rule (算法1 第10行)."""

from __future__ import annotations

import json

import cv_protocol as P

PRE = "int cv_pre_discount(long total) { return total >= 0 && total <= 100000000; }\n"
ONCE = PRE + "int cv_post_discount(long total, long paid) { return paid == (total >= 100 ? total - 20 : total); }\n"


def harness(assertion: str, premise: str = "cv_pre_discount(total)") -> str:
    return (
        "int main(void) {\n    long total = __VERIFIER_nondet_long();\n"
        f"    __VERIFIER_assume({premise});\n    long paid = discount(total);\n"
        "#ifdef CHECK_REACH\n    assert(0);\n#else\n"
        f"    assert({assertion});\n#endif\n    return 0;\n}}\n"
    )


def answer(*artifacts: dict) -> str:
    return "```json\n" + json.dumps({"artifacts": list(artifacts)}, ensure_ascii=False) + "\n```\n"


def p1(reading: str, spec: str, harness_text: str) -> dict:
    return {"item": "R1", "segment": "S1", "form": "property", "kind": "P1", "reading": reading,
            "functions": ["discount"], "spec": spec, "harness": harness_text}  # fmt: skip


def plan_with(project, a: dict, *, rewrite_a: dict | None = None):
    (project.root / "a.txt").write_text(answer(a), encoding="utf-8")
    return project.plan(
        "requirements.json", "generator/segments.txt", "a.txt", None,
        rewrite_a=answer(rewrite_a) if rewrite_a else None,
    )  # fmt: skip


def test_good_property_passes_all_four_checks(make_project):
    project = make_project("discount")
    plan_with(project, p1("只减一次", ONCE, harness("cv_post_discount(total, paid)")))
    seg = project.segment()
    assert seg["status"] == P.SEG_DETERMINED
    checks = {c["check"]: c["ok"] for c in next(iter(seg["selfcheck"].values()))["checks"]}
    assert checks == {"compiles": True, "uses_spec": True, "negation": True, "reach": True}
    assert "只有一个来源给出了子任务产物" in seg["notes"]


def test_vacuous_property_is_discarded(make_project):
    project = make_project("discount")
    plan_with(project, p1("空洞", PRE, harness("paid == paid")))
    seg = project.segment()
    assert seg["status"] == P.SEG_UNCOVERED
    assert "vacuous" in seg["removed"][0]["why"]


def test_contradictory_premises_are_discarded(make_project):
    project = make_project("discount")
    plan_with(project, p1("前提矛盾", ONCE, harness("cv_post_discount(total, paid)", "total < 0 && total > 0")))
    assert "no path reaches the assertion" in project.segment()["removed"][0]["why"]


def test_missing_reach_branch_is_a_failure(make_project):
    project = make_project("discount")
    no_reach = (
        harness("cv_post_discount(total, paid)")
        .replace("#ifdef CHECK_REACH\n    assert(0);\n#else\n", "")
        .replace("#endif\n", "")
    )
    plan_with(project, p1("无 CHECK_REACH", ONCE, no_reach))
    assert "CHECK_REACH" in project.segment()["removed"][0]["why"]


def test_compile_error_is_rewritten_once_and_kept(make_project):
    project = make_project("discount")
    broken = p1("只减一次", ONCE, harness("cv_post_discount(total, paid)").replace("long paid", "long paid =="))
    fixed = p1("只减一次", ONCE, harness("cv_post_discount(total, paid)"))
    state = plan_with(project, broken, rewrite_a=fixed)
    seg = project.segment()
    assert seg["status"] == P.SEG_DETERMINED and not seg["removed"]
    assert project.complete(state, ["src/once.c"])["signal"] == P.SIGNAL_PASS


def test_rewrite_that_still_fails_is_dropped(make_project):
    project = make_project("discount")
    broken = p1("只减一次", ONCE, harness("cv_post_discount(total, paid)").replace("long paid", "long paid =="))
    state = plan_with(project, broken, rewrite_a=broken)
    seg = project.segment()
    assert seg["status"] == P.SEG_UNCOVERED
    assert "重写后自检仍不通过" in seg["removed"][0]["why"]
    # 未覆盖的要求不参与汇总: nothing is checked, the judge passes and the report says why.
    state = project.complete(state, ["src/wrong.c"])
    assert state["signal"] == P.SIGNAL_PASS
    assert state["report"]["segments"][0]["status"] == P.SEG_UNCOVERED


def test_uncovered_segment_keeps_the_generators_reason(make_project):
    project = make_project("discount")
    (project.root / "a.txt").write_text(
        answer({"item": "R1", "segment": "S1", "form": "uncovered", "reason": "“及时”没有确定的真假条件"}),
        encoding="utf-8",
    )
    project.plan("requirements.json", "generator/segments.txt", "a.txt", None)
    seg = project.segment()
    assert seg["status"] == P.SEG_UNCOVERED and "及时" in seg["reason"]


def test_segment_span_must_come_from_the_item(make_project):
    project = make_project("discount")
    (project.root / "seg.txt").write_text(
        '```json\n{"items": [{"item": "R1", "segments": [{"id": "S1", "span": "满 200 元减 50 元", "functions": ["discount"]}]}]}\n```',
        encoding="utf-8",
    )
    project.plan("requirements.json", "seg.txt", "generator/source_a.txt", None)
    assert "不在条目原文中" in project.segment("R1", "S0")["reason"]
