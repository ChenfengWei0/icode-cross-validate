"""Simplified reference models against the implementation, by differential testing (§3 流程 6).

The artifact holds the model (``cv_ref_<f>`` with the signature of ``f``, plus the documented
precondition as ``cv_pre_<f>``) and a harness that draws inputs, calls both and asserts that they
agree; ``compose`` puts them in one translation unit. The harness runs on a batch of generated
inputs: the random-testing engine with a fixed seed and iteration count.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv_protocol as P
from checker.common import Context
from checker.fuzz import fuzz


def difftest(ctx: Context, harness: Path, name: str) -> dict[str, Any]:
    result = fuzz(ctx, harness, [], name=name, check_reach="CHECK_REACH" in harness.read_text(encoding="utf-8"))
    if result["status"] == P.RESULT_FAIL:
        ce = dict(result["counterexample"], method="differential testing", reason=result["reason"])
        return P.check_result(P.RESULT_FAIL, counterexample=ce)
    if result["status"] == P.RESULT_PASS:
        runs = result["detail"].get("valid", 0)
        return P.check_result(P.RESULT_PASS, strength="difftest", reason=f"agrees with the model on {runs} inputs")
    return P.check_result(P.RESULT_UNKNOWN, reason=result["reason"])
