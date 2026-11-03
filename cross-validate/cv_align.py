"""The alignment agent's checks.json: format, coverage and freezing (README 第 11 节; 算法1 第6行)."""

from __future__ import annotations

import json
from pathlib import Path

import cv_state as S
import cv_verify as V

KINDS = ("command", "trace", "verify")
PROGRAM_JUDGED = ("trace", "verify")  # 细则 11.5: the program judges the generated formula


def load(align_dir: Path) -> tuple[dict | None, str | None]:
    # 细则 11.2: no links in align/, whose targets could change after freezing (细则 5.2).
    links = sorted(str(x.relative_to(align_dir)) for x in Path(align_dir).rglob("*") if x.is_symlink())
    if links:
        return None, f"align/ 中不得有符号链接：{'、'.join(links[:5])}"
    # 细则 11.11: only the headers the checker generates may answer `#include <cv_Rn.h>`.
    shadows = sorted(str(x.relative_to(align_dir)) for x in Path(align_dir).rglob("cv_*.h"))
    if shadows:
        return None, f"align/ 中不得有名为 cv_*.h 的文件，它们由程序生成：{'、'.join(shadows[:5])}"
    path = Path(align_dir) / "checks.json"
    if not path.exists():
        return None, "对齐 agent 没有写出 align/checks.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"align/checks.json 不能解析：{e}"


def validate(data, props: dict[str, dict], limits: dict, entities: set[str] | frozenset = frozenset()) -> dict:
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
            errors.append(f"{where}.kind 只能是 command、trace 或 verify")
            continue
        if kind == "verify":
            check, problem = _verify_check(item, props[pid], limits, entities)
            if problem:
                errors.append(f"{where}：{problem}")
                continue
            checks.append({"index": len(checks), **check})
            continue
        if not isinstance(command, str) or not command.strip():
            errors.append(f"{where}.command 应是非空字符串")
            continue
        if "\0" in command:
            errors.append(f"{where}.command 不得含空字符（细则 11.8）")
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
        unstated = item.get("unstated_inputs", False)
        if not isinstance(unstated, bool):
            errors.append(f"{where}.unstated_inputs 只能是 true 或 false")
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
                "unstated_inputs": unstated,
            }
        )
    failures: dict[str, str] = {}
    for pid, prop in props.items():
        # 细则 11.6, 13.8: checks on inputs the requirement does not define are extra; they cover nothing.
        mine = [c for c in checks if c["proposition"] == pid and not c["unstated_inputs"]]
        if not mine:
            others = any(c["proposition"] == pid for c in checks)
            failures[pid] = "只有超出需求的输入的检查（unstated_inputs）" if others else "没有任何检查"
        elif prop["class"] == "时序" and not any(c["kind"] in PROGRAM_JUDGED for c in mine):
            failures[pid] = "时序命题既没有 trace 检查，也没有 verify 检查（细则 11.5）"
    return {"format_errors": errors, "checks": checks, "bindings": bindings, "align_failures": failures}


def _int(value, low: int, high: int | None = None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low and (high is None or value <= high)


def _verify_check(item: dict, prop: dict, limits: dict, entities) -> tuple[dict, str | None]:
    """细则 11.11 checks.json 中的写法."""
    unfit = V.problems(prop["tree"], set(entities)) if prop.get("tree") is not None else ["命题没有公式"]
    if unfit:
        return {}, f"{prop['id']} 不能用 verify 检查：{'；'.join(unfit)}"
    sources = item.get("sources")
    if not isinstance(sources, list) or not sources or not all(isinstance(x, str) and x for x in sources):
        return {}, "sources 应是非空的文件列表"
    if any(
        V.SOURCE.match(x) is None or ".." in Path(x.removeprefix("$CV_ALIGN/")).parts or x.startswith("/")
        for x in sources
    ):
        return {}, "sources 只能是 $CV_ALIGN/ 开头的文件或 W 中的相对路径，不得含 .."
    if all(x.startswith("$CV_ALIGN/") for x in sources):
        return {}, "sources 至少要有一个 W 中的文件：verify 检查的是主任务的代码，不是另写的模型"
    max_steps = limits.get("verify_max_steps", 50)
    if not _int(item.get("steps"), 1, max_steps):
        return {}, f"steps 应是 1 到 {max_steps} 的整数"
    if not _int(item.get("unwind"), 1):
        return {}, "unwind 应是正整数"
    cap = limits.get("verify_timeout", 300)
    timeout = item.get("timeout", cap)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= cap:
        return {}, f"timeout 应是不超过 {cap} 的正数"
    unstated = item.get("unstated_inputs", False)
    if not isinstance(unstated, bool):
        return {}, "unstated_inputs 只能是 true 或 false"
    form = item.get("form", "")
    return {
        "proposition": prop["id"],
        "form": form if isinstance(form, str) else "",
        "kind": "verify",
        "command": "",
        "sources": sources,
        "steps": item["steps"],
        "unwind": item["unwind"],
        "runs": 1,
        "timeout": timeout,
        "unstated_inputs": unstated,
    }, None


def freeze(run: Path, result: dict) -> None:
    """细则 11.9, 5.2: the checker runs the frozen copy, never the agent's file."""
    frozen = {"bindings": result["bindings"], "checks": result["checks"], "align_failures": result["align_failures"]}
    S.write_json(Path(run) / "align" / "checks.frozen.json", frozen)
