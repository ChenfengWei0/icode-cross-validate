"""Messages of the cross-validation protocol (design doc §3 "协议").

Every message that crosses a component boundary is built or validated here, so the fields the
design names are written down once. Messages are plain JSON values: they ride on
``WorkflowValue.data`` and are stored under ``.cv/``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# --- 需求条目集 (requirement item set) --------------------------------------------------------

STATUS_NORMAL = "normal"
STATUS_EMPTY = "empty"
STATUS_PARTIAL = "partial"  # 部分未处理
ITEM_SET_STATUSES = (STATUS_NORMAL, STATUS_EMPTY, STATUS_PARTIAL)

# --- 子任务产物 (subtask artifact) -------------------------------------------------------------

FORM_PROPERTY = "property"  # 形式化性质
FORM_REFERENCE = "reference_model"  # 简化参考模型
FORM_CASES = "cases"  # 具体用例
FORM_UNCOVERED = "uncovered"  # 三种都不可行
FORMS = (FORM_PROPERTY, FORM_REFERENCE, FORM_CASES)
PROPERTY_KINDS = ("P1", "P2", "P3", "P4")
SOURCES = ("A", "B")

# --- 检查结果与强度 (check results and their strength) ---------------------------------------

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_UNKNOWN = "unknown"

# Weakest first. A result is only as strong as the method that reached it (§3 流程 6).
STRENGTHS = ("cases", "fuzz", "difftest", "bounded", "static", "proof")
STRENGTH_RANK = {"cases": 0, "fuzz": 1, "difftest": 1, "bounded": 2, "static": 2, "proof": 3}

# --- 段状态 (segment status, 算法1 第11-12行) ----------------------------------------------------

SEG_UNCOVERED = "uncovered"  # 未覆盖
SEG_DETERMINED = "determined"  # 确定
SEG_AMBIGUOUS = "ambiguous"  # 有歧义
SEG_CONFLICT = "conflict"  # 冲突

# --- 信号 (signals) ---------------------------------------------------------------------------

SIGNAL_PASS = "pass"  # 放行
SIGNAL_BLOCK = "block"  # 阻止
SIGNAL_PAUSE = "pause"  # 暂停
SIGNAL_REJECT = "reject"  # 拒绝
SIGNALS = (SIGNAL_PASS, SIGNAL_BLOCK, SIGNAL_PAUSE, SIGNAL_REJECT)
SIGNAL_NAMES_ZH = {SIGNAL_PASS: "放行", SIGNAL_BLOCK: "阻止", SIGNAL_PAUSE: "暂停", SIGNAL_REJECT: "拒绝"}


class ProtocolError(ValueError):
    """A message does not have the shape the protocol prescribes."""


def canonical_json(value: Any) -> str:
    """The one serialisation that hashes and cache keys are computed from."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def _string_list(value: Any, where: str) -> list[str]:
    _require(isinstance(value, list) and all(isinstance(x, str) for x in value), f"{where} must be a list of strings")
    return list(value)


# --- 需求条目集 --------------------------------------------------------------------------------


def validate_item_set(raw: Any) -> dict[str, Any]:
    """Validate a requirement item set and return it normalised (§3 协议, row 1).

    The item set is produced upstream (需求处理); this function only checks its shape. ``id`` is the
    item-set number the completion declaration refers to; when absent it is the digest of the items.
    """
    _require(isinstance(raw, dict), "requirement item set must be a JSON object")
    items_raw = raw.get("items", [])
    _require(isinstance(items_raw, list), "items must be a list")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items_raw):
        _require(isinstance(item, dict), f"items[{index}] must be an object")
        item_id = item.get("id")
        _require(isinstance(item_id, str) and item_id.strip() != "", f"items[{index}].id must be a non-empty string")
        _require(item_id not in seen, f"duplicate item id {item_id!r}")
        seen.add(item_id)
        text = item.get("text")
        _require(isinstance(text, str) and text.strip() != "", f"item {item_id}: text must be a non-empty string")
        examples = _string_list(item.get("examples", []), f"item {item_id}: examples")
        source = item.get("source", "")
        _require(isinstance(source, str), f"item {item_id}: source must be a string")
        items.append({"id": item_id, "text": text, "examples": examples, "source": source})
    unprocessed = _string_list(raw.get("unprocessed", []), "unprocessed")
    status = raw.get("status") or (STATUS_EMPTY if not items else STATUS_PARTIAL if unprocessed else STATUS_NORMAL)
    _require(status in ITEM_SET_STATUSES, f"status must be one of {ITEM_SET_STATUSES}")
    _require(status != STATUS_EMPTY or not items, "an empty item set cannot carry items")
    set_id = raw.get("id") or "Q-" + digest(items)[:12]
    _require(isinstance(set_id, str), "id must be a string")
    return {"id": set_id, "status": status, "items": items, "unprocessed": unprocessed}


