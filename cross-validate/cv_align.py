"""The alignment agent's checks.json: format, coverage and freezing (README 第 11 节; 算法1 第6行)."""

from __future__ import annotations

import json
from pathlib import Path

import cv_state as S

KINDS = ("command", "trace")


def load(align_dir: Path) -> tuple[dict | None, str | None]:
    path = Path(align_dir) / "checks.json"
    if not path.exists():
        return None, "对齐 agent 没有写出 align/checks.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"align/checks.json 不能解析：{e}"


def validate(data, props: dict[str, dict], limits: dict) -> dict:
    """细则 11.2–11.6. `props` are the propositions handed to the alignment agent, by id.

    Format errors go back to the agent once (细则 11.7); missing coverage is an alignment failure of
    that proposition (细则 11.6) and is never reported back.
    """
    errors: list[str] = []
    checks: list[dict] = []
    if not isinstance(data, dict):
        return {"format_errors": ["checks.json 应是一个对象"], "checks": [], "bindings": {}, "align_failures": {}}
    bindings = data.get("bindings", {})
    if not isinstance(bindings, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in bindings.items()
    ):
        errors.append("bindings 应是从名称到文字说明的对象")
        bindings = {}
    items = data.get("checks")
    if not isinstance(items, list):
        errors.append("checks 应是数组")
        items = []
    for k, item in enumerate(items):
        where = f"checks[{k}]"
        if not isinstance(item, dict):
            errors.append(f"{where} 应是对象")
            continue
        pid, kind, command = item.get("proposition"), item.get("kind"), item.get("command")
        if pid not in props:
            errors.append(f"{where}.proposition 不是交给你的命题编号：{pid!r}")
            continue
        if kind not in KINDS:
            errors.append(f"{where}.kind 只能是 command 或 trace")
            continue
        if not isinstance(command, str) or not command.strip():
            errors.append(f"{where}.command 应是非空字符串")
            continue
        if kind == "trace" and props[pid]["class"] == "结构":
            errors.append(f"{where}：结构命题 {pid} 不能用 trace 检查")
            continue
        runs = item.get("runs", 1)
        if kind == "trace" and (
            not isinstance(runs, int) or isinstance(runs, bool) or not 1 <= runs <= limits["max_runs"]
        ):
            errors.append(f"{where}.runs 应是 1 到 {limits['max_runs']} 的整数")
            continue
        timeout = item.get("timeout", limits["check_timeout"])
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < timeout <= limits["check_timeout"]
        ):
            errors.append(f"{where}.timeout 应是不超过 {limits['check_timeout']} 的正数")
            continue
        form = item.get("form", "")
        checks.append(
            {
                "index": len(checks),
                "proposition": pid,
                "form": form if isinstance(form, str) else "",
                "kind": kind,
                "command": command,
                "runs": runs if kind == "trace" else 1,
                "timeout": timeout,
            }
        )
    failures: dict[str, str] = {}
    for pid, prop in props.items():
        mine = [c for c in checks if c["proposition"] == pid]
        if not mine:
            failures[pid] = "没有任何检查"
        elif prop["class"] == "时序" and not any(c["kind"] == "trace" for c in mine):
            failures[pid] = "时序命题缺少 trace 检查（细则 11.5）"
    return {"format_errors": errors, "checks": checks, "bindings": bindings, "align_failures": failures}


def freeze(run: Path, result: dict) -> None:
    """细则 11.9, 5.2: the checker runs the frozen copy, never the agent's file."""
    frozen = {"bindings": result["bindings"], "checks": result["checks"], "align_failures": result["align_failures"]}
    S.write_json(Path(run) / "align" / "checks.frozen.json", frozen)
