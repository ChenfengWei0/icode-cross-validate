"""The judge (判定器): a program that runs cross validation (design doc §3, 算法 1).

Plan phase (after the requirement item set and the interface are fixed, before implementation):
``plan_prepare`` → ``segment_prompt`` → ``plan_requests`` → ``generation_prompt`` (sources A and B)
→ ``plan_check`` → ``rewrite_prompt`` → ``plan_freeze``. The generator is a model; the judge only
writes its prompts and reads its answers. Nothing the generator sees comes from an implementation.

Completion phase (after the main agent declares completion): ``verify`` (算法1 第14-17行), then, if
the header changed, the plan phase again for the affected items, then ``compare`` (第18-33行).

Comparison, aggregation and signals are computed here from checker results, never by a model.

Every public function takes the run state (a JSON dict that rides on ``WorkflowValue.data``) and
returns the next one. The authoritative records live outside the project (``cv_store``).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import string
import sys
from pathlib import Path
from typing import Any

import cv_protocol as P
from checker import CHECKER_VERSION, Context, Limits, ToolMissing, self_check
from checker import compare as checker_compare
from checker.equivalence import canonical, equivalent, group_key, minimal_conflict, unsatisfiable
from checker.selfcheck import example_checks
from cv_clang import ClangError, read_implementation, read_interface
from cv_store import Store, file_digest

HERE = Path(__file__).resolve().parent
JUDGE_VERSION = "1.0.0"
CV = ".cv"

DEFAULT_CONFIG: dict[str, Any] = {
    # Read when the workflow is built (they shape the graph): only from the plugin's config.json.
    "main_profile": "Code",
    "main_model": None,
    "generator_profile": "cv-generator",
    "generator_a_model": None,
    "generator_b_model": None,
    "max_rounds": 12,
    # Read at run time: the plugin's config.json, then the project's .cv/config.json (§3 集成).
    "retry_limit": 3,
    "k_induction_max": 2,
    "unwind_full": 16,
    "bounded_unwind": 6,
    "sat_conflicts": 2_000_000,
    "fuzz_iterations": 1000,
    "fuzz_seed": 1,
    "fuzz_max_draws": 64,
    "min_strength": "cases",
    "require_disambiguation": False,
    "unknown_blocks": False,
    "cflags": ["-std=c11"],
    "safety_timeout_seconds": 300,
}
GENERATOR_PROMPTS = ("segment.md", "generate.md", "rewrite.md", "conventions.md")
BUILD_KEYS = ("main_profile", "main_model", "generator_profile", "generator_a_model", "generator_b_model", "max_rounds")


# --- configuration -----------------------------------------------------------------------------


def load_config(project: Path | None = None) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    plugin = HERE / "config.json"
    if plugin.is_file():
        config.update(json.loads(plugin.read_text(encoding="utf-8")))
    if project is not None:
        local = project / CV / "config.json"
        if local.is_file():
            overrides = json.loads(local.read_text(encoding="utf-8"))
            config.update({k: v for k, v in overrides.items() if k not in BUILD_KEYS})
    unknown = sorted(set(config) - set(DEFAULT_CONFIG))
    if unknown:
        raise ValueError(f"unknown configuration keys: {unknown}")
    if config["min_strength"] not in P.STRENGTHS:
        raise ValueError(f"min_strength must be one of {P.STRENGTHS}")
    for key in ("retry_limit", "max_rounds", "k_induction_max", "unwind_full", "bounded_unwind", "fuzz_iterations"):
        if not isinstance(config[key], int) or config[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    return config


def generator_version(config: dict[str, Any]) -> str:
    """生成器与提示的版本: the generator profile and models, the prompt texts, the judge version."""
    prompts = {name: (HERE / "prompts" / name).read_text(encoding="utf-8") for name in GENERATOR_PROMPTS}
    return P.digest(
        {
            "profile": config["generator_profile"],
            "models": [config["generator_a_model"], config["generator_b_model"]],
            "prompts": prompts,
            "judge": JUDGE_VERSION,
        }
    )


# --- helpers -----------------------------------------------------------------------------------


def _project(state: dict[str, Any]) -> Path:
    return Path(state["project"])


def _store(state: dict[str, Any]) -> Store:
    return Store(_project(state))


def _context(store: Store, config: dict[str, Any], interface: dict[str, Any], workdir: Path) -> Context:
    decl = interface["decl"]
    return Context(
        project=store.project,
        interface=interface["snapshot"],
        include_dirs=[(store.project / d).resolve() for d in decl["include_dirs"]],
        limits=Limits.from_config(config),
        workdir=workdir,
    )


def _prompt(name: str, **values: str) -> str:
    folder = "prompts" if name in GENERATOR_PROMPTS else "harness"
    template = string.Template((HERE / folder / name).read_text(encoding="utf-8"))
    return template.substitute(values)


def parse_json_block(text: str) -> Any:
    """The JSON object in an agent's answer: the last ```json block, else the outermost braces."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, flags=re.DOTALL)
    for block in reversed(blocks):
        parsed = _loads(block)
        if parsed is not None:
            return parsed
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError("the answer contains no JSON object")


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _norm(text: str) -> str:
    return " ".join(text.split())


def _manifest_names(project: Path) -> list[str]:
    folder = project / CV / "manifests"
    names = [p.name for p in folder.glob("*.json")] if folder.is_dir() else []
    return sorted(names, key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)])


def _headers_block(snapshot: dict[str, Any]) -> str:
    return "\n\n".join(f"// ---- {name} ----\n{text}" for name, text in sorted(snapshot["header_texts"].items()))


def _items_by_id(item_set: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in item_set["items"]}


# --- intake ------------------------------------------------------------------------------------


def intake(text: str, project: str | Path) -> dict[str, Any]:
    """Accept the requirement item set (§3 协议 row 1) and open a session.

    The input is the item set (inline JSON, or a path to a JSON file, by default
    ``.cv/requirements.json``). Requirement processing happens upstream; free text is passed through
    as one item, untouched, and the report says so.
    """
    root = Path(project).resolve()
    notes: list[str] = []
    stripped = text.strip()
    default = root / CV / "requirements.json"
    candidate = root / stripped if stripped and "\n" not in stripped and len(stripped) < 1024 else None
    if not stripped and default.is_file():
        raw = json.loads(default.read_text(encoding="utf-8"))
        notes.append(f"需求条目集读自 {CV}/requirements.json")
    elif candidate is not None and candidate.is_file():
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        notes.append(f"需求条目集读自 {stripped}")
    elif stripped.startswith("{"):
        raw = json.loads(stripped)
    else:
        raw = P.passthrough_item_set(stripped, source="workflow input")
        if raw["items"]:
            notes.append("输入不是需求条目集：整段文字原样作为条目 R1。需求处理不属于交叉验证，未做拆分。")
    item_set = P.validate_item_set(raw)
    config = load_config(root)
    for sub in ("subtasks", "manifests", "contracts"):
        (root / CV / sub).mkdir(parents=True, exist_ok=True)
    store = Store(root)
    store.begin_session(_manifest_names(root))
    store.save("requirements", item_set)
    store.save("config", config)
    store.save("plan", {"items": {}, "conflicts": [], "notes": []})
    store.save("frozen", {"artifacts": {}, "interface": None})
    task = stripped if not item_set["items"] and stripped and not stripped.startswith("{") else ""
    return {
        "project": str(root),
        "item_set_id": item_set["id"],
        "empty": not item_set["items"],
        "task": task or "\n".join(item_set["unprocessed"]),
        "notes": notes,
        "round": 0,
        "signal": None,
    }