def passthrough_item_set(text: str, source: str) -> dict[str, Any]:
    """Wrap free text as a one-item set without interpreting it.

    Requirement processing is upstream of cross validation (§3 组件); this is not that step. The
    whole text becomes item R1, unchanged, and the report says so.
    """
    stripped = text.strip()
    if not stripped:
        return validate_item_set({"status": STATUS_EMPTY, "items": []})
    return validate_item_set({"items": [{"id": "R1", "text": stripped, "examples": [], "source": source}]})


# --- 接口 --------------------------------------------------------------------------------------


def validate_interface(raw: Any) -> dict[str, Any]:
    """``.cv/interface.json`` written by the main agent: the header files, fixed before implementation."""
    _require(isinstance(raw, dict), "interface declaration must be a JSON object")
    headers = _string_list(raw.get("headers"), "headers")
    _require(len(headers) > 0, "headers must name at least one header file")
    _require(all(h.endswith((".h", ".hpp", ".hh")) for h in headers), "headers must be .h/.hpp/.hh files")
    include_dirs = _string_list(raw.get("include_dirs", []), "include_dirs")
    return {"headers": headers, "include_dirs": include_dirs}


# --- 完成声明 ----------------------------------------------------------------------------------


def validate_manifest(raw: Any, expected_item_set_id: str) -> dict[str, Any]:
    """A completion declaration (§3 协议, row 5): the item-set number and the delivered files."""
    _require(isinstance(raw, dict), "completion manifest must be a JSON object")
    set_id = raw.get("requirements_id")
    _require(isinstance(set_id, str), "requirements_id must be a string")
    _require(
        set_id == expected_item_set_id,
        f"requirements_id {set_id!r} does not name the current requirement item set {expected_item_set_id!r}",
    )
    files = _string_list(raw.get("files"), "files")
    _require(len(files) > 0, "files must list at least one delivered file")
    return {"requirements_id": set_id, "files": files}


# --- 子任务产物 --------------------------------------------------------------------------------

_ARTIFACT_FIELDS = ("id", "item", "segment", "span", "functions", "form", "reading", "source", "content")


def make_artifact(
    *,
    item: str,
    segment: str,
    span: str,
    functions: list[str],
    form: str,
    kind: str | None,
    reading: str,
    source: str,
    content: dict[str, Any],
    ordinal: int,
) -> dict[str, Any]:
    """Build a subtask artifact with every field §3 协议 row 4 lists."""
    _require(form in FORMS, f"form must be one of {FORMS}")
    _require(source in SOURCES, f"source must be one of {SOURCES}")
    if form == FORM_PROPERTY:
        _require(kind in PROPERTY_KINDS, f"a property needs kind in {PROPERTY_KINDS}")
    artifact_id = f"{item}.{segment}.{source}{ordinal}"
    return {
        "id": artifact_id,
        "item": item,
        "segment": segment,
        "span": span,
        "functions": sorted(set(functions)),
        "form": form,
        "kind": kind if form == FORM_PROPERTY else None,
        "reading": reading,
        "source": source,
        "content": content,
    }


def validate_artifact(raw: Any) -> dict[str, Any]:
    _require(isinstance(raw, dict), "artifact must be an object")
    for name in _ARTIFACT_FIELDS:
        _require(name in raw, f"artifact is missing {name!r}")
    _require(raw["form"] in FORMS, f"artifact {raw['id']}: unknown form {raw['form']!r}")
    _require(isinstance(raw["content"], dict), f"artifact {raw['id']}: content must be an object")
    return raw


# --- 检查结果 ----------------------------------------------------------------------------------


def check_result(
    status: str,
    *,
    strength: str | None = None,
    counterexample: dict[str, Any] | None = None,
    reason: str = "",
    reach: bool | None = None,
) -> dict[str, Any]:
    """One artifact's comparison result (§3 协议, row 6)."""
    _require(status in (RESULT_PASS, RESULT_FAIL, RESULT_UNKNOWN), f"bad result status {status!r}")
    if status == RESULT_PASS:
        _require(strength in STRENGTHS, "a pass must state its strength")
    if status == RESULT_FAIL:
        _require(counterexample is not None, "a failure must carry a counterexample")
    return {
        "status": status,
        "strength": strength,
        "counterexample": counterexample,
        "reason": reason,
        "reach": reach,
    }


def verdict(signal: str, *, feedback: str = "", report: dict[str, Any] | None = None) -> dict[str, Any]:
    """The judge's decision (§3 协议, row 7). Built only from check results, never by a model."""
    _require(signal in SIGNALS, f"bad signal {signal!r}")
    return {"signal": signal, "feedback": feedback, "report": report or {}}
