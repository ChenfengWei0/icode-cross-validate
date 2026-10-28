"""What the judge and the checker learn from C sources, read through clang's JSON AST.

Two readers:

* ``read_interface``: the interface functions and record types declared in the header files. The
  generator may only name these functions (§3 流程 2), and a change to one of them is what makes the
  judge plan again (算法1 第17行).
* ``read_implementation``: the functions an implementation defines, their bodies, whom they call and
  which record fields they write. The bodies key the comparison cache (§3 集成); the writes are what
  structural constraints (P4) are judged on.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from checker.common import run_tool, tool_path

WRITE_FUNCTIONS = frozenset({"memcpy", "memmove", "memset", "bzero", "strcpy", "strncpy", "strcat", "strncat"})


class ClangError(RuntimeError):
    pass


def ast_dump(source: Path, include_dirs: list[Path], cflags: list[str], *, language: str = "c") -> dict[str, Any]:
    argv = [tool_path("clang"), "-x", language, *cflags]
    argv += [f"-I{d}" for d in include_dirs]
    argv += ["-Xclang", "-ast-dump=json", "-fsyntax-only", str(source)]
    proc = run_tool(argv, timeout=120)
    if proc.returncode != 0:
        raise ClangError(f"clang could not parse {source}:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


class _Locations:
    """clang prints a file or line only when it changes, in the order loc, range.begin, range.end."""

    def __init__(self) -> None:
        self.file = ""
        self.line = 0

    def take(self, where: dict[str, Any] | None) -> tuple[str, int]:
        if not where:
            return self.file, self.line
        for part in (where.get("spellingLoc"), where.get("expansionLoc"), where):
            if isinstance(part, dict):
                if "file" in part:
                    self.file = part["file"]
                if "line" in part:
                    self.line = part["line"]
        return self.file, self.line


def _walk(node: dict[str, Any], locations: _Locations, visit) -> None:
    file, line = locations.take(node.get("loc"))
    rng = node.get("range") or {}
    begin = locations.take(rng.get("begin"))
    locations.take(rng.get("end"))
    visit(node, file or begin[0], line or begin[1])
    for child in node.get("inner", []) or []:
        _walk(child, locations, visit)


def _same_file(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def _slice(text_bytes: bytes, rng: dict[str, Any]) -> str:
    begin = (rng.get("begin") or {}).get("offset")
    end = rng.get("end") or {}
    if begin is None or end.get("offset") is None:
        return ""
    return text_bytes[begin : end["offset"] + end.get("tokLen", 0)].decode("utf-8", errors="replace")


# --- interface ---------------------------------------------------------------------------------


def _record_names(tu: dict[str, Any]) -> dict[str, set[str]]:
    """RecordDecl id -> every name it is known by (its tag and the typedefs that name it)."""
    names: dict[str, set[str]] = {}
    for node in tu.get("inner", []):
        if node.get("kind") == "RecordDecl" and node.get("id"):
            names.setdefault(node["id"], set())
            if node.get("name"):
                names[node["id"]].add(node["name"])
    for node in tu.get("inner", []):
        if node.get("kind") != "TypedefDecl":
            continue
        for ref in _decl_refs(node):
            if ref in names:
                names[ref].add(node["name"])
    return names


def _decl_refs(node: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(node, dict):
        refs.extend(
            node[key]["id"]
            for key in ("decl", "ownedTagDecl")
            if isinstance(node.get(key), dict) and node[key].get("id")
        )
        for value in node.values():
            if isinstance(value, (dict, list)):
                refs.extend(_decl_refs(value))
    elif isinstance(node, list):
        for value in node:
            refs.extend(_decl_refs(value))
    return refs


def read_interface(project: Path, headers: list[str], include_dirs: list[str], cflags: list[str]) -> dict[str, Any]:
    """Functions and records declared in *headers*, with digests that tell a changed declaration."""
    header_paths = [(project / h).resolve() for h in headers]
    inc = [(project / d).resolve() for d in include_dirs] + sorted({p.parent for p in header_paths})
    functions: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, Any]] = {}
    texts: dict[str, str] = {}
    definitions: list[str] = []
    for header in header_paths:
        if not header.is_file():
            raise ClangError(f"header {header} does not exist")
        texts[_rel(project, header)] = header.read_text(encoding="utf-8")
        tu = ast_dump(header, inc, cflags)
        record_names = _record_names(tu)
        sources: dict[str, bytes] = {}
        locations = _Locations()
        for node in tu.get("inner", []):
            file, _ = locations.take(node.get("loc"))
            rng = node.get("range") or {}
            begin_file, _ = locations.take(rng.get("begin"))
            locations.take(rng.get("end"))
            file = file or begin_file
            if node.get("isImplicit") or not any(_same_file(file, h) for h in header_paths):
                continue
            rel = _rel(project, Path(file))
            if rel not in sources:
                sources[rel] = Path(file).read_bytes()
            if node.get("kind") == "FunctionDecl" and any(
                c.get("kind") == "CompoundStmt" for c in node.get("inner", []) or []
            ):
                definitions.append(node["name"])  # a body in a header is implementation the generator would see
            if node.get("kind") == "FunctionDecl" and node.get("name") not in functions:
                params = [
                    {"name": p.get("name", ""), "type": p["type"]["qualType"]}
                    for p in node.get("inner", [])
                    if p.get("kind") == "ParmVarDecl"
                ]
                qual = node["type"]["qualType"]
                text = " ".join(_slice(sources[rel], rng).split())
                functions[node["name"]] = {
                    "name": node["name"],
                    "return": qual.split("(", 1)[0].strip(),
                    "params": params,
                    "decl": text,
                    "header": rel,
                    "digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            elif node.get("kind") == "RecordDecl" and node.get("completeDefinition"):
                fields = [
                    {"name": f.get("name", ""), "type": f["type"]["qualType"]}
                    for f in node.get("inner", [])
                    if f.get("kind") == "FieldDecl"
                ]
                for name in sorted(record_names.get(node["id"], set())):
                    records[name] = {"name": name, "fields": fields, "header": rel}
    types_text = canonical_types(texts, functions)
    return {
        "headers": sorted(texts),
        "header_texts": texts,
        "functions": functions,
        "records": records,
        "definitions": sorted(set(definitions)),
        "types_digest": hashlib.sha256(types_text.encode("utf-8")).hexdigest(),
    }


def canonical_types(texts: dict[str, str], functions: dict[str, dict[str, Any]]) -> str:
    """Header text with the function declarations taken out: what is left are the type definitions."""
    out = []
    for name in sorted(texts):
        text = " ".join(texts[name].split())
        for fn in functions.values():
            text = text.replace(fn["decl"], "")
        out.append(f"{name}\n{text}")
    return "\n".join(out)


def _rel(project: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


# --- implementation ----------------------------------------------------------------------------


def _strip(node: dict[str, Any]) -> dict[str, Any]:
    while node.get("kind") in ("ImplicitCastExpr", "ParenExpr", "CStyleCastExpr") and node.get("inner"):
        node = node["inner"][0]
    return node


def _is_record_type(qual: str, record_names: dict[str, set[str]]) -> set[str]:
    hits: set[str] = set()
    words = qual.replace("*", " ").replace("const", " ").replace("struct", " ").split()
    for names in record_names.values():
        if names & set(words):
            hits |= names
    return hits


def read_implementation(
    project: Path, files: list[str], include_dirs: list[str], cflags: list[str], interface: dict[str, Any]
) -> dict[str, Any]:
    """Functions defined in the delivered C files: body, callees, record-field writes."""
    inc = [(project / d).resolve() for d in include_dirs]
    inc += sorted({(project / h).resolve().parent for h in interface.get("headers", [])})
    functions: dict[str, dict[str, Any]] = {}
    for rel in files:
        path = (project / rel).resolve()
        if path.suffix not in (".c", ".cc", ".cpp", ".cxx"):
            continue
        language = "c" if path.suffix == ".c" else "c++"
        tu = ast_dump(path, inc, cflags, language=language)
        record_names = _record_names(tu)
        fields: dict[str, tuple[set[str], str]] = {}
        for node in tu.get("inner", []):
            if node.get("kind") == "RecordDecl":
                for field in node.get("inner", []):
                    if field.get("kind") == "FieldDecl":
                        fields[field["id"]] = (record_names.get(node["id"], set()), field.get("name", ""))
        source = path.read_bytes()
        locations = _Locations()
        for node in tu.get("inner", []):
            file, _ = locations.take(node.get("loc"))
            rng = node.get("range") or {}
            begin_file, _ = locations.take(rng.get("begin"))
            locations.take(rng.get("end"))
            file = file or begin_file
            body = next((c for c in node.get("inner", []) or [] if c.get("kind") == "CompoundStmt"), None)
            if node.get("kind") != "FunctionDecl" or body is None or not _same_file(file, path):
                continue
            callees: set[str] = set()
            writes: list[dict[str, Any]] = []

            def visit(n, f, line, *, callees=callees, writes=writes, fields=fields, record_names=record_names):
                kind = n.get("kind")
                if kind == "DeclRefExpr":
                    ref = n.get("referencedDecl") or {}
                    if ref.get("kind") == "FunctionDecl":
                        callees.add(ref.get("name", ""))
                target = None
                assigns = (kind == "BinaryOperator" and n.get("opcode") == "=") or kind == "CompoundAssignOperator"
                if assigns or (kind == "UnaryOperator" and n.get("opcode") in ("++", "--", "&")):
                    target = n["inner"][0]
                if target is not None:
                    target = _strip(target)
                    if target.get("kind") == "MemberExpr":
                        rec, fname = fields.get(target.get("referencedMemberDecl", ""), (set(), target.get("name", "")))
                        writes.append({"records": sorted(rec), "field": fname, "line": line, "how": n.get("opcode")})
                    elif kind != "UnaryOperator" or n.get("opcode") != "&":
                        rec = _is_record_type((target.get("type") or {}).get("qualType", ""), record_names)
                        if rec and "*" not in (target.get("type") or {}).get("qualType", ""):
                            writes.append({"records": sorted(rec), "field": "*", "line": line, "how": "whole"})
                if kind in ("CStyleCastExpr", "ExplicitCastExpr") and n.get("castKind") == "BitCast" and n.get("inner"):
                    # (long long *)acc: a record pointer turned into another pointer can write any field
                    before = (_strip(n["inner"][0]).get("type") or {}).get("qualType", "")
                    rec = _is_record_type(before, record_names) if "*" in before else set()
                    if rec:
                        writes.append({"records": sorted(rec), "field": "*", "line": line, "how": "cast"})
                if kind == "CallExpr" and n.get("inner"):
                    callee = _strip(n["inner"][0])
                    name = (callee.get("referencedDecl") or {}).get("name", "")
                    if name in WRITE_FUNCTIONS and len(n["inner"]) > 1:
                        qual = (_strip(n["inner"][1]).get("type") or {}).get("qualType", "")
                        rec = _is_record_type(qual, record_names)
                        if rec:
                            writes.append({"records": sorted(rec), "field": "*", "line": line, "how": name})

            sub = _Locations()
            sub.file, sub.line = locations.file, locations.line
            _walk(body, sub, visit)
            text = _slice(source, rng)
            functions[node["name"]] = {
                "name": node["name"],
                "file": rel,
                "static": node.get("storageClass") == "static",
                "body": text,
                "callees": sorted(callees - {node["name"]}),
                "writes": writes,
            }
    return {"functions": functions}


__all__ = [
    "ClangError",
    "ast_dump",
    "read_implementation",
    "read_interface",
]
