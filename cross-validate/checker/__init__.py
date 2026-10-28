"""The checker: a program the judge calls (§3 组件). It reads artifacts and code, never asks a model.

``self_check`` judges an artifact on its own (§3 流程 5); ``compare`` judges the code against it
(§3 流程 6). Each comparison result says which method reached it, i.e. its strength.
"""

from __future__ import annotations

from typing import Any

import cv_protocol as P
from checker.common import CHECKER_VERSION, Context, Limits, ToolMissing
from checker.compose import property_file, reference_file
from checker.difftest import difftest
from checker.prove import prove
from checker.run_cases import run_cases
from checker.selfcheck import selfcheck
from checker.static_analysis import check_structure

__all__ = ["CHECKER_VERSION", "Context", "Limits", "ToolMissing", "compare", "self_check"]


def self_check(ctx: Context, artifact: dict[str, Any], examples: list[dict[str, Any]]) -> dict[str, Any]:
    return selfcheck(ctx, artifact, examples)


def compare(ctx: Context, artifact: dict[str, Any], implementation: dict[str, Any]) -> dict[str, Any]:
    form, kind = artifact["form"], artifact.get("kind")
    if form == P.FORM_PROPERTY and kind == "P4":
        return check_structure(artifact, implementation)
    if form == P.FORM_PROPERTY:
        return prove(ctx, property_file(ctx, artifact), artifact["id"])
    if form == P.FORM_REFERENCE:
        return difftest(ctx, reference_file(ctx, artifact), artifact["id"])
    return run_cases(ctx, artifact)
