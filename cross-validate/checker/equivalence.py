"""Comparing readings without looking at any implementation (§3 流程 4, 5; 算法1 第8、9、12行).

* ``equivalent(a, b)``: are two artifacts for the same segment the same reading? Different forms or
  property kinds are different readings. Otherwise the solver looks for an input on which their
  canonical predicates (or models, or cases) disagree; that input is the distinguishing input. When
  the two cannot be compared (no canonical predicates, different signatures) the answer is None,
  and the caller keeps both readings: that can only let a misreading pass, never reject correct
  code (约束 2 at the expense of 约束 1, as the design chooses for ambiguity).
* ``unsatisfiable(group)``: can the preconditions and postconditions of one function, or the
  invariants of one record type, all hold at once? ``minimal_conflict`` shrinks an unsatisfiable
  group to a minimal conflicting subset by deletion.

Every artifact is built in its own translation unit with its canonical names prefixed, so two
artifacts' definitions never collide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import cv_protocol as P
from checker.common import Context, cbmc, goto_compile, prelude, write
from checker.compose import CANONICAL_PREFIXES, spec_text
from cv_clang import ClangError, ast_dump


@dataclass(frozen=True)
class Canon:
    name: str
    ret: str
    params: tuple[str, ...]


def canonical(ctx: Context, artifact: dict[str, Any]) -> dict[str, Canon]:
    """The canonical predicates and models an artifact defines, with their signatures."""
    text = spec_text(artifact)
    if not text.strip():
        return {}
    path = write(ctx, f"{artifact['id']}.canon.c", prelude(ctx) + "\n" + text + "\n")
    try:
        tu = ast_dump(path, list(ctx.include_dirs) + [p.parent for p in ctx.header_paths()], list(ctx.limits.cflags))
    except ClangError:
        return {}
    found: dict[str, Canon] = {}
    for node in tu.get("inner", []):
        name = node.get("name", "")
        has_body = any(c.get("kind") == "CompoundStmt" for c in node.get("inner", []) or [])
        if node.get("kind") == "FunctionDecl" and has_body and name.startswith(CANONICAL_PREFIXES):
            params = tuple(p["type"]["qualType"] for p in node.get("inner", []) if p.get("kind") == "ParmVarDecl")
            found[name] = Canon(name, node["type"]["qualType"].split("(", 1)[0].strip(), params)
    return found


def _unit(ctx: Context, artifact: dict[str, Any], prefix: str, canon: dict[str, Canon]) -> Any:
    renames = "".join(f"#define {n} {prefix}{n}\n" for n in sorted(canon))
    return write(ctx, f"{artifact['id']}.{prefix}unit.c", renames + prelude(ctx) + "\n" + spec_text(artifact) + "\n")


def _declare(prefix: str, c: Canon) -> str:
    return f"{c.ret} {prefix}{c.name}({', '.join(c.params) or 'void'});"


def _locals(params: tuple[str, ...], tag: str) -> tuple[list[str], list[str]]:
    """Nondet locals for each parameter type; pointer parameters get the address of a nondet object."""
    decls, args = [], []
    for i, t in enumerate(params):
        name = f"cv_{tag}{i}"
        if t.rstrip().endswith("*") and "(" not in t:
            pointee = t.rstrip()[:-1].strip().removeprefix("const ").strip()
            decls.append(f"    {pointee} {name};")
            args.append(f"&{name}")
        else:
            decls.append(f"    {t} {name};")
            args.append(name)
    return decls, args


def _distinguishing(ctx: Context, shown: dict[str, tuple[str, list[str]]], values: dict[str, Any]) -> str:
    """The distinguishing input as a call of the interface function: ``discount(200)``, result 180."""
    parts = []
    for fn, args in shown.values():
        got = [values.get(a.lstrip("&"), "?") for a in args]
        if all(g == "?" for g in got):
            continue
        arity = len(ctx.interface["functions"].get(fn, {}).get("params", got))
        text = f"{fn}({', '.join(got[:arity])})"
        if len(got) > arity:
            text += f"，结果为 {', '.join(got[arity:])} 时"
        parts.append(text)
    return "; ".join(parts)


def _trace_values(failure: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for step in failure.get("trace", []) or []:
        if step.get("stepType") == "assignment" and str(step.get("lhs", "")).startswith("cv_"):
            value = step.get("value") or {}
            if value.get("name") == "struct":
                data = {m.get("name"): (m.get("value") or {}).get("data") for m in value.get("members", [])}
                values[step["lhs"]] = json.dumps(data, ensure_ascii=False)
            else:
                values[step["lhs"]] = _number(str(value.get("data", "?")))
    return values


def _number(text: str) -> str:
    """CBMC writes integer literals with C suffixes (``41964l``, ``7ul``); show the bare number."""
    stripped = text.rstrip("lLuU")
    return stripped if stripped.lstrip("-").isdigit() else text


def _solve(ctx: Context, name: str, units: list[Any], main: str) -> tuple[str, dict[str, Any]]:
    main_path = write(ctx, f"{name}.main.c", prelude(ctx) + main)
    goto = ctx.workdir / f"{name}.goto"
    if goto_compile(ctx, [main_path, *units], goto):
        return "unknown", {}
    outcome = cbmc(ctx, goto, unwind=ctx.limits.unwind_full, unwinding_assertions=True)
    if outcome.verdict == "failure":
        return "failure", _trace_values(outcome.failures[0])
    return outcome.verdict, {}


def equivalent(ctx: Context, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    if a["form"] != b["form"] or a.get("kind") != b.get("kind"):
        return {"equivalent": False, "distinguishing": None, "reason": "different forms or property kinds"}
    if a["form"] == P.FORM_CASES:
        return _equivalent_cases(a, b)
    if a.get("kind") == "P4":
        same = a["content"].get("target") == b["content"].get("target") and sorted(
            a["content"].get("allowed_writers", [])
        ) == sorted(b["content"].get("allowed_writers", []))
        diff = sorted(set(a["content"].get("allowed_writers", [])) ^ set(b["content"].get("allowed_writers", [])))
        return {
            "equivalent": same,
            "distinguishing": None if same else f"a write by {', '.join(diff) or 'a function'}",
            "reason": "" if same else "different targets or writers",
        }
    ca, cb = canonical(ctx, a), canonical(ctx, b)
    judged = {n for n in set(ca) | set(cb) if not n.startswith("cv_pre_")}
    if not judged or any(n not in ca or n not in cb or ca[n].params != cb[n].params for n in judged):
        return {"equivalent": None, "distinguishing": None, "reason": "no comparable canonical predicates"}
    units = [_unit(ctx, a, "cvA_", ca), _unit(ctx, b, "cvB_", cb)]
    decls = [_declare("cvA_", c) for c in ca.values()] + [_declare("cvB_", c) for c in cb.values()]
    body: list[str] = []
    shown_args: dict[str, tuple[str, list[str]]] = {}
    for n in sorted(judged):
        fn = n.split("_", 2)[2]
        pre = f"cv_pre_{fn}"
        locals_, args = _locals(ca[n].params, f"{len(shown_args)}_")
        shown_args[n] = (fn, args)
        body += ["  {", *locals_]
        pre_params = ca.get(pre, cb.get(pre))
        pre_args = args[: len(pre_params.params)] if pre_params else []
        pa = f"!!cvA_{pre}({', '.join(pre_args)})" if pre in ca else "1"
        pb = f"!!cvB_{pre}({', '.join(pre_args)})" if pre in cb else "1"
        if pre in ca or pre in cb:
            body.append(f"    assert({pa} == {pb});")
        lhs, rhs = f"cvA_{n}({', '.join(args)})", f"cvB_{n}({', '.join(args)})"
        if not n.startswith("cv_ref_"):
            lhs, rhs = f"!!{lhs}", f"!!{rhs}"
        body.append(f"    if ({pa} && {pb}) assert({lhs} == {rhs});")
        body.append("  }")
    main = "\n" + "\n".join(decls) + "\nint main(void) {\n" + "\n".join(body) + "\n  return 0;\n}\n"
    verdict, values = _solve(ctx, f"eq_{a['id']}_{b['id']}", units, main)
    if verdict == "success":
        return {"equivalent": True, "distinguishing": None, "reason": "the solver found no distinguishing input"}
    if verdict == "failure":
        return {
            "equivalent": False,
            "distinguishing": _distinguishing(ctx, shown_args, values) or "an input exists",
            "reason": "readings disagree",
        }
    return {"equivalent": None, "distinguishing": None, "reason": f"solver gave no answer ({verdict})"}


def _equivalent_cases(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def key(c: dict[str, Any]) -> str:
        return " ".join((c.get("setup", "") + " " + c["call"]).split())

    expect_a = {key(c): c["expect"].strip() for c in a["content"].get("cases", [])}
    for c in b["content"].get("cases", []):
        k = key(c)
        if k in expect_a and expect_a[k] != c["expect"].strip():
            return {
                "equivalent": False,
                "distinguishing": c["call"],
                "reason": f"{c['call']}: {expect_a[k]} vs {c['expect'].strip()}",
            }
    return {"equivalent": True, "distinguishing": None, "reason": "no input on which the cases disagree"}


# --- conflicts ---------------------------------------------------------------------------------


def group_key(name: str) -> tuple[str, str] | None:
    for prefix, kind in (("cv_pre_", "fn"), ("cv_post_", "fn"), ("cv_inv_", "inv")):
        if name.startswith(prefix):
            return kind, name[len(prefix) :]
    return None


def unsatisfiable(
    ctx: Context, members: list[tuple[dict[str, Any], dict[str, Canon]]], key: tuple[str, str]
) -> bool | None:
    """Can every predicate of *key* across *members* hold together? True means they cannot."""
    kind, subject = key
    names = [f"cv_pre_{subject}", f"cv_post_{subject}"] if kind == "fn" else [f"cv_inv_{subject}"]
    signatures = {n: c.params for _, canon in members for n, c in canon.items() if n in names}
    shared: dict[str, tuple[str, ...]] = {}
    for _, canon in members:
        for n in names:
            if n in canon:
                if n in shared and shared[n] != canon[n].params:
                    return None
                shared[n] = canon[n].params
    if not signatures:
        return None
    longest = max(shared.values(), key=len)
    locals_, args = _locals(longest, "x")
    units, decls, conds = [], [], []
    for i, (artifact, canon) in enumerate(members):
        prefix = f"cv{i}_"
        units.append(_unit(ctx, artifact, prefix, canon))
        decls += [_declare(prefix, c) for c in canon.values()]
        conds.extend(f"{prefix}{n}({', '.join(args[: len(canon[n].params)])})" for n in names if n in canon)
    main = (
        "\n"
        + "\n".join(decls)
        + "\nint main(void) {\n"
        + "\n".join(locals_)
        + f"\n    __VERIFIER_assume({' && '.join(conds)});\n    assert(0);\n    return 0;\n}}\n"
    )
    ids = "_".join(a["id"] for a, _ in members)
    verdict, _ = _solve(ctx, f"sat_{subject}_{abs(hash(ids)) % 10**8}", units, main)
    if verdict == "success":
        return True
    if verdict == "failure":
        return False
    return None


def minimal_conflict(
    ctx: Context, members: list[tuple[dict[str, Any], dict[str, Canon]]], key: tuple[str, str]
) -> list[dict[str, Any]]:
    """Deletion-based shrinking of an unsatisfiable group to a minimal conflicting subset."""
    core = list(members)
    for member in list(core):
        trial = [m for m in core if m is not member]
        if len(trial) >= 1 and unsatisfiable(ctx, trial, key) is True:
            core = trial
    return [a for a, _ in core]
