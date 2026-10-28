"""Self-check: is the artifact itself sound, before it is compared with any code (§3 流程 5)?

Formal properties (P1-P3) pass four checks:

1. it compiles;
2. it holds on the examples (only where an example gives inputs and output, i.e. P1 with
   ``cv_post_<f>``, and reference models);
3. its negation can hold under the harness premises: with "any implementation" (havoc stubs) some
   path violates it, so it is not true of every program;
4. its assertion is reachable: built with ``CHECK_REACH`` the ``assert(0)`` must fail.

Checks 1-2 keep a wrong artifact from rejecting correct code (约束 2); 3-4 keep an empty check from
passing everything (约束 1). "Not true of every program" is judged against arbitrary behaviour, not
against an implementation that does nothing: "nothing bad happens" holds for one that does nothing.

P4 is checked for well-formedness against the interface. Reference models and cases must compile and
agree with the examples; cases must state a constant expected result.
"""

from __future__ import annotations

import re
from typing import Any

import cv_protocol as P
from checker.common import (
    Context,
    cbmc,
    goto_compile,
    havoc_stubs,
    native_compile,
    prelude,
    run_tool,
    syntax_check,
    write,
)
from checker.compose import cases_program, property_file, reference_file, spec_file


def _ok(name: str, ok: bool | None, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def _canonical_names(text: str) -> set[str]:
    return set(re.findall(r"\b(cv_(?:pre|post|inv|rel|ref)_[A-Za-z0-9_]+)\s*\(", text))


def example_checks(ctx: Context, artifact: dict[str, Any], examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the artifact's canonical predicates or model on the examples that give an output.

    Returns one check per example that applies; an empty list when none applies.
    """
    defined = _canonical_names(_spec(artifact))
    checks = []
    for n, ex in enumerate(examples):
        fn, args, result = ex.get("function"), ex.get("args", []), ex.get("result")
        if result is None or fn not in artifact["functions"]:
            continue
        call_args = ", ".join(args)
        if artifact["form"] == P.FORM_PROPERTY and f"cv_post_{fn}" in defined:
            judged = f"cv_post_{fn}({', '.join([*args, result])})"
        elif artifact["form"] == P.FORM_REFERENCE and f"cv_ref_{fn}" in defined:
            judged = f"(cv_ref_{fn}({call_args}) == ({result}))"
        else:
            continue  # this artifact says nothing about the example's output
        conditions = [f"cv_pre_{fn}({call_args})"] if f"cv_pre_{fn}" in defined else []
        conditions.append(judged)
        main = "int main(void) { return (" + " && ".join(conditions) + ") ? 0 : 1; }"
        source = spec_file(ctx, artifact, main, tag=f"example{n}")
        binary = ctx.workdir / f"{artifact['id']}.example{n}.bin"
        error = native_compile(ctx, [source], binary)
        text = ex.get("text") or f"{fn}({call_args}) = {result}"
        if error:
            checks.append(
                _ok("example", False, f"cannot evaluate on example '{text}' without the implementation: {error}")
            )
            continue
        proc = run_tool([str(binary)], timeout=ctx.limits.safety_timeout, cwd=ctx.workdir)
        checks.append(
            _ok("example", proc.returncode == 0, f"example '{text}'" + ("" if proc.returncode == 0 else " is violated"))
        )
    return checks


def _spec(artifact: dict[str, Any]) -> str:
    c = artifact["content"]
    return c.get("spec", "") if artifact["form"] == P.FORM_PROPERTY else c.get("model", "")


def selfcheck(ctx: Context, artifact: dict[str, Any], examples: list[dict[str, Any]]) -> dict[str, Any]:
    form, kind = artifact["form"], artifact.get("kind")
    unknown = sorted(set(artifact["functions"]) - set(ctx.interface["functions"]))
    if unknown:
        return _verdict([_ok("interface", False, f"names functions not declared in the headers: {unknown}")])
    if form == P.FORM_PROPERTY and kind == "P4":
        return _verdict(_check_structure(ctx, artifact))
    if form == P.FORM_PROPERTY:
        return _verdict(_check_property(ctx, artifact, examples))
    if form == P.FORM_REFERENCE:
        return _verdict(_check_reference(ctx, artifact, examples))
    return _verdict(_check_cases(ctx, artifact, examples))


def _verdict(checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [c for c in checks if c["ok"] is False]
    return {"ok": not failed, "checks": checks, "reason": "; ".join(c["detail"] for c in failed)}


def _check_property(ctx: Context, artifact: dict[str, Any], examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = property_file(ctx, artifact)
    error = syntax_check(ctx, source)
    if error:
        return [_ok("compiles", False, error)]
    checks = [_ok("compiles", True)]
    checks += _uses_spec(artifact)
    checks += example_checks(ctx, artifact, examples)
    if "CHECK_REACH" not in artifact["content"]["harness"]:
        checks.append(_ok("reach", False, "the harness has no CHECK_REACH branch, so reachability cannot be checked"))
        return checks
    stubs = write(ctx, f"{artifact['id']}.stubs.c", prelude(ctx) + havoc_stubs(ctx))
    hold = ctx.workdir / f"{artifact['id']}.self.goto"
    reach = ctx.workdir / f"{artifact['id']}.self.reach.goto"
    error = goto_compile(ctx, [source, stubs], hold) or goto_compile(ctx, [source, stubs], reach, ["CHECK_REACH"])
    if error:
        checks.append(_ok("compiles", False, error))
        return checks
    # Every check must pass (§3 流程 5): a check that reaches no conclusion is not a pass.
    negation = cbmc(ctx, hold, unwind=ctx.limits.bounded_unwind, unwinding_assertions=False)
    if negation.verdict == "success":
        checks.append(_ok("negation", False, "holds for every implementation: the check is vacuous"))
    elif negation.verdict == "failure":
        checks.append(_ok("negation", True))
    else:
        checks.append(_ok("negation", False, f"could not show that the property can fail: {negation.reason}"))
    reached = cbmc(ctx, reach, unwind=ctx.limits.bounded_unwind, unwinding_assertions=False)
    if reached.verdict == "success":
        checks.append(_ok("reach", False, "no path reaches the assertion: the premises contradict each other"))
    elif reached.verdict == "failure":
        checks.append(_ok("reach", True))
    else:
        checks.append(_ok("reach", False, f"could not show that the assertion is reachable: {reached.reason}"))
    return checks


def _uses_spec(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """The canonical predicates are what readings, examples and conflicts are judged on, so the harness
    must assert them: otherwise those judgements would be about something other than what is checked."""
    harness = artifact["content"].get("harness", "")
    judged = [n for n in _canonical_names(_spec(artifact)) if not n.startswith("cv_pre_")]
    unused = [n for n in judged if f"{n}(" not in harness]
    if unused:
        return [_ok("uses_spec", False, f"the harness does not assert {unused}, which the spec defines")]
    return [_ok("uses_spec", True)] if judged else []


def _check_structure(ctx: Context, artifact: dict[str, Any]) -> list[dict[str, Any]]:
    c = artifact["content"]
    target = c.get("target") or {}
    record = ctx.interface["records"].get(target.get("record", ""))
    if record is None:
        return [_ok("target", False, f"record {target.get('record')!r} is not defined in the headers")]
    fields = {f["name"]: f for f in record["fields"]}
    field = fields.get(target.get("field", ""))
    if field is None:
        return [_ok("target", False, f"{target.get('record')} has no field {target.get('field')!r}")]
    allowed = c.get("allowed_writers")
    if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
        return [_ok("allowed_writers", False, "allowed_writers must be a list of function names")]
    missing = sorted(set(allowed) - set(ctx.interface["functions"]))
    if missing:
        return [_ok("allowed_writers", False, f"not declared in the headers: {missing}")]
    if field["type"].startswith("const "):
        return [_ok("negation", False, "the field is const: no write can happen, the constraint is vacuous")]
    return [_ok("target", True), _ok("allowed_writers", True), _ok("negation", True, "a write elsewhere is possible")]


def _check_reference(ctx: Context, artifact: dict[str, Any], examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = reference_file(ctx, artifact)
    error = syntax_check(ctx, source)
    if error:
        return [_ok("compiles", False, error)]
    checks = [_ok("compiles", True), *_uses_spec(artifact)]
    names = _canonical_names(artifact["content"]["model"])
    for fn in artifact["functions"]:
        if f"cv_ref_{fn}" not in names:
            checks.append(_ok("signature", False, f"no cv_ref_{fn} for interface function {fn}"))
            continue
        probe = (
            f"_Static_assert(__builtin_types_compatible_p(__typeof__(cv_ref_{fn}), __typeof__({fn})), "
            f'"cv_ref_{fn} must have the signature of {fn}");\nint main(void) {{ return 0; }}'
        )
        error = syntax_check(ctx, spec_file(ctx, artifact, probe, tag=f"sig_{fn}"))
        checks.append(_ok("signature", error is None, error or ""))
    checks += example_checks(ctx, artifact, examples)
    return checks


def _check_cases(ctx: Context, artifact: dict[str, Any], examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases = artifact["content"].get("cases")
    if not isinstance(cases, list) or not cases:
        return [_ok("cases", False, "no cases")]
    if not all(
        isinstance(c, dict) and isinstance(c.get("call"), str) and isinstance(c.get("expect"), str) for c in cases
    ):
        return [_ok("cases", False, "every case needs a call and an expected result")]
    error = syntax_check(ctx, cases_program(ctx, artifact))
    if error:
        return [_ok("compiles", False, f"{error} (the expected result must be a constant arithmetic expression)")]
    checks = [_ok("compiles", True), _ok("determinate", True)]
    by_call = {" ".join(c["call"].split()): c for c in cases if not c.get("setup", "").strip()}
    for ex in examples:
        if ex.get("result") is None:
            continue
        call = " ".join(f"{ex['function']}({', '.join(ex.get('args', []))})".split())
        case = by_call.get(call)
        if case is None:
            continue
        probe = f"int main(void) {{ return (({case['expect']}) == ({ex['result']})) ? 0 : 1; }}"
        binary = ctx.workdir / f"{artifact['id']}.exprobe.bin"
        source = write(ctx, f"{artifact['id']}.exprobe.c", probe + "\n")
        err = native_compile(ctx, [source], binary)
        same = err is None and run_tool([str(binary)], timeout=ctx.limits.safety_timeout).returncode == 0
        checks.append(_ok("example", same, f"case {call} expects {case['expect']}, the example says {ex['result']}"))
    return checks
