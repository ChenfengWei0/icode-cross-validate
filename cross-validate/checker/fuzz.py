"""Random testing of a harness against the implementation (§3 流程 6, the third and weakest method).

The harness keeps its SV-COMP form; ``runtime/cv_fuzz_runtime.c`` supplies the nondet functions
from a seeded generator and runs each iteration in a forked child. Interface calls made by the
harness are logged through linker wrappers (GNU ld ``--wrap``), so a counterexample is the sequence
of calls with their arguments, never the harness itself.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from checker.common import RUNTIME, Context, native_compile, run_tool, write

_SIGNED = ("char", "short", "int", "long", "_Bool", "bool", "signed", "int8_t", "int16_t", "int32_t", "int64_t")
_SIGNED += ("ssize_t", "ptrdiff_t", "intptr_t", "intmax_t")


def _log_expr(ctype: str, name: str) -> str:
    t = ctype.replace("const", "").strip()
    if "*" in t or "[" in t or "(" in t:
        return f'fprintf(cv_log, "{name}=<pointer>")'
    if "float" in t or "double" in t:
        return f'fprintf(cv_log, "{name}=%.17g", (double){name})'
    if "unsigned" in t or t in ("size_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t", "uintptr_t", "uintmax_t"):
        return f'fprintf(cv_log, "{name}=%llu", (unsigned long long){name})'
    if any(word in t.split() for word in _SIGNED):
        return f'fprintf(cv_log, "{name}=%lld", (long long){name})'
    return f'fprintf(cv_log, "{name}=<value>")'


def wrappers(ctx: Context) -> tuple[str, list[str]]:
    """``__wrap_f`` for every interface function: log the call when the harness makes it."""
    if not sys.platform.startswith("linux"):
        return "", []
    out = ["#include <stdio.h>", "extern FILE *cv_log;", "static int cv_depth;"]
    names = []
    for fn in ctx.interface["functions"].values():
        params = fn["params"]
        if any(not p["name"] for p in params):
            continue
        signature = ", ".join(f"{p['type']} {p['name']}" for p in params) or "void"
        args = ", ".join(p["name"] for p in params)
        logs = '; fprintf(cv_log, ", "); '.join(_log_expr(p["type"], p["name"]) for p in params)
        ret = fn["return"]
        call = f"__real_{fn['name']}({args})"
        log_call = (
            f'    if (cv_log && cv_depth == 0) {{ fprintf(cv_log, "call {fn["name"]}("); {logs}{";" if logs else ""} '
            'fprintf(cv_log, ")\\n"); }'
        )
        body = [log_call, "    cv_depth++;"]
        if ret == "void":
            body += [f"    {call};", "    cv_depth--;"]
        else:
            body += [f"    {ret} cv_r = {call};", "    cv_depth--;", "    return cv_r;"]
        out.append(f"{ret} __real_{fn['name']}({signature});")
        out.append(f"{ret} __wrap_{fn['name']}({signature}) {{\n" + "\n".join(body) + "\n}")
        names.append(fn["name"])
    return "\n".join(out) + "\n", names


def run(ctx: Context, harness: Path, extra_sources: list[Path], *, name: str, reach: bool) -> dict[str, Any]:
    """Build and run one variant. Returns the runtime's summary plus the failing iteration's log."""
    wrap_text, wrapped = wrappers(ctx)
    sources = [harness, *extra_sources, *ctx.impl_files, RUNTIME / "cv_fuzz_runtime.c"]
    if wrap_text:
        sources.append(write(ctx, f"{name}.wrap.c", wrap_text))
    binary = ctx.workdir / f"{name}.{'reach' if reach else 'hold'}.bin"
    extra = ["-Dmain=cv_harness_main"]
    if reach:
        extra.append("-DCHECK_REACH")
    extra += [f"-Wl,--wrap={n}" for n in wrapped]
    # The harness is renamed with -D, which must not rename the runtime's own main: compile separately.
    harness_obj = ctx.workdir / f"{name}.{'reach' if reach else 'hold'}.harness.o"
    error = native_compile(ctx, [harness], harness_obj, ["-c", *extra[: 2 if reach else 1]])
    if error:
        return {"error": error}
    rest = [s for s in sources if s != harness]
    error = native_compile(ctx, [harness_obj, *rest], binary, [f"-Wl,--wrap={n}" for n in wrapped])
    if error:
        return {"error": error}
    log = ctx.workdir / f"{name}.log"
    env = {
        "CV_FUZZ_ITERATIONS": str(ctx.limits.fuzz_iterations),
        "CV_FUZZ_SEED": str(ctx.limits.fuzz_seed),
        "CV_FUZZ_MAX_DRAWS": str(ctx.limits.fuzz_max_draws),
        "CV_FUZZ_LOG": str(log),
    }
    proc = run_tool([str(binary)], timeout=ctx.limits.safety_timeout, env=env, cwd=ctx.workdir)
    if proc.timed_out:
        return {"error": "random testing hit the safety timeout"}
    try:
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": f"random testing gave no summary: {proc.stderr.strip()[-500:]}"}
    if summary.get("failed_iteration", -1) >= 0:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.is_file() else []
        summary["calls"] = [line[5:] for line in lines if line.startswith("call ")]
        summary["nondet_values"] = [line.split()[-1] for line in lines if line.startswith("nondet ")]
    return summary


def fuzz(ctx: Context, harness: Path, extra_sources: list[Path], *, name: str, check_reach: bool) -> dict[str, Any]:
    """Run the harness; with *check_reach*, first establish that its assertion is reached at all.

    Returns {"status": pass|fail|unknown, "reason", "counterexample", "detail"}.
    """
    if check_reach:
        reach = run(ctx, harness, extra_sources, name=name, reach=True)
        if "error" in reach:
            return {"status": "unknown", "reason": reach["error"]}
        if reach.get("failed_iteration", -1) < 0:
            return {
                "status": "unknown",
                "reason": f"no random input reached the assertion ({reach.get('valid', 0)} runs within the premises)",
            }
    hold = run(ctx, harness, extra_sources, name=name, reach=False)
    if "error" in hold:
        return {"status": "unknown", "reason": hold["error"]}
    if hold.get("failed_iteration", -1) >= 0:
        signal = hold.get("signal")
        what = (
            "assertion failed"
            if signal == 6
            else f"the program stopped abnormally (signal {signal}, exit {hold.get('exit_code')})"
        )
        return {
            "status": "fail",
            "reason": what,
            "counterexample": {
                "calls": hold.get("calls", []),
                "nondet_values": hold.get("nondet_values", []),
                "iteration": hold["failed_iteration"],
                "seed": ctx.limits.fuzz_seed,
            },
        }
    if hold.get("valid", 0) == 0:
        return {"status": "unknown", "reason": "no random input satisfied the premises"}
    return {"status": "pass", "detail": hold}
