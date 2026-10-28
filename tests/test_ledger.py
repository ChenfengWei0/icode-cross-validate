"""The design's reconciliation example (§1, §3 流程 3): every money movement goes through payment."""

from __future__ import annotations

import json

import cv_protocol as P

PLAN = ("requirements.json", "generator/segments.txt", "generator/source_a.txt", "generator/source_b.txt")
GOOD = ["src/account.c", "src/payment.c", "src/refund.c"]


def _with_refund(project, variant: str) -> list[str]:
    (project.root / "src/refund.c").write_text(project.read(f"variants/{variant}"), encoding="utf-8")
    return GOOD


def test_plan_freezes_structure_and_invariant(make_project):
    project = make_project("ledger")
    project.plan(*PLAN)
    seg = project.segment()
    assert seg["status"] in (P.SEG_DETERMINED, P.SEG_AMBIGUOUS)
    kinds = sorted(a["kind"] for a in seg["readings"][0]["artifacts"])
    assert kinds == ["P2", "P4"]
    assert all(r["ok"] for r in seg["selfcheck"].values())
    assert sorted(p.name for p in (project.root / ".cv/subtasks").iterdir())


def test_correct_implementation_passes_with_proof(make_project):
    project = make_project("ledger")
    state = project.complete(project.plan(*PLAN), GOOD)
    assert state["signal"] == P.SIGNAL_PASS
    assert state["report"]["segments"][0]["strength"] == "static"  # weakest of static (P4) and proof (P2)
    contract = json.loads((project.root / ".cv/contracts/R1.S1.json").read_text(encoding="utf-8"))
    assert {a["kind"] for a in contract["artifacts"]} == {"P2", "P4"}


def test_bypassing_the_payment_module_is_blocked(make_project):
    project = make_project("ledger")
    state = project.plan(*PLAN)
    state = project.complete(state, _with_refund(project, "refund_bypass.c"))
    assert state["signal"] == P.SIGNAL_BLOCK
    assert "refund writes account_t.balance" in state["feedback"]


def test_wrong_amount_is_blocked_with_a_call_sequence(make_project):
    project = make_project("ledger")
    state = project.plan(*PLAN)
    state = project.complete(state, _with_refund(project, "refund_double.c"))
    assert state["signal"] == P.SIGNAL_BLOCK
    assert "refund(" in state["feedback"] and "account_init(" in state["feedback"]