def interface_prompt(state: dict[str, Any]) -> str:
    store = _store(state)
    item_set = store.load("requirements", {})
    return _prompt("main_interface.md", items=json.dumps(item_set, ensure_ascii=False, indent=2), cv=CV)


# --- plan phase (算法1 第1-13行) ----------------------------------------------------------------


def _read_interface(project: Path, config: dict[str, Any]) -> dict[str, Any]:
    decl_path = project / CV / "interface.json"
    decl = P.validate_interface(json.loads(decl_path.read_text(encoding="utf-8")))
    snapshot = read_interface(project, decl["headers"], decl["include_dirs"], list(config["cflags"]))
    if snapshot["definitions"]:
        # The generator reads the headers: a function body there is implementation it would see (约束 3).
        raise P.ProtocolError(f"头文件里有函数体 {snapshot['definitions']}：接口只能含声明与类型定义")
    digests = {h: file_digest(project / h) for h in decl["headers"]}
    return {"decl": decl, "decl_digest": file_digest(decl_path), "header_digests": digests, "snapshot": snapshot}


def _plan_key(item: dict[str, Any], interface: dict[str, Any], config: dict[str, Any]) -> str:
    # 算法1 第3行: (q 的文本与示例, H 与类型定义, 生成器与提示的版本)
    snap = interface["snapshot"]
    return P.digest(
        {
            "item": {"text": item["text"], "examples": item["examples"]},
            "headers": snap["header_texts"],
            "types": snap["types_digest"],
            "generator": generator_version(config),
            "checker": CHECKER_VERSION,
        }
    )


def plan_prepare(state: dict[str, Any], phase: str) -> dict[str, Any]:
    """Decide which items need the generator. ``phase`` is "plan" or "replan" (算法1 第17行)."""
    store = _store(state)
    item_set = store.load("requirements", {})
    config = store.load("config", DEFAULT_CONFIG)
    state = dict(state, phase=phase, has_work=False, has_a=False, has_b=False, rewrite_a=False, rewrite_b=False)
    work: dict[str, Any] = {"phase": phase, "items": [], "cached": {}, "failed": {}, "segments": {}, "examples": {}}
    if not item_set["items"]:  # 算法1 第1行
        store.save("plan_work", work)
        return state
    if phase == "plan":
        try:
            interface = _read_interface(store.project, config)
        except (OSError, ValueError, ClangError, ToolMissing) as exc:
            reason = f"没有可用的接口声明（{CV}/interface.json 与头文件）：{exc}"
            work["failed"] = {item["id"]: reason for item in item_set["items"]}
            store.save("interface", None)
            store.save("plan_work", work)
            return state
        store.save("interface", interface)
        targets = [item["id"] for item in item_set["items"]]
    else:
        interface = store.load("interface", None)
        targets = list(state.get("replan_items", []))
        if interface is None:
            store.save("plan_work", work)
            return state
    items = _items_by_id(item_set)
    for item_id in targets:  # 算法1 第2行: 对 Q 中每条条目 q
        key = _plan_key(items[item_id], interface, config)
        cached = store.cache_get("plan", key)
        if cached is not None:  # 算法1 第3行: 复用
            work["cached"][item_id] = cached
        else:
            work["items"].append(item_id)
    store.save("plan_work", work)
    state["has_work"] = bool(work["items"])
    return state


def segment_prompt(state: dict[str, Any]) -> str:
    store = _store(state)
    work = store.load("plan_work", {})
    items = _items_by_id(store.load("requirements", {}))
    interface = store.load("interface", {})
    payload = [{"id": i, "text": items[i]["text"], "examples": items[i]["examples"]} for i in work["items"]]
    return _prompt(
        "segment.md",
        items=json.dumps(payload, ensure_ascii=False, indent=2),
        headers=_headers_block(interface["snapshot"]),
        functions=", ".join(sorted(interface["snapshot"]["functions"])),
    )


def plan_requests(state: dict[str, Any], answer: str | None) -> dict[str, Any]:
    """Read the segmentation (§3 流程 2) and build the generation requests for sources A and B.

    算法1 第4行: the generator divides each item into segments, each with its position (an exact span
    of the item) and the interface functions it involves (only functions declared in the headers).
    """
    store = _store(state)
    work = store.load("plan_work", {})
    items = _items_by_id(store.load("requirements", {}))
    interface = store.load("interface", {})
    declared = set(interface["snapshot"]["functions"])
    try:
        parsed = parse_json_block(answer or "")
        entries = {e["item"]: e for e in parsed.get("items", []) if isinstance(e, dict) and "item" in e}
    except (ValueError, AttributeError) as exc:
        entries = {}
        work["notes"] = [f"划分结果无法解析：{exc}"]
    for item_id in work["items"]:
        entry = entries.get(item_id)
        if entry is None:
            work["failed"][item_id] = "生成器没有划分该条目"
            continue
        segments, seen = [], set()
        for n, seg in enumerate(entry.get("segments", []) or [], start=1):
            span = str(seg.get("span", ""))
            seg_id = str(seg.get("id") or f"S{n}")
            if seg_id in seen or not span.strip() or _norm(span) not in _norm(items[item_id]["text"]):
                continue  # 每段须标明在条目中的位置: a span that is not in the item is not a position
            seen.add(seg_id)
            functions = [f for f in seg.get("functions", []) if f in declared]  # 只限头文件中声明的函数
            segments.append({"id": seg_id, "span": span, "functions": functions})
        if not segments:
            work["failed"][item_id] = "生成器给出的段落都不在条目原文中"
            continue
        work["segments"][item_id] = segments
        work["examples"][item_id] = [
            ex for ex in entry.get("examples", []) or [] if isinstance(ex, dict) and ex.get("function") in declared
        ]
    work["items"] = [i for i in work["items"] if i in work["segments"]]
    store.save("plan_work", work)
    has = bool(work["items"])
    return dict(state, has_a=has, has_b=has)


def generation_prompt(state: dict[str, Any], source: str) -> str:
    """生成请求 (§3 协议 row 3): item text, its examples for source B only, and the headers."""
    store = _store(state)
    work = store.load("plan_work", {})
    items = _items_by_id(store.load("requirements", {}))
    interface = store.load("interface", {})
    requests = []
    for item_id in work["items"]:
        request = {"item": item_id, "text": items[item_id]["text"], "segments": work["segments"][item_id]}
        if source == "B":  # 两个来源换输入: A 只读条目，B 读条目加示例与其余条目（全局约束）
            request["examples"] = items[item_id]["examples"]
            request["other_items"] = [i["text"] for i in items.values() if i["id"] != item_id]
        requests.append(request)
    return _prompt(
        "generate.md",
        source=source,
        requests=json.dumps(requests, ensure_ascii=False, indent=2),
        headers=_headers_block(interface["snapshot"]),
        conventions=(HERE / "prompts" / "conventions.md").read_text(encoding="utf-8"),
    )


_CONTENT_KEYS = {
    P.FORM_PROPERTY: ("spec", "harness", "target", "allowed_writers"),
    P.FORM_REFERENCE: ("model", "harness"),
    P.FORM_CASES: ("cases",),
}


