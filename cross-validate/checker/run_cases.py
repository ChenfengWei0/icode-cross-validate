"""Concrete cases against the implementation: build and run them directly (§3 流程 6)."""

from __future__ import annotations

import json
from typing import Any

import cv_protocol as P
from checker.common import Context, native_compile, run_tool
from checker.compose import cases_program


def run_cases(ctx: Context, artifact: dict[str, Any]) -> dict[str, Any]:
    program = cases_program(ctx, artifact)
    binary = ctx.workdir / f"{artifact['id']}.cases.bin"
    error = native_compile(ctx, [program, *ctx.impl_files], binary)
    if error:
        return P.check_result(P.RESULT_UNKNOWN, reason=f"the cases do not build with the implementation: {error}")
    proc = run_tool([str(binary)], timeout=ctx.limits.safety_timeout, cwd=ctx.workdir)
    if proc.timed_out:
        return P.check_result(P.RESULT_UNKNOWN, reason="the cases hit the safety timeout")
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    cases = artifact["content"]["cases"]
    failed = [line for line in lines if not line.get("ok")]
    if len(lines) < len(cases) and not failed:
        return P.check_result(P.RESULT_UNKNOWN, reason="not every case reported a result")
    if failed:
        calls = []
        for line in failed:
            case = cases[line["case"]]
            got = f"crashed with signal {line['crash']}" if "crash" in line else f"returned {line.get('actual')}"
            setup = f"{case['setup'].strip()} " if case.get("setup", "").strip() else ""
            calls.append(f"{setup}{case['call']} {got}, expected {case['expect']}")
        return P.check_result(P.RESULT_FAIL, counterexample={"calls": calls, "method": "cases"})
    return P.check_result(P.RESULT_PASS, strength="cases", reason=f"{len(cases)} cases")
