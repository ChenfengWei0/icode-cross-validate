"""Formal properties P1-P3 against the implementation (§3 流程 6).

Three methods in order, each tried only when the one before reached no conclusion; earlier ones let
fewer errors through (约束 1):

1. the whole value range: CBMC with unwinding assertions (every path covered), or k-induction
   (base case and step case) for harnesses that loop forever;
2. within a bound: CBMC unwinding every loop ``bounded_unwind`` times;
3. random testing (``fuzz.py``).

Before claiming a pass the assertion must be reachable with this implementation (Hold ∧ Reach): a
property that holds only because no path gets to it has checked nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv_protocol as P
from checker.common import Context, cbmc, counterexample_from_trace, goto_compile, has_loops, k_induction
from checker.fuzz import fuzz


def _counterexample(outcome, ctx: Context, method: str) -> dict[str, Any]:
    ce = counterexample_from_trace(outcome.failures[0], set(ctx.interface["functions"]))
    ce["method"] = method
    return ce


def prove(ctx: Context, harness: Path, name: str) -> dict[str, Any]:
    sources = [harness, *ctx.impl_files]
    goto = ctx.workdir / f"{name}.goto"
    reach_goto = ctx.workdir / f"{name}.reach.goto"
    error = goto_compile(ctx, sources, goto) or goto_compile(ctx, sources, reach_goto, ["CHECK_REACH"])
    if error:
        # The harness built against the headers at self-check; with the code it does not: the code's fault.
        return P.check_result(
            P.RESULT_FAIL,
            counterexample={"calls": [], "reason": f"实现与检查程序无法一起构建：{error}", "method": "build"},
        )

    # Reach: with CHECK_REACH the assertion is assert(0); a failure means some path gets there. Unwinding
    # assertions make "no path" a proof rather than an artefact of the bound.
    reach = cbmc(ctx, reach_goto, unwind=ctx.limits.bounded_unwind, unwinding_assertions=True)
    if reach.verdict == "success":
        # Self-check showed the assertion reachable for some implementation under these premises; this
        # one stops (exit, abort, assume(0)) on every input the premises allow: it never delivers.
        reason = "对所有满足前提的输入，调用之后都到达不了检查点：实现提前退出、中止或不返回"
        return P.check_result(
            P.RESULT_FAIL, reach=False, counterexample={"calls": [], "reason": reason, "method": "reach"}
        )
    reach_known = reach.verdict == "failure"
    notes: list[str] = []

    # 1. The whole value range.
    if has_loops(ctx, goto):
        state, outcome = k_induction(ctx, goto)
        if state == "failure":
            return P.check_result(P.RESULT_FAIL, counterexample=_counterexample(outcome, ctx, "k-induction base case"))
        if state == "proof" and reach_known:
            return P.check_result(P.RESULT_PASS, strength="proof", reach=True, reason="k-induction")
        full = cbmc(ctx, goto, unwind=ctx.limits.unwind_full, unwinding_assertions=True)
    else:
        full = cbmc(ctx, goto, unwind=None, unwinding_assertions=False)
    if full.verdict == "failure":
        return P.check_result(P.RESULT_FAIL, counterexample=_counterexample(full, ctx, "full-range proof"))
    if full.verdict == "success" and reach_known:
        return P.check_result(P.RESULT_PASS, strength="proof", reach=True, reason="every path covered")
    notes.append(f"full range: {full.reason or full.verdict}")

    # 2. Within a bound.
    bounded = cbmc(ctx, goto, unwind=ctx.limits.bounded_unwind, unwinding_assertions=False)
    if bounded.verdict == "failure":
        return P.check_result(P.RESULT_FAIL, counterexample=_counterexample(bounded, ctx, "bounded proof"))
    if bounded.verdict == "success" and reach_known:
        return P.check_result(
            P.RESULT_PASS, strength="bounded", reach=True, reason=f"loops unwound {ctx.limits.bounded_unwind} times"
        )
    notes.append(f"bounded: {bounded.reason or bounded.verdict}")

    # 3. Random testing.
    result = fuzz(ctx, harness, [], name=name, check_reach=True)
    if result["status"] == P.RESULT_FAIL:
        ce = dict(result["counterexample"], method="random testing", reason=result["reason"])
        return P.check_result(P.RESULT_FAIL, counterexample=ce)
    if result["status"] == P.RESULT_PASS:
        runs = result["detail"].get("valid", 0)
        return P.check_result(P.RESULT_PASS, strength="fuzz", reach=True, reason=f"{runs} random runs")
    notes.append(f"random testing: {result['reason']}")
    return P.check_result(P.RESULT_UNKNOWN, reason="; ".join(notes))