def _parse_artifacts(answer: str | None, source: str, work: dict[str, Any]) -> tuple[list[dict], dict, list[str]]:
    """Artifacts from one source's answer, plus the segments it marked uncovered and parse notes."""
    notes: list[str] = []
    try:
        entries = parse_json_block(answer or "").get("artifacts", [])
    except (ValueError, AttributeError) as exc:
        return [], {}, [f"来源 {source} 的输出无法解析：{exc}"]
    artifacts, uncovered, ordinals = [], {}, {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item, seg_id = entry.get("item"), entry.get("segment")
        segment = next((s for s in work["segments"].get(item, []) if s["id"] == seg_id), None)
        if segment is None:
            notes.append(f"来源 {source} 给出未知的段 {item}.{seg_id}")
            continue
        # 算法1 第5行: the generator picks the first feasible form, in the order property, reference model,
        # cases (prompts/conventions.md). 算法1 第6行: none feasible -> uncovered, with the reason it gives.
        form = entry.get("form")
        if form == P.FORM_UNCOVERED:
            uncovered[(item, seg_id)] = str(entry.get("reason", "未说明原因"))
            continue
        if form not in P.FORMS:
            notes.append(f"来源 {source} 在 {item}.{seg_id} 给出未知形态 {form!r}")
            continue
        content = {k: entry[k] for k in _CONTENT_KEYS[form] if k in entry}
        ordinals[(item, seg_id)] = ordinals.get((item, seg_id), 0) + 1
        try:
            artifacts.append(
                P.make_artifact(
                    item=item,
                    segment=seg_id,
                    span=segment["span"],
                    functions=[f for f in entry.get("functions") or [] if f in segment["functions"]]
                    or segment["functions"],
                    form=form,
                    kind=entry.get("kind"),
                    reading=str(entry.get("reading", "")),
                    source=source,
                    content=content,
                    ordinal=ordinals[(item, seg_id)],
                )
            )
        except P.ProtocolError as exc:
            notes.append(f"来源 {source} 在 {item}.{seg_id}：{exc}")
    return artifacts, uncovered, notes


def _workdir(store: Store, name: str) -> Path:
    path = store.root / "work" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _group_readings(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A reading is what one source wrote under one ``reading`` label: all its artifacts must pass.

    Several artifacts under one label are supplementary checks of the same reading (the design's
    structure constraint plus a sequence invariant for one rule); different labels are different
    readings (for instance two value ranges, §3 流程 3).
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for artifact in artifacts:
        groups.setdefault((artifact["source"], artifact["reading"]), []).append(artifact)
    return [{"source": s, "label": label, "artifacts": arts} for (s, label), arts in groups.items()]


def _readings_equivalent(ctx: Context, r1: dict[str, Any], r2: dict[str, Any], pairs: list) -> bool | None:
    """Two readings are the same when their artifacts match one to one as equivalent artifacts.

    None when some artifact could not be compared; the caller then keeps both readings.
    """
    if len(r1["artifacts"]) != len(r2["artifacts"]):
        return False
    unmatched = list(r2["artifacts"])
    undecided = False
    for x in r1["artifacts"]:
        found = None
        for y in unmatched:
            result = equivalent(ctx, x, y)
            pairs.append({"a": x["id"], "b": y["id"], **result})
            if result["equivalent"] is True:
                found = y
                break
            undecided = undecided or result["equivalent"] is None
        if found is None:
            return None if undecided else False
        unmatched.remove(found)
    return True


def _dedupe(ctx: Context, readings: list[dict[str, Any]], pairs: list) -> list[dict[str, Any]]:
    """算法1 第8行: equivalent readings collapse into the first; the others stay as readings."""
    kept: list[dict[str, Any]] = []
    for candidate in readings:
        if not any(_readings_equivalent(ctx, k, candidate, pairs) is True for k in kept):
            kept.append(candidate)
    return kept


def plan_check(state: dict[str, Any], answer_a: str | None, answer_b: str | None) -> dict[str, Any]:
    """算法1 第7-10行: two sources, equivalence, contradictions with the examples, self-check."""
    store = _store(state)
    work = store.load("plan_work", {})
    config = store.load("config", DEFAULT_CONFIG)
    interface = store.load("interface", {})
    ctx = _context(store, config, interface, _workdir(store, "plan"))
    arts_a, unc_a, notes_a = _parse_artifacts(answer_a, "A", work)  # 算法1 第7行: 来源 A
    arts_b, unc_b, notes_b = _parse_artifacts(answer_b, "B", work)  # 算法1 第7行: 来源 B
    work["notes"] = work.get("notes", []) + notes_a + notes_b
    work["seg_state"] = {}
    rewrite: dict[str, list] = {"A": [], "B": []}
    for item_id in work["items"]:
        examples = work["examples"].get(item_id, [])
        for segment in work["segments"][item_id]:
            key = f"{item_id}.{segment['id']}"
            mine = [a for a in arts_a + arts_b if a["item"] == item_id and a["segment"] == segment["id"]]
            reasons = [r for r in (unc_a.get((item_id, segment["id"])), unc_b.get((item_id, segment["id"]))) if r]
            if not segment["functions"]:  # nothing to aggregate the result under (算法1 第22-25行)
                mine, reasons = [], ["这段要求没有涉及头文件中声明的接口函数", *reasons]
            pairs: list[dict[str, Any]] = []
            readings = _dedupe(ctx, _group_readings(mine), pairs)
            removed = []
            # 算法1 第9行 (examples): only among several readings, and never by looking at code.
            if len(readings) >= 2 and examples:
                keep = []
                for reading in readings:
                    failed = [
                        c for a in reading["artifacts"] for c in example_checks(ctx, a, examples) if c["ok"] is False
                    ]
                    if failed:
                        removed.append({"reading": reading["label"], "why": "与示例矛盾：" + failed[0]["detail"]})
                    else:
                        keep.append(reading)
                readings = keep
            # 算法1 第10行: self-check; a failing artifact is rewritten once by its own source.
            results = {}
            for reading in readings:
                for artifact in reading["artifacts"]:
                    results[artifact["id"]] = self_check(ctx, artifact, examples)
                    if not results[artifact["id"]]["ok"]:
                        rewrite[artifact["source"]].append(
                            {"artifact": artifact, "reason": results[artifact["id"]]["reason"],
                             "checks": results[artifact["id"]]["checks"]}
                        )  # fmt: skip
            work["seg_state"][key] = {
                "readings": readings,
                "selfcheck": results,
                "pairs": pairs,
                "removed": removed,
                "uncovered_reasons": reasons,
                "sources": sorted({a["source"] for a in mine}),
            }
    work["rewrite"] = rewrite
    store.save("plan_work", work)
    return dict(state, rewrite_a=bool(rewrite["A"]), rewrite_b=bool(rewrite["B"]))


def rewrite_prompt(state: dict[str, Any], source: str) -> str:
    store = _store(state)
    work = store.load("plan_work", {})
    interface = store.load("interface", {})
    items = _items_by_id(store.load("requirements", {}))
    failed = []
    for r in work["rewrite"][source]:
        artifact, reason = r["artifact"], r["reason"]
        item = items[artifact["item"]]
        if source == "A":  # A never reads the examples, not even through a failure reason
            failed_checks = [c for c in r.get("checks", []) if c["ok"] is False]
            parts = [c["detail"] for c in failed_checks if c["check"] != "example"]
            if any(c["check"] == "example" for c in failed_checks):
                parts.append("与条目的某个示例矛盾")
            reason = "；".join(parts) or reason
        entry = {"item": artifact["item"], "requirement": item["text"], "segment": artifact["segment"],
                 "span": artifact["span"], "reading": artifact["reading"], "form": artifact["form"],
                 "kind": artifact["kind"], "functions": artifact["functions"], **artifact["content"],
                 "selfcheck_failure": reason}  # fmt: skip
        if source == "B":
            entry["examples"] = item["examples"]
        failed.append(entry)
    return _prompt(
        "rewrite.md",
        failed=json.dumps(failed, ensure_ascii=False, indent=2),
        headers=_headers_block(interface["snapshot"]),
        conventions=(HERE / "prompts" / "conventions.md").read_text(encoding="utf-8"),
    )


def _apply_rewrites(ctx: Context, work: dict[str, Any], source: str, answer: str | None) -> None:
    """重写一次，仍不通过则丢弃 (算法1 第10行)."""
    pending = work["rewrite"][source]
    if not pending:
        return
    rewritten, _, notes = _parse_artifacts(answer, source, work)
    work["notes"] += notes
    for request in pending:
        old = request["artifact"]
        seg = work["seg_state"][f"{old['item']}.{old['segment']}"]
        reading = next(r for r in seg["readings"] if any(a["id"] == old["id"] for a in r["artifacts"]))
        same_place = [a for a in rewritten if a["item"] == old["item"] and a["segment"] == old["segment"]]
        match = next((a for a in same_place if a["reading"] == old["reading"] and a["form"] == old["form"]), None)
        reading["artifacts"] = [a for a in reading["artifacts"] if a["id"] != old["id"]]
        if match is None:
            seg["removed"].append({"artifact": old["id"], "why": "自检不通过且没有重写：" + request["reason"]})
            continue
        rewritten.remove(match)
        match = dict(match, id=old["id"])
        result = self_check(ctx, match, work["examples"].get(old["item"], []))
        seg["selfcheck"][old["id"]] = result
        if result["ok"]:
            reading["artifacts"].append(match)
            seg["rewritten"] = True
        else:
            seg["removed"].append({"artifact": old["id"], "why": "重写后自检仍不通过：" + result["reason"]})
    for seg in work["seg_state"].values():  # a reading whose every artifact was discarded is gone
        seg["readings"] = [r for r in seg["readings"] if r["artifacts"]]


def plan_freeze(state: dict[str, Any], answer_a: str | None, answer_b: str | None) -> dict[str, Any]:
    """算法1 第10-13行: rewrites, segment status, conflicts, freeze; then cache the item plans."""
    store = _store(state)
    work = store.load("plan_work", {})
    config = store.load("config", DEFAULT_CONFIG)
    interface = store.load("interface", None)
    plan = store.load("plan", {"items": {}, "conflicts": [], "notes": []})
    item_set = store.load("requirements", {})
    items = _items_by_id(item_set)
    if work.get("phase") == "replan" and not work["items"] and not work["cached"]:
        return state  # nothing was affected: the plan stays as frozen
    if not item_set["items"]:
        plan["notes"] = ["无可检查的需求"]  # 算法1 第1行
        store.save("plan", plan)
        return dict(state, plan_summary=_summary(plan))
    if interface is None:
        for item_id, reason in work["failed"].items():
            plan["items"][item_id] = _uncovered_item(items[item_id], reason)
        store.save("plan", plan)
        _freeze(store, plan, None)
        return dict(state, plan_summary=_summary(plan))
    ctx = _context(store, config, interface, _workdir(store, "freeze"))
    if work.get("rewrite"):
        _apply_rewrites(ctx, work, "A", answer_a)
        _apply_rewrites(ctx, work, "B", answer_b)
    for item_id in work["items"]:
        segs = []
        for segment in work["segments"][item_id]:
            seg = work["seg_state"][f"{item_id}.{segment['id']}"]
            readings = seg["readings"]
            if seg.get("rewritten") and len(readings) >= 2:  # a rewrite can make two readings equal
                readings = _dedupe(ctx, readings, seg["pairs"])
            segs.append(_segment_record(segment, readings, seg))
        if not _sources_differ(config, items[item_id], item_set):
            for record in segs:
                record["notes"].append(SAME_SOURCE_NOTE)
        plan["items"][item_id] = {"item": item_id, "segments": segs, "examples": work["examples"].get(item_id, [])}
    for item_id, reason in work["failed"].items():
        plan["items"][item_id] = _uncovered_item(items[item_id], reason)
    for item_id, cached in work["cached"].items():
        plan["items"][item_id] = cached
    _remove_contradicting_readings(ctx, plan)  # 算法1 第9行 (与其他已确定的子任务产物矛盾)
    _mark_conflicts(ctx, plan)  # 算法1 第12行
    for item_id in work["items"]:
        store.cache_put("plan", _plan_key(items[item_id], interface, config), plan["items"][item_id])
    plan["notes"] = work.get("notes", [])
    store.save("plan", plan)
    _freeze(store, plan, interface)  # 算法1 第13行
    return dict(state, plan_summary=_summary(plan), replan_items=[])


SAME_SOURCE_NOTE = (
    "两个来源用的是同一个模型，而条目既没有示例、也没有其他条目，两者的输入也相同：来源之间没有差异，"
    "约束 3（不和主任务犯同一种错）得不到来源差异的支持。请在 config.json 中为 generator_b_model 配置另一个模型"
)


def _sources_differ(config: dict[str, Any], item: dict[str, Any], item_set: dict[str, Any]) -> bool:
    """两个来源至少换模型或换输入 (§3 流程 3): B also reads the examples and the other items."""
    other_items = len(item_set["items"]) > 1
    return config["generator_a_model"] != config["generator_b_model"] or bool(item["examples"]) or other_items


def _uncovered_item(item: dict[str, Any], reason: str) -> dict[str, Any]:
    segment = {"id": "S0", "span": item["text"], "functions": [], "status": P.SEG_UNCOVERED, "readings": []}
    segment.update(reason=reason, removed=[], distinguishing=None, sources=[], selfcheck={}, notes=[])
    return {"item": item["id"], "segments": [segment], "examples": []}


def _segment_record(segment: dict[str, Any], readings: list[dict[str, Any]], seg: dict[str, Any]) -> dict[str, Any]:
    """算法1 第11行: 未覆盖 (none left) / 确定 (one reading) / 有歧义 (two or more)."""
    if not readings:
        status = P.SEG_UNCOVERED
        reason = "；".join(seg["uncovered_reasons"]) or "没有通过自检的子任务产物"
    else:
        status = P.SEG_DETERMINED if len(readings) == 1 else P.SEG_AMBIGUOUS
        reason = ""
    ids = {a["id"] for r in readings for a in r["artifacts"]}
    distinguishing = None
    for pair in seg["pairs"]:
        if pair["a"] in ids and pair["b"] in ids and pair.get("distinguishing"):
            distinguishing = pair["distinguishing"]
    notes = ["只有一个来源给出了子任务产物"] if readings and len(seg["sources"]) < 2 else []
    return {
        "id": segment["id"],
        "span": segment["span"],
        "functions": sorted(
            {f for r in readings for a in r["artifacts"] for f in a["functions"]} | set(segment["functions"])
        ),
        "status": status,
        "readings": readings,
        "reason": reason,
        "removed": seg["removed"],
        "distinguishing": distinguishing,
        "sources": seg["sources"],
        "selfcheck": {k: v for k, v in seg["selfcheck"].items() if k in ids},
        "notes": notes,
    }


def _segment_artifacts(seg: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for r in seg["readings"] for a in r["artifacts"]]


def _canonical_members(ctx: Context, artifacts: list[dict[str, Any]]) -> dict[tuple[str, str], list]:
    groups: dict[tuple[str, str], list] = {}
    for artifact in artifacts:
        canon = canonical(ctx, artifact)
        for key in {group_key(n) for n in canon} - {None}:
            groups.setdefault(key, []).append((artifact, canon))
    return groups


def _determined(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        a
        for record in plan["items"].values()
        for seg in record["segments"]
        if seg["status"] == P.SEG_DETERMINED
        for a in _segment_artifacts(seg)
        if a["form"] == P.FORM_PROPERTY and a.get("kind") in ("P1", "P2")
    ]


def _remove_contradicting_readings(ctx: Context, plan: dict[str, Any]) -> None:
    """Among several readings, drop those that cannot hold together with a determined artifact."""
    determined = _canonical_members(ctx, _determined(plan))
    for record in plan["items"].values():
        for seg in record["segments"]:
            if seg["status"] != P.SEG_AMBIGUOUS:
                continue
            keep = []
            for reading in seg["readings"]:
                clash = None
                for artifact in reading["artifacts"]:
                    canon = canonical(ctx, artifact)
                    for key in sorted({group_key(n) for n in canon} - {None}):
                        base = determined.get(key, [])
                        if (
                            base
                            and unsatisfiable(ctx, base, key) is False
                            and unsatisfiable(ctx, [*base, (artifact, canon)], key) is True
                        ):
                            clash = key
                            break
                    if clash:
                        break
                if clash:
                    seg["removed"].append(
                        {"reading": reading["label"], "why": f"与已确定的子任务产物矛盾（{clash[1]}）"}
                    )
                else:
                    keep.append(reading)
            seg["readings"] = keep
            seg["status"] = P.SEG_DETERMINED if len(keep) == 1 else P.SEG_AMBIGUOUS if keep else P.SEG_UNCOVERED
            if not keep:
                seg["reason"] = "所有读法都与已确定的子任务产物矛盾"


def _mark_conflicts(ctx: Context, plan: dict[str, Any]) -> None:
    """Pre/postconditions of one function, or invariants of one type, that cannot hold together."""
    plan["conflicts"] = []
    for key, members in sorted(_canonical_members(ctx, _determined(plan)).items()):
        if len(members) < 2 or unsatisfiable(ctx, members, key) is not True:
            continue
        ids = {a["id"] for a in minimal_conflict(ctx, members, key)}
        plan["conflicts"].append({"subject": key[1], "artifacts": sorted(ids)})
        for record in plan["items"].values():
            for seg in record["segments"]:
                if any(a["id"] in ids for a in _segment_artifacts(seg)):
                    seg["status"] = P.SEG_CONFLICT
                    seg["reason"] = f"与其他要求冲突（{key[1]}）：需求本身可能有矛盾"


def _artifact_path(project: Path, artifact_id: str) -> Path:
    return project / CV / "subtasks" / f"{artifact_id}.json"


def _freeze(store: Store, plan: dict[str, Any], interface: dict[str, Any] | None) -> None:
    """冻结: write the artifacts to .cv/subtasks and record their hashes outside the project."""
    folder = store.project / CV / "subtasks"
    folder.mkdir(parents=True, exist_ok=True)
    live = set()
    hashes = {}
    for record in plan["items"].values():
        for seg in record["segments"]:
            for artifact in _segment_artifacts(seg):
                path = _artifact_path(store.project, artifact["id"])
                path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
                rel = path.relative_to(store.project).as_posix()
                hashes[rel] = file_digest(path)
                live.add(path.name)
    for stale in folder.glob("*.json"):
        if stale.name not in live:
            stale.unlink()
    frozen_interface = None
    if interface is not None:
        frozen_interface = {
            "decl_digest": interface["decl_digest"],
            "header_digests": interface["header_digests"],
            "functions": {n: f["digest"] for n, f in interface["snapshot"]["functions"].items()},
            "types_digest": interface["snapshot"]["types_digest"],
        }
    store.save("frozen", {"artifacts": hashes, "interface": frozen_interface})


def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for record in plan["items"].values():
        for seg in record["segments"]:
            counts[seg["status"]] = counts.get(seg["status"], 0) + 1
    return {"segments": counts, "conflicts": len(plan.get("conflicts", []))}


# --- completion phase (算法1 第14-33行) ---------------------------------------------------------


def implement_prompt(state: dict[str, Any]) -> str:
    store = _store(state)
    item_set = store.load("requirements", {})
    if state.get("feedback"):
        return _prompt("main_feedback.md", feedback=state["feedback"], cv=CV, set_id=item_set["id"])
    interface = store.load("interface", None) or {}
    headers = ", ".join(interface.get("decl", {}).get("headers", [])) or "（无）"
    return _prompt(
        "main_implement.md",
        items=json.dumps(item_set, ensure_ascii=False, indent=2),
        task=state.get("task") or "（无）",
        headers=headers,
        cv=CV,
        set_id=item_set["id"],
    )


def _decide(state: dict[str, Any], signal: str, *, feedback: str = "", report: dict[str, Any] | None = None) -> dict:
    v = P.verdict(signal, feedback=feedback, report=report)
    return dict(state, signal=v["signal"], feedback=v["feedback"], report=v["report"])


C_SOURCES = (".c",)
UNSUPPORTED_SOURCES = (".cc", ".cpp", ".cxx", ".c++", ".m", ".mm")


def verify(state: dict[str, Any], *, require_declaration: bool = False) -> dict[str, Any]:
    """等待 and 校验: a new completion declaration, intact artifacts, an unchanged interface.

    ``require_declaration``: in the workflow the judge runs once the main agent has stopped, so a
    stop without a new declaration is not "not a completion" (算法1 第14行 is for a harness that
    fires after every reply) but a completion without a declaration: it is sent back, with the
    format, instead of being passed unchecked.
    """
    store = _store(state)
    history = store.load("history", {"rounds": []})
    round_no = max(state.get("round", 0), len(history["rounds"])) + 1
    state = dict(state, round=round_no, signal=None, feedback="", replan_items=[], manifest=None)
    seen = set(store.load("seen_manifests", []))
    names = _manifest_names(store.project)
    new = [n for n in names if n not in seen]
    item_set = store.load("requirements", {})
    if not new:  # 算法1 第14行
        if require_declaration and item_set["items"]:
            message = _prompt("main_manifest_error.md", error="没有写新的完成清单", cv=CV, set_id=item_set["id"])
            return _decide(
                state,
                P.SIGNAL_BLOCK,
                feedback=message,
                report=_bare_report(store, state, "主 agent 停止时没有作出完成声明"),
            )
        return _decide(state, P.SIGNAL_PASS, report=_bare_report(store, state, "没有新的完成声明：未进行比对"))
    store.save("seen_manifests", sorted(seen | set(new)))
    if not item_set["items"]:  # 算法1 第15行
        return _decide(state, P.SIGNAL_PASS, report=_bare_report(store, state, "无可检查的需求"))
    latest = store.project / CV / "manifests" / new[-1]
    try:
        manifest = P.validate_manifest(json.loads(latest.read_text(encoding="utf-8")), item_set["id"])
        missing = [f for f in manifest["files"] if not (store.project / f).is_file()]
        if missing:
            raise P.ProtocolError(f"清单中的文件不存在：{missing}")
    except (OSError, ValueError) as exc:
        message = _prompt("main_manifest_error.md", error=str(exc), cv=CV, set_id=item_set["id"])
        return _decide(
            state, P.SIGNAL_BLOCK, feedback=message, report=_bare_report(store, state, f"完成声明无效：{exc}")
        )
    unsupported = [f for f in manifest["files"] if f.lower().endswith(UNSUPPORTED_SOURCES)]
    if unsupported:
        reason = f"交付含有 checker 不支持的源文件 {unsupported}：本插件只检查 C 实现，这次交付无法被交叉验证"
        return _decide(state, P.SIGNAL_REJECT, feedback=reason, report=_bare_report(store, state, reason))
    state["manifest"] = manifest
    frozen = store.load("frozen", {"artifacts": {}, "interface": None})
    if frozen.get("interface") is None:
        # 接口须在实现开始前确定 (§3 协议 row 2). Without one nothing was planned: the requirements would all
        # stay uncovered and pass unchecked. A declaration written now is planned before any comparison.
        try:
            interface = _read_interface(store.project, store.load("config", DEFAULT_CONFIG))
        except (OSError, ValueError, ClangError, ToolMissing) as exc:
            message = _prompt("main_interface_error.md", error=str(exc), cv=CV)
            return _decide(
                state, P.SIGNAL_BLOCK, feedback=message, report=_bare_report(store, state, f"没有可用的接口：{exc}")
            )
        store.save("interface", interface)
        state["replan_items"] = [item["id"] for item in item_set["items"]]
        state["notes"] = [*state.get("notes", []), "接口在实现之后才声明：全部条目在比对前重新计划"]
        return state
    changed = []
    for rel, expected in sorted(frozen["artifacts"].items()):  # 算法1 第16行
        path = store.project / rel
        if not path.is_file() or file_digest(path) != expected:
            changed.append(rel)
    if changed:
        report = _bare_report(store, state, f"子任务产物被改动：{changed}")
        return _decide(state, P.SIGNAL_REJECT, feedback=f"子任务产物被改动：{changed}", report=report)
    state["replan_items"] = _affected_items(store, frozen)  # 算法1 第17行
    return state


def _affected_items(store: Store, frozen: dict[str, Any]) -> list[str]:
    old = frozen.get("interface")
    config = store.load("config", DEFAULT_CONFIG)
    plan = store.load("plan", {"items": {}})
    decl_path = store.project / CV / "interface.json"
    if old is None:
        return []
    unchanged = (
        decl_path.is_file()
        and file_digest(decl_path) == old["decl_digest"]
        and all(
            (store.project / h).is_file() and file_digest(store.project / h) == d
            for h, d in old["header_digests"].items()
        )
    )
    if unchanged:
        return []
    try:
        interface = _read_interface(store.project, config)
    except (OSError, ValueError, ClangError, ToolMissing):
        return sorted(plan["items"])
    store.save("interface", interface)
    if interface["snapshot"]["types_digest"] != old["types_digest"]:
        return sorted(plan["items"])
    current = {n: f["digest"] for n, f in interface["snapshot"]["functions"].items()}
    touched = {n for n in set(old["functions"]) | set(current) if old["functions"].get(n) != current.get(n)}
    affected = []
    for item_id, record in plan["items"].items():
        functions = {f for seg in record["segments"] for f in seg["functions"]}
        if functions & touched or any(seg["status"] == P.SEG_UNCOVERED for seg in record["segments"]):
            affected.append(item_id)
    if not affected:  # a change that touches no requirement: only the frozen header record moves on
        plan_state = store.load("plan", {"items": {}})
        _freeze(store, plan_state, interface)
    return sorted(affected)


def _compare_key(artifact: dict[str, Any], delivered: dict[str, str], frozen: dict[str, Any], config: dict) -> str:
    """比对结果的缓存键 (§3 集成).

    The design names the plan key plus the bodies of the checked function and of the functions it
    calls, plus the checker's version and configuration. A harness may call interface functions the
    artifact does not list, and globals, macros and initialisers are not function bodies, so this key
    takes every delivered file whole: a superset of the design's key, which can only make the cache
    hit less often, never return a result for code it was not computed on.
    """
    return P.digest(
        {
            "artifact": artifact,
            "delivered": delivered,
            "interface": frozen.get("interface"),
            "checker": CHECKER_VERSION,
            "limits": Limits.from_config(config).describe(),
        }
    )


def _code_errors(text: str, workdir: Path) -> str:
    """Compiler messages about the delivered code only: lines about generated files would quote artifacts."""
    kept = [line for line in text.splitlines() if str(workdir) not in line]
    return "\n".join(kept[:40])


def compare(state: dict[str, Any]) -> dict[str, Any]:
    """比对 and 判定: 算法1 第18-33行."""
    if state.get("signal"):
        return state
    store = _store(state)
    config = store.load("config", DEFAULT_CONFIG)
    plan = store.load("plan", {"items": {}})
    interface = store.load("interface", None)
    item_set = store.load("requirements", {})
    frozen = store.load("frozen", {"artifacts": {}, "interface": None})
    manifest = state["manifest"]
    impl_files = [f for f in manifest["files"] if f.endswith(C_SOURCES)]
    delivered = {
        f: (store.project / f).read_text(encoding="utf-8", errors="replace")
        for f in sorted({*manifest["files"], *((interface or {}).get("decl", {}).get("headers", []))})
        if (store.project / f).is_file()
    }
    results: dict[str, dict[str, Any]] = {}
    impl: dict[str, Any] = {"functions": {}}
    impl_error = ""
    if interface is not None:
        ctx = _context(store, config, interface, _workdir(store, f"compare-{state['round']}"))
        ctx.impl_files = [(store.project / f).resolve() for f in impl_files]
        try:
            impl = read_implementation(
                store.project,
                impl_files,
                interface["decl"]["include_dirs"],
                list(config["cflags"]),
                interface["snapshot"],
            )
        except ClangError as exc:  # the delivered code does not compile: that is the code's failure
            impl_error = "实现无法编译：\n" + _code_errors(str(exc), ctx.workdir)
        except ToolMissing as exc:
            impl_error = ""
            results = {a["id"]: P.check_result(P.RESULT_UNKNOWN, reason=str(exc)) for a in _all_artifacts(plan)}
            return _judge(state, store, config, plan, item_set, results)
        for record in plan["items"].values():
            for seg in record["segments"]:
                if seg["status"] not in (P.SEG_DETERMINED, P.SEG_AMBIGUOUS):
                    continue  # 未覆盖与冲突的要求跳过，只写入报告
                for artifact in _segment_artifacts(seg):
                    on_disk = json.loads(_artifact_path(store.project, artifact["id"]).read_text(encoding="utf-8"))
                    if impl_error:
                        results[artifact["id"]] = P.check_result(
                            P.RESULT_FAIL, counterexample={"calls": [], "reason": impl_error, "method": "compile"}
                        )
                        continue
                    undefined = sorted(f for f in on_disk["functions"] if f not in impl["functions"])
                    if undefined:
                        reason = f"交付的文件里没有实现接口函数 {undefined}"
                        results[artifact["id"]] = P.check_result(
                            P.RESULT_FAIL, counterexample={"calls": [], "reason": reason, "method": "compile"}
                        )
                        continue
                    key = _compare_key(on_disk, delivered, frozen, config)
                    cached = store.cache_get("compare", key)
                    if cached is None:  # 算法1 第18行
                        try:
                            cached = checker_compare(ctx, on_disk, impl)
                        except ToolMissing as exc:
                            cached = P.check_result(P.RESULT_UNKNOWN, reason=str(exc))
                        if cached.get("counterexample") and cached["counterexample"].get("reason"):
                            cached["counterexample"]["reason"] = _code_errors(
                                cached["counterexample"]["reason"], ctx.workdir
                            )
                        if not (cached["status"] == P.RESULT_UNKNOWN and "safety timeout" in cached["reason"]):
                            store.cache_put("compare", key, cached)
                    results[artifact["id"]] = cached
    return _judge(state, store, config, plan, item_set, results)


def _all_artifacts(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for record in plan["items"].values() for seg in record["segments"] for a in _segment_artifacts(seg)]


def _reading_result(reading: dict[str, Any], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A reading passes when every one of its artifacts passes; its strength is the weakest one's."""
    outcomes = [(a, results[a["id"]]) for a in reading["artifacts"] if a["id"] in results]
    failed = [(a, r) for a, r in outcomes if r["status"] == P.RESULT_FAIL]
    if failed:
        return {"status": P.RESULT_FAIL, "strength": None, "failures": [r for _, r in failed]}
    if outcomes and all(r["status"] == P.RESULT_PASS for _, r in outcomes):
        weakest = min((r["strength"] for _, r in outcomes), key=lambda s: P.STRENGTH_RANK[s])
        return {"status": P.RESULT_PASS, "strength": weakest, "failures": []}
    reasons = "；".join(f"{a['id']}: {r['reason']}" for a, r in outcomes if r["status"] == P.RESULT_UNKNOWN)
    return {"status": P.RESULT_UNKNOWN, "strength": None, "failures": [], "reason": reasons or "没有检查结果"}


def _segment_result(seg: dict[str, Any], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """算法1 第19-21行: 确定 -> its reading's result; 有歧义 -> any reading passing passes."""
    per_reading = [(r, _reading_result(r, results)) for r in seg["readings"]]
    passing = [(r, o) for r, o in per_reading if o["status"] == P.RESULT_PASS]
    if passing:
        reading, outcome = max(passing, key=lambda ro: P.STRENGTH_RANK[ro[1]["strength"]])
        return {"status": P.RESULT_PASS, "strength": outcome["strength"], "reading": reading["label"],
                "adopted": reading, "failures": [], "reason": ""}  # fmt: skip
    if per_reading and all(o["status"] == P.RESULT_FAIL for _, o in per_reading):
        failures = [f for _, o in per_reading for f in o["failures"]]
        return {"status": P.RESULT_FAIL, "strength": None, "reading": None, "adopted": None,
                "failures": failures, "reason": ""}  # fmt: skip
    reasons = "；".join(o.get("reason", "") for _, o in per_reading if o["status"] == P.RESULT_UNKNOWN)
    return {"status": P.RESULT_UNKNOWN, "strength": None, "reading": None, "adopted": None,
            "failures": [], "reason": reasons}  # fmt: skip


def _judge(state, store, config, plan, item_set, results) -> dict[str, Any]:
    items = _items_by_id(item_set)
    segments = []
    for item_id, record in sorted(plan["items"].items()):
        for seg in record["segments"]:
            entry = {"item": item_id, "segment": seg["id"], "span": seg["span"], "status": seg["status"],
                     "functions": seg["functions"], "reason": seg.get("reason", ""), "seg": seg}  # fmt: skip
            if seg["status"] in (P.SEG_DETERMINED, P.SEG_AMBIGUOUS):
                entry["outcome"] = _segment_result(seg, results)
            segments.append(entry)
    # 算法1 第22-25行
    min_rank = P.STRENGTH_RANK[config["min_strength"]]
    functions: dict[str, dict[str, Any]] = {}
    for entry in segments:
        if "outcome" not in entry:
            continue
        # Every compared requirement counts: one that names no function counts under its own name.
        for fn in entry["functions"] or [f"<{entry['item']}.{entry['segment']}>"]:
            functions.setdefault(fn, {"outcomes": []})["outcomes"].append(entry["outcome"])
    for info in functions.values():
        statuses = [o["status"] for o in info["outcomes"]]
        strong = all(o["strength"] and P.STRENGTH_RANK[o["strength"]] >= min_rank for o in info["outcomes"])
        if P.RESULT_FAIL in statuses:
            info["result"] = P.RESULT_FAIL
        elif all(s == P.RESULT_PASS for s in statuses) and strong:
            info["result"] = P.RESULT_PASS
        else:
            info["result"] = P.RESULT_UNKNOWN
    history = store.load("history", {"rounds": [], "ever_failed": []})
    retries = store.load("retries", {})
    failed_fns = sorted(f for f, i in functions.items() if i["result"] == P.RESULT_FAIL)
    signal, feedback = P.SIGNAL_PASS, ""
    if failed_fns:  # 算法1 第26-29行
        for fn in failed_fns:
            retries[fn] = retries.get(fn, 0) + 1
        history["ever_failed"] = sorted(set(history["ever_failed"]) | set(failed_fns))
        exhausted = [f for f in failed_fns if retries[f] >= config["retry_limit"]]
        failing = [e for e in segments if e.get("outcome", {}).get("status") == P.RESULT_FAIL]
        if exhausted:
            signal = P.SIGNAL_REJECT
            feedback = f"重试已到上限（{config['retry_limit']}）的函数：{exhausted}\n\n" + _feedback(
                failing, items, state
            )
        else:
            signal = P.SIGNAL_BLOCK
            feedback = _feedback(failing, items, state)
    else:
        ambiguous = [
            e for e in segments
            if e["status"] == P.SEG_AMBIGUOUS and e["seg"].get("distinguishing")
        ]  # fmt: skip
        unknown_fns = sorted(f for f, i in functions.items() if i["result"] == P.RESULT_UNKNOWN)
        if config["require_disambiguation"] and ambiguous:  # 算法1 第30行
            signal = P.SIGNAL_PAUSE
            feedback = _pause_text(ambiguous, items)
        elif config["unknown_blocks"] and unknown_fns:  # 算法1 第31行
            signal = P.SIGNAL_BLOCK
            feedback = "以下函数的检查没有结论，需要能被检查的实现：\n" + "\n".join(
                f"- {f}：" + "；".join(o["reason"] for o in functions[f]["outcomes"] if o["status"] != P.RESULT_PASS)
                for f in unknown_fns
            )
        else:
            _write_contracts(store, segments)  # 算法1 第32行
    history["rounds"].append({"round": state["round"], "signal": signal, "failed": failed_fns})
    store.save("retries", retries)
    store.save("history", history)
    report = _report(store, state, plan, item_set, segments, functions, history, retries, signal)
    return _decide(state, signal, feedback=feedback, report=report)


def _feedback(failing: list[dict[str, Any]], items: dict[str, Any], state: dict[str, Any]) -> str:
    """阻止时的反馈: only the counterexample input and the violated requirement text (约束 3)."""
    lines = [f"交叉验证未通过（第 {state['round']} 轮）。以下要求被违反："]
    for entry in failing:
        item = items[entry["item"]]
        lines.append(f"\n- 需求 {item['id']}：{item['text']}")
        if _norm(entry["span"]).rstrip("。.；;") != _norm(item["text"]).rstrip("。.；;"):
            lines.append(f"  被违反的部分：{entry['span']}")
        failures = entry["outcome"]["failures"]
        for n, failure in enumerate(failures, start=1):
            ce = failure["counterexample"] or {}
            label = f"反例 {n}" if len(failures) > 1 else "反例"
            calls = ce.get("calls") or []
            if calls:
                lines.append(f"  {label}（按调用顺序）：")
                lines += [f"    {i}. {c}" for i, c in enumerate(calls, start=1)]
            elif ce.get("nondet_values"):
                lines.append(f"  {label}：输入 {', '.join(map(str, ce['nondet_values']))}")
            if ce.get("reason"):
                lines.append(f"  现象：{ce['reason']}")
    return "\n".join(lines)


def _pause_text(ambiguous: list[dict[str, Any]], items: dict[str, Any]) -> str:
    lines = ["以下要求有不止一种读法，需要你确定采用哪一种（把选择写成该条目的示例后重新运行）："]
    for entry in ambiguous:
        readings = [r["label"] or r["artifacts"][0]["id"] for r in entry["seg"]["readings"]]
        lines.append(f"\n- 需求 {entry['item']}：{items[entry['item']]['text']}")
        lines.append(f"  片段：{entry['span']}")
        lines.append(f"  读法：{' / '.join(readings)}")
        lines.append(f"  区分输入：{entry['seg']['distinguishing']}")
    return "\n".join(lines)


def _write_contracts(store: Store, segments: list[dict[str, Any]]) -> None:
    """契约库: the passing artifacts and the adopted readings, for compositional verification to use."""
    folder = store.project / CV / "contracts"
    folder.mkdir(parents=True, exist_ok=True)
    for entry in segments:
        outcome = entry.get("outcome")
        if not outcome or outcome["status"] != P.RESULT_PASS:
            continue
        contract = {
            "item": entry["item"],
            "segment": entry["segment"],
            "span": entry["span"],
            "functions": entry["functions"],
            "reading": outcome["reading"],
            "ambiguous": entry["status"] == P.SEG_AMBIGUOUS,
            "strength": outcome["strength"],
            "artifacts": outcome["adopted"]["artifacts"],
        }
        path = folder / f"{entry['item']}.{entry['segment']}.json"
        path.write_text(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _bare_report(store: Store, state: dict[str, Any], note: str) -> dict[str, Any]:
    item_set = store.load("requirements", {})
    return {
        "round": state.get("round", 0),
        "item_set": item_set.get("id"),
        "item_set_status": item_set.get("status"),
        "unprocessed": item_set.get("unprocessed", []),
        "notes": [*state.get("notes", []), note],
        "segments": [],
        "functions": {},
    }


def _report(store, state, plan, item_set, segments, functions, history, retries, signal) -> dict[str, Any]:
    """算法1 第33行: coverage, statuses and results, readings and strengths, unknowns, first vs modified."""
    seg_rows = []
    for entry in segments:
        outcome = entry.get("outcome") or {}
        seg = entry["seg"]
        seg_rows.append(
            {
                "item": entry["item"],
                "segment": entry["segment"],
                "span": entry["span"],
                "functions": entry["functions"],
                "status": entry["status"],
                "result": outcome.get("status"),
                "strength": outcome.get("strength"),
                "reading": outcome.get("reading"),
                "reason": entry["reason"] or outcome.get("reason", ""),
                "readings": [
                    {
                        "label": r["label"],
                        "source": r["source"],
                        "artifacts": [{"id": a["id"], "form": a["form"], "kind": a["kind"]} for a in r["artifacts"]],
                    }
                    for r in seg["readings"]
                ],
                "removed": seg.get("removed", []),
                "notes": seg.get("notes", []),
            }
        )
    fn_rows = {}
    for fn, info in sorted(functions.items()):
        if info["result"] == P.RESULT_PASS:
            when = "修改后通过" if fn in history["ever_failed"] else "第一次比对即通过"
        else:
            when = None
        fn_rows[fn] = {"result": info["result"], "pass": when, "retries": retries.get(fn, 0)}
    coverage = {}
    for item_id, record in sorted(plan["items"].items()):
        statuses = [s["status"] for s in record["segments"]]
        coverage[item_id] = {s: statuses.count(s) for s in sorted(set(statuses))}
    unknowns = [
        {"item": r["item"], "segment": r["segment"], "reason": r["reason"]}
        for r in seg_rows
        if r["result"] == P.RESULT_UNKNOWN
    ]
    return {
        "signal": signal,
        "round": state["round"],
        "item_set": item_set["id"],
        "item_set_status": item_set["status"],
        "unprocessed": item_set["unprocessed"],
        "notes": [*state.get("notes", []), *plan.get("notes", [])],
        "coverage": coverage,
        "segments": seg_rows,
        "functions": fn_rows,
        "unknowns": unknowns,
        "conflicts": plan.get("conflicts", []),
        "rounds": history["rounds"],
    }


# --- final report ------------------------------------------------------------------------------


def render_report(state: dict[str, Any]) -> str:
    """The workflow's output: the last signal and the report, as Markdown."""
    report = state.get("report") or {}
    signal = state.get("signal") or P.SIGNAL_PASS
    lines = [f"# 交叉验证报告：{P.SIGNAL_NAMES_ZH[signal]}（{signal}）", ""]
    lines.append(
        f"- 需求条目集：{report.get('item_set')}（状态 {report.get('item_set_status')}），第 {report.get('round')} 轮"
    )
    if report.get("unprocessed"):
        lines.append(f"- 需求处理未能处理的部分：{report['unprocessed']}")
    lines += [f"- {n}" for n in report.get("notes", [])]
    if report.get("functions"):
        lines += ["", "## 接口函数", "", "| 函数 | 结果 | 通过方式 | 重试次数 |", "| --- | --- | --- | --- |"]
        for fn, row in report["functions"].items():
            lines.append(f"| `{fn}` | {row['result']} | {row['pass'] or '—'} | {row['retries']} |")
    if report.get("segments"):
        lines += [
            "",
            "## 要求",
            "",
            "| 条目.段 | 状态 | 结果 | 强度 | 采用的读法 | 说明 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for r in report["segments"]:
            note = "；".join(filter(None, [r["reason"], *r["notes"]])).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {r['item']}.{r['segment']} | {r['status']} | {r['result'] or '—'} | {r['strength'] or '—'} "
                f"| {r['reading'] or '—'} | {note[:300]} |"
            )
    if report.get("unknowns"):
        lines += ["", "## 未知（这些要求其实没有检查过）", ""]
        lines += [f"- {u['item']}.{u['segment']}：{u['reason']}" for u in report["unknowns"]]
    if report.get("conflicts"):
        lines += ["", "## 冲突", ""]
        lines += [f"- {c['subject']}：{', '.join(c['artifacts'])}" for c in report["conflicts"]]
    if state.get("feedback") and signal != P.SIGNAL_PASS:
        lines += ["", "## 说明", "", state["feedback"]]
    store = _store(state)
    store.save("last_report", {"signal": signal, "report": report, "feedback": state.get("feedback", "")})
    (store.project / CV / "report.json").write_text(
        json.dumps({"signal": signal, "report": report}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return "\n".join(lines) + "\n"


# --- command line ------------------------------------------------------------------------------


def _read(path: str | None) -> str | None:
    return Path(path).read_text(encoding="utf-8") if path else None


def main(argv: list[str] | None = None) -> int:
    """Offline use, without iCode: plan from recorded generator answers, then judge a completion."""
    parser = argparse.ArgumentParser(prog="judge.py", description="cross-validation judge (算法 1)")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="plan phase from recorded generator answers")
    plan.add_argument("--project", required=True)
    plan.add_argument("--requirements", default="", help="item set JSON (default .cv/requirements.json)")
    plan.add_argument("--segments", required=True)
    plan.add_argument("--source-a", required=True)
    plan.add_argument("--source-b", required=True)
    plan.add_argument("--rewrite-a")
    plan.add_argument("--rewrite-b")
    done = sub.add_parser("complete", help="completion phase for the newest manifest")
    done.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        state = intake(args.requirements, args.project)
        state = plan_prepare(state, "plan")
        if state["has_work"]:
            state = plan_requests(state, _read(args.segments))
            state = plan_check(state, _read(args.source_a), _read(args.source_b))
        state = plan_freeze(state, _read(args.rewrite_a), _read(args.rewrite_b))
        sys.stdout.write(json.dumps(state["plan_summary"], ensure_ascii=False) + "\n")
        return 0
    state = {"project": str(Path(args.project).resolve()), "round": 0, "notes": []}
    state = verify(state)
    state = compare(state)
    sys.stdout.write(render_report(state))
    return 0 if state["signal"] == P.SIGNAL_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
