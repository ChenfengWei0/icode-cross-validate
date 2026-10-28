"""Structural constraints (P4) against the implementation, by static analysis (§3 流程 3, 6).

A P4 artifact names a record field and the interface functions allowed to write it, for example
"the balance field is written only by the payment module's functions". The checker reads every
delivered C file through clang and reports each write outside the allowed set.

What counts as a write: assignment and compound assignment to the field, ``++``/``--``, taking its
address (the pointer could be written through), assigning the whole record, and memcpy-like calls on
the record. Internal ``static`` helpers in the same file as an allowed function belong to that
function's module and may write too (the generator only sees headers, so it cannot name them).
Writes through pointers obtained some other way, or through casts, are not seen.
"""

from __future__ import annotations

from typing import Any

import cv_protocol as P


def check_structure(artifact: dict[str, Any], implementation: dict[str, Any]) -> dict[str, Any]:
    content = artifact["content"]
    record = content["target"]["record"]
    field = content["target"]["field"]
    allowed = set(content["allowed_writers"])
    functions = implementation["functions"]
    module_files = {f["file"] for name, f in functions.items() if name in allowed}
    permitted = allowed | {name for name, f in functions.items() if f["static"] and f["file"] in module_files}
    violations = []
    for name, fn in sorted(functions.items()):
        if name in permitted:
            continue
        violations.extend(
            {"function": name, "file": fn["file"], "line": write["line"], "how": write["how"] or "write"}
            for write in fn["writes"]
            if record in write["records"] and write["field"] in (field, "*")
        )
    if violations:
        calls = [f"{v['function']} writes {record}.{field} ({v['how']}) at {v['file']}:{v['line']}" for v in violations]
        return P.check_result(P.RESULT_FAIL, counterexample={"calls": calls, "method": "static analysis"})
    return P.check_result(
        P.RESULT_PASS, strength="static", reason=f"no write to {record}.{field} outside {sorted(allowed)}"
    )
