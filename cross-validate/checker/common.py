"""Toolchain shared by every checker step: compilers, CBMC, the generated C around an artifact.

Resource limits are deterministic (§3 流程 6): CBMC is bounded by unwinding depth, by the k of
k-induction and by a SAT conflict budget (z3's ``sat.max_conflicts``, through ``runtime/sat.sh``);
random testing by its iteration count and seed. The wall-clock ``safety_timeout`` is only a net
under a tool that hangs; when it fires the result is "unknown" and says so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"
CHECKER_VERSION = "1.0.0"

SV_COMP_NONDET = (
    ("bool", "_Bool"),
    ("char", "char"),
    ("uchar", "unsigned char"),
    ("short", "short"),
    ("ushort", "unsigned short"),
    ("int", "int"),
    ("uint", "unsigned int"),
    ("unsigned", "unsigned int"),
    ("long", "long"),
    ("ulong", "unsigned long"),
    ("longlong", "long long"),
    ("ulonglong", "unsigned long long"),
    ("size_t", "__SIZE_TYPE__"),
    ("float", "float"),
    ("double", "double"),
)


class ToolMissing(RuntimeError):
    pass


_TOOL_ENV = {"clang": "CV_CLANG", "cbmc": "CV_CBMC", "goto-cc": "CV_GOTO_CC", "goto-instrument": "CV_GOTO_INSTRUMENT"}


def tool_path(name: str) -> str:
    override = os.environ.get(_TOOL_ENV.get(name, ""), "")
    found = override or shutil.which(name)
    if not found:
        raise ToolMissing(f"'{name}' is not on PATH; the cross-validation checker needs it")
    return found


@dataclass
class ToolRun:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_tool(argv: list[str], *, timeout: float, cwd: Path | None = None, env: dict[str, str] | None = None) -> ToolRun:
    merged = dict(os.environ, **(env or {}))
    try:
        proc = subprocess.run(  # argv is built by the checker, never by a shell string
            argv, cwd=cwd, env=merged, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        return ToolRun(-1, out, f"safety timeout after {timeout}s", timed_out=True)
    return ToolRun(proc.returncode, proc.stdout, proc.stderr)


# --- limits ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limits:
    """The deterministic resource limits, read from the configuration (§3 集成)."""

    k_induction_max: int = 2
    unwind_full: int = 16
    bounded_unwind: int = 6
    sat_conflicts: int = 2_000_000
    fuzz_iterations: int = 1000
    fuzz_seed: int = 1
    fuzz_max_draws: int = 64
    safety_timeout: float = 300.0
    cflags: tuple[str, ...] = ("-std=c11",)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> Limits:
        return cls(
            k_induction_max=int(config["k_induction_max"]),
            unwind_full=int(config["unwind_full"]),
            bounded_unwind=int(config["bounded_unwind"]),
            sat_conflicts=int(config["sat_conflicts"]),
            fuzz_iterations=int(config["fuzz_iterations"]),
            fuzz_seed=int(config["fuzz_seed"]),
            fuzz_max_draws=int(config["fuzz_max_draws"]),
            safety_timeout=float(config["safety_timeout_seconds"]),
            cflags=tuple(config["cflags"]),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "k_induction_max": self.k_induction_max,
            "unwind_full": self.unwind_full,
            "bounded_unwind": self.bounded_unwind,
            "sat_conflicts": self.sat_conflicts,
            "fuzz_iterations": self.fuzz_iterations,
            "fuzz_seed": self.fuzz_seed,
            "fuzz_max_draws": self.fuzz_max_draws,
            "cflags": list(self.cflags),
        }


# --- the code around an artifact ---------------------------------------------------------------


@dataclass
class Context:
    """Everything a checker step needs besides the artifact: interface, includes, limits, workdir."""

    project: Path
    interface: dict[str, Any]
    include_dirs: list[Path]
    limits: Limits
    workdir: Path
    impl_files: list[Path] = field(default_factory=list)

    def header_paths(self) -> list[Path]:
        return [(self.project / h).resolve() for h in self.interface["headers"]]


def prelude(ctx: Context) -> str:
    """Included before every piece of generated code: assert, the headers, the SV-COMP interface."""
    lines = ["#include <assert.h>", "#include <stdbool.h>", "#include <stddef.h>", "#include <stdint.h>"]
    lines += [f'#include "{p.as_posix()}"' for p in ctx.header_paths()]
    lines += [f"{ctype} __VERIFIER_nondet_{name}(void);" for name, ctype in SV_COMP_NONDET]
    lines.append("void __VERIFIER_assume(int cond);")
    return "\n".join(lines) + "\n"


def include_flags(ctx: Context) -> list[str]:
    dirs = list(dict.fromkeys([*ctx.include_dirs, *(p.parent for p in ctx.header_paths())]))
    return [f"-I{d}" for d in dirs]


def write(ctx: Context, name: str, text: str) -> Path:
    path = ctx.workdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def havoc_stubs(ctx: Context) -> str:
    """A body for every interface function that does anything at all: the "any implementation".

    Pointer arguments to writable objects are overwritten with arbitrary values and the result is
    arbitrary. Under CBMC an uninitialised local is a nondeterministic value. Self-check uses these
    stubs to ask whether a property can fail at all (not vacuous) and whether its assertion can be
    reached, before any implementation exists (§3 流程 5).
    """
    out = []
    for fn in ctx.interface["functions"].values():
        params = fn["params"]
        names = [p["name"] or f"cv_p{i}" for i, p in enumerate(params)]
        signature = ", ".join(_declare(p["type"], n) for p, n in zip(params, names, strict=True)) or "void"
        body = []
        for p, n in zip(params, names, strict=True):
            pointee = _writable_pointee(p["type"])
            if pointee:
                body.append(f"    if ({n}) {{ {pointee} cv_h_{n}; *{n} = cv_h_{n}; }}")
        if fn["return"] != "void":
            body.append(f"    {fn['return']} cv_h_ret; return cv_h_ret;")
        out.append(f"{fn['return']} {fn['name']}({signature}) {{\n" + "\n".join(body) + "\n}")
    return "\n\n".join(out) + "\n"


def _declare(ctype: str, name: str) -> str:
    if "(*)" in ctype:
        return ctype.replace("(*)", f"(*{name})", 1)
    if ctype.endswith("]"):
        base, _, dims = ctype.partition("[")
        return f"{base.strip()} {name}[{dims}"
    return f"{ctype} {name}"


def _writable_pointee(ctype: str) -> str | None:
    text = ctype.strip()
    if "(" in text or not text.endswith("*"):
        return None
    pointee = text[:-1].strip()
    if not pointee or pointee.startswith("const ") or pointee.endswith(" const") or pointee in ("void", "char"):
        return None
    return pointee


# --- CBMC --------------------------------------------------------------------------------------


@dataclass
class CbmcOutcome:
    """``verdict`` is "success", "failure" (a property assertion fails), "unwind" (only unwinding
    assertions fail: inconclusive) or "unknown" (the tool gave no answer)."""

    verdict: str
    failures: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


def goto_compile(ctx: Context, sources: list[Path], out: Path, defines: list[str] = ()) -> str | None:
    argv = [tool_path("goto-cc"), *include_flags(ctx), *[f"-D{d}" for d in defines]]
    argv += [str(s) for s in sources] + ["-o", str(out)]
    proc = run_tool(argv, timeout=ctx.limits.safety_timeout, cwd=ctx.workdir)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or "goto-cc failed"
    return None


def has_loops(ctx: Context, goto: Path) -> bool:
    proc = run_tool([tool_path("cbmc"), str(goto), "--show-loops"], timeout=ctx.limits.safety_timeout)
    return "Loop " in proc.stdout


def _sat_options(ctx: Context) -> tuple[list[str], dict[str, str]]:
    if shutil.which("z3"):
        return ["--external-sat-solver", str(RUNTIME / "sat.sh")], {"CV_SAT_CONFLICTS": str(ctx.limits.sat_conflicts)}
    return [], {}


def cbmc(ctx: Context, goto: Path, *, unwind: int | None, unwinding_assertions: bool) -> CbmcOutcome:
    sat, env = _sat_options(ctx)
    argv = [tool_path("cbmc"), str(goto), "--json-ui", "--trace", *sat]
    if unwind is not None:
        argv += ["--unwind", str(unwind)]
        if unwinding_assertions:
            argv.append("--unwinding-assertions")
    proc = run_tool(argv, timeout=ctx.limits.safety_timeout, env=env)
    if proc.timed_out:
        return CbmcOutcome("unknown", reason=proc.stderr)
    try:
        messages = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return CbmcOutcome("unknown", reason=(proc.stderr or proc.stdout).strip()[-2000:] or "cbmc gave no output")
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            results.extend(message.get("result", []) or [])
            if message.get("messageType") == "ERROR":
                errors.append(message.get("messageText", ""))
    if not results:
        return CbmcOutcome("unknown", reason="; ".join(errors) or "cbmc reported no properties")
    failed = [r for r in results if r.get("status") == "FAILURE"]
    real = [r for r in failed if ".unwind." not in r.get("property", "")]
    if real:
        return CbmcOutcome("failure", failures=real)
    if failed:
        return CbmcOutcome("unwind", reason="unwinding assertion failed: the bound does not cover every path")
    if errors:
        return CbmcOutcome("unknown", reason="; ".join(errors))
    if any(r.get("status") != "SUCCESS" for r in results):
        return CbmcOutcome("unknown", reason="cbmc left properties undecided")
    return CbmcOutcome("success")


def k_induction(ctx: Context, goto: Path) -> tuple[str, CbmcOutcome | None]:
    """k-induction through goto-instrument (base case, then step case), k = 1..k_induction_max.

    Returns ("proof", None), ("failure", outcome of the failing base case) or ("open", None).
    """
    for k in range(1, ctx.limits.k_induction_max + 1):
        base = goto.with_suffix(f".base{k}.goto")
        step = goto.with_suffix(f".step{k}.goto")
        gi = tool_path("goto-instrument")
        for case, out in (("--base-case", base), ("--step-case", step)):
            proc = run_tool([gi, "--k-induction", str(k), case, str(goto), str(out)], timeout=ctx.limits.safety_timeout)
            if proc.returncode != 0:
                return "open", None
        base_outcome = cbmc(ctx, base, unwind=ctx.limits.unwind_full, unwinding_assertions=False)
        if base_outcome.verdict == "failure":
            return "failure", base_outcome
        if base_outcome.verdict != "success":
            continue
        step_outcome = cbmc(ctx, step, unwind=ctx.limits.unwind_full, unwinding_assertions=True)
        if step_outcome.verdict == "success":
            return "proof", None
    return "open", None


def counterexample_from_trace(failure: dict[str, Any], interface_names: set[str]) -> dict[str, Any]:
    """The counterexample as input: the interface calls the harness made, with their arguments.

    Only calls made directly by ``main`` (the harness) are shown; the artifact itself is not.
    """
    calls: list[dict[str, Any]] = []
    stack: list[str] = []
    pending: dict[str, Any] | None = None
    nondet: list[str] = []
    for step in failure.get("trace", []) or []:
        kind = step.get("stepType")
        if kind == "function-call":
            name = (step.get("function") or {}).get("displayName", "")
            caller = stack[-1] if stack else ""
            stack.append(name)
            pending = None
            if caller == "main" and name in interface_names:
                pending = {"function": name, "args": []}
                calls.append(pending)
        elif kind == "function-return":
            if stack:
                stack.pop()
            pending = None
        elif kind == "assignment":
            if step.get("assignmentType") == "actual-parameter" and pending is not None:
                pending["args"].append(f"{step.get('lhs')}={_value(step)}")
            if step.get("lhs", "").startswith("return_value___VERIFIER_nondet") and stack[-1:] == ["main"]:
                nondet.append(_value(step))
    for call in calls:
        call["text"] = f"{call['function']}({', '.join(call['args'])})"
    location = failure.get("trace", [{}])[-1].get("sourceLocation", {}) if failure.get("trace") else {}
    return {
        "calls": [c["text"] for c in calls],
        "nondet_values": nondet,
        "failed_check_line": location.get("line"),
    }


def _value(step: dict[str, Any]) -> str:
    value = step.get("value") or {}
    data = str(value.get("data", "?"))
    if value.get("name") == "pointer" or "!" in data:
        return "<pointer>"
    stripped = data.rstrip("lLuU")
    return stripped if stripped.lstrip("-").isdigit() else data


def native_compile(ctx: Context, sources: list[Path], out: Path, extra: list[str] = ()) -> str | None:
    argv = [tool_path("clang"), *ctx.limits.cflags, *include_flags(ctx), "-O0", "-g", "-w", *extra]
    argv += [str(s) for s in sources] + ["-o", str(out)]
    proc = run_tool(argv, timeout=ctx.limits.safety_timeout, cwd=ctx.workdir)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or "compilation failed"
    return None


def syntax_check(ctx: Context, source: Path) -> str | None:
    argv = [tool_path("clang"), *ctx.limits.cflags, *include_flags(ctx), "-fsyntax-only", str(source)]
    proc = run_tool(argv, timeout=ctx.limits.safety_timeout, cwd=ctx.workdir)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip() or "does not compile"
    return None
