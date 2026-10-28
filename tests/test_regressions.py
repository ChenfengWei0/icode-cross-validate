"""Regressions from the independent review: each test is a way the judge used to pass code it had not checked."""

from __future__ import annotations

import importlib
import json

import cv_protocol as P
import judge
from checker.common import CbmcOutcome
from test_ledger import GOOD, PLAN
from test_selfcheck import ONCE, answer, harness, p1

ARTIFACT = p1("只减一次", ONCE, harness("cv_post_discount(total, paid)"))


def _plan(project, a: dict, b: dict | None = None, segments: str = "generator/segments.txt") -> dict:
    (project.root / "a.txt").write_text(answer(a), encoding="utf-8")
    if b is not None:
        (project.root / "b.txt").write_text(answer(b), encoding="utf-8")
    return project.plan("requirements.json", segments, "a.txt", "b.txt" if b else None)


def _declare(project, files: list[str]) -> None:
    project.manifests += 1
    path = project.root / ".cv" / "manifests" / f"{project.manifests:03d}.json"
    path.write_text(json.dumps({"requirements_id": "Q-discount", "files": files}), encoding="utf-8")


def _source(project, name: str, code: str) -> str:
    (project.root / "src" / name).write_text(code, encoding="utf-8")
    return f"src/{name}"


def test_stopping_without_a_declaration_is_blocked_in_the_workflow(make_project):
    project = make_project("discount")
    state = judge.verify(_plan(project, ARTIFACT), require_declaration=True)
    assert state["signal"] == P.SIGNAL_BLOCK and "没有写新的完成清单" in state["feedback"]


def test_interface_declared_late_is_planned_before_comparison(make_project):
    project = make_project("discount")
    (project.root / ".cv" / "interface.json").unlink()
    state = _plan(project, ARTIFACT)
    assert project.plan_record()["items"]["R1"]["segments"][0]["status"] == P.SEG_UNCOVERED

    (project.root / ".cv" / "interface.json").write_text(project.read("interface.json"), encoding="utf-8")
    _declare(project, ["src/every.c"])
    state = judge.verify(state, require_declaration=True)
    assert state["signal"] is None and state["replan_items"] == ["R1"]

    state = judge.plan_prepare(state, "replan")
    assert state["has_work"]
    state = judge.plan_requests(state, project.read("generator/segments.txt"))
    state = judge.plan_check(state, project.read("a.txt"), None)
    state = judge.compare(judge.plan_freeze(state, None, None))
    assert state["signal"] == P.SIGNAL_BLOCK


def test_code_that_does_not_compile_is_blocked(make_project):
    project = make_project("discount")
    broken = _source(project, "broken.c", '#include "discount.h"\nlong discount(long total) { return total - ; }\n')
    state = project.complete(_plan(project, ARTIFACT), [broken])
    assert state["signal"] == P.SIGNAL_BLOCK and "无法编译" in state["feedback"]


def test_code_that_never_returns_is_blocked(make_project):
    project = make_project("discount")
    code = '#include <stdlib.h>\n#include "discount.h"\nlong discount(long total) { exit(0); }\n'
    state = project.complete(_plan(project, ARTIFACT), [_source(project, "exits.c", code)])
    assert state["signal"] == P.SIGNAL_BLOCK and "到达不了检查点" in state["feedback"]


def test_undefined_function_is_blocked(make_project):
    project = make_project("discount")
    empty = _source(project, "empty.c", '#include "discount.h"\n')
    state = project.complete(_plan(project, ARTIFACT), [empty])
    assert state["signal"] == P.SIGNAL_BLOCK


def test_changed_callee_is_not_served_from_cache(make_project):
    # The P2 artifact lists pay and refund; its harness also calls account_init, which neither calls.
    project = make_project("ledger")
    state = project.plan(*PLAN)
    assert project.complete(state, GOOD)["signal"] == P.SIGNAL_PASS
    account = project.root / "src" / "account.c"
    account.write_text(account.read_text(encoding="utf-8").replace("= initial;", "= initial + 1;"), encoding="utf-8")
    assert project.complete(state, GOOD)["signal"] == P.SIGNAL_BLOCK


def test_write_through_a_cast_is_blocked(make_project):
    project = make_project("ledger")
    state = project.plan(*PLAN)
    (project.root / "src" / "refund.c").write_text(
        '#include "refund.h"\n\nvoid refund(account_t *acc, int amount) { *(long long *)acc += amount; }\n',
        encoding="utf-8",
    )
    state = project.complete(state, GOOD)
    assert state["signal"] == P.SIGNAL_BLOCK and "refund writes account_t.balance" in state["feedback"]


def test_cpp_delivery_is_rejected(make_project):
    project = make_project("discount")
    cpp = _source(project, "once.cpp", project.read("src/once.c"))
    state = project.complete(_plan(project, ARTIFACT), [cpp])
    assert state["signal"] == P.SIGNAL_REJECT


def test_segment_without_functions_is_uncovered(make_project):
    project = make_project("discount")
    segments = project.read("generator/segments.txt").replace('"discount"', "")
    (project.root / "segments.txt").write_text(segments, encoding="utf-8")
    _plan(project, ARTIFACT, segments="segments.txt")
    seg = project.segment()
    assert seg["status"] == P.SEG_UNCOVERED and "没有涉及头文件中声明的接口函数" in json.dumps(seg, ensure_ascii=False)


def test_equal_predicates_under_different_premises_stay_two_readings(make_project):
    project = make_project("discount")
    narrow = p1(
        "只减一次（小额）", ONCE, harness("cv_post_discount(total, paid)", "cv_pre_discount(total) && total < 1000")
    )
    _plan(project, ARTIFACT, dict(narrow, reading="只减一次（小额）"))
    assert len(project.segment()["readings"]) == 2


def test_harness_must_assert_the_spec_it_defines(make_project):
    project = make_project("discount")
    unused = p1("只减一次", ONCE, harness("paid <= total"))
    _plan(project, unused)
    seg = project.segment()
    assert seg["status"] == P.SEG_UNCOVERED
    assert "does not assert ['cv_post_discount']" in seg["removed"][0]["why"]


def test_inconclusive_self_check_is_not_a_pass(make_project, monkeypatch):
    project = make_project("discount")
    monkeypatch.setattr(
        importlib.import_module("checker.selfcheck"),
        "cbmc",
        lambda *a, **k: CbmcOutcome("unknown", reason="conflict limit"),
    )
    _plan(project, ARTIFACT)
    seg = project.segment()
    assert seg["status"] == P.SEG_UNCOVERED and "could not show" in seg["removed"][0]["why"]
