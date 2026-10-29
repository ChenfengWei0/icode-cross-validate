"""What each workflow node does (README 第 4 节). `cross-validate.py` only wires these into a graph.

Node state is a small dict {run, mode, w, ...}; everything else is on disk in the run directory,
outside the project (细则 3.1).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import cv_align as A
import cv_check as C
import cv_ledger as L
import cv_monitor as M
import cv_report as RP
import cv_run as RUN
import cv_state as S
import cv_text as T
from cv_formula import Decls

HERE = Path(__file__).resolve().parent
RUN_PREFIX = "@cv-run:"
MODE_GOAL, MODE_ACCUM = "目标", "积累"


def load_config() -> dict:
    return json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def prompt(name: str, **values: str) -> str:
    text = (HERE / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _note(state: dict, text: str) -> None:
    state.setdefault("notes", []).append(text)


def _invalid(state: dict, text: str) -> None:
    if text not in state.setdefault("invalid", []):
        state["invalid"].append(text)


# ---------------------------------------------------------------------------------------------------
# Intake (细则 4.1, 5.1)
# ---------------------------------------------------------------------------------------------------


def check_installed(workflow_dir: Path) -> None:
    """细则 5.1: only the globally installed copy may run; a project copy would shadow it."""
    installed = S.global_workflow_dir().resolve()
    if Path(workflow_dir).resolve() != installed:
        raise RuntimeError(f"交叉验证工作流必须从全局目录 {installed} 运行，当前运行的是 {workflow_dir}（细则 5.1）")


def code_version(w: Path) -> dict:
    """细则 14.1 第 2 项: the commit, and whether there are uncommitted changes, or a file digest."""

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=w, capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    head = git("rev-parse", "--short", "HEAD")
    if head is None:
        inside = git("rev-parse", "--is-inside-work-tree") == "true"
        return {
            "version": ("git 仓库，尚无提交" if inside else "不是 git 仓库") + "，文件摘要 " + S.folder_digest(w)[:12]
        }
    dirty = git("status", "--porcelain")
    if dirty:
        return {"version": f"提交 {head}（含未提交改动：{len(dirty.splitlines())} 个文件）"}
    return {"version": f"提交 {head}"}


def intake(text: str, cwd: str, workflow_dir: Path) -> dict:
    check_installed(workflow_dir)
    cwd = os.path.realpath(cwd)
    if text.startswith(RUN_PREFIX):
        run = Path(text[len(RUN_PREFIX) :].strip()).resolve()
        projects = (S.cv_home() / "projects").resolve()
        info = S.read_json(run / "run.json") if run.is_dir() else None
        if info is None or run.parent.name != "runs" or run.parent.parent.parent != projects:
            raise RuntimeError(f"{run} 不是任何已启用项目的运行目录（细则 4.1）")
        if os.path.realpath(info.get("snapshot", "")) != cwd:
            raise RuntimeError("积累模式必须在启动器做的快照中运行（细则 4.3）")
        return {"run": str(run), "mode": MODE_ACCUM, "w": cwd, "started": _now(), "workflow_dir": str(workflow_dir)}
    if not text.strip():
        raise RuntimeError("目标为空：请输入要实现的目标，它同时是交叉验证的需求")
    run = S.create_run(cwd)
    S.write_json(
        run / "run.json",
        {"mode": MODE_GOAL, "trigger": "目标模式", "project": cwd, "snapshot": None, "started": _now()},
    )
    S.write_text(run / "goal.txt", text)
    return {"run": str(run), "mode": MODE_GOAL, "w": cwd, "started": _now(), "workflow_dir": str(workflow_dir)}


def after_implement(state: dict) -> dict:
    """细则 4.2: the main agent ended normally, the goal is reached; R is the goal text itself."""
    run = Path(state["run"])
    info = S.read_json(run / "run.json")
    info["code"] = code_version(Path(state["w"]))
    S.write_json(run / "run.json", info)
    goal = (run / "goal.txt").read_text(encoding="utf-8")
    req, txt = L.build_requirement([{"text": goal, "quotes": [{"from": "目标", "text": goal}]}], {}, MODE_GOAL)
    S.write_json(run / "requirement.json", req)
    S.write_text(run / "requirement.txt", txt)
    S.record_digests(run, "requirement")
    return state


# ---------------------------------------------------------------------------------------------------
# Tool calls of the subtask agent (细则 5.3)
# ---------------------------------------------------------------------------------------------------


def violations(state: dict) -> list[dict]:
    found = _lines(Path(state["run"]) / "tool-violations.jsonl")
    for line in _lines(S.cv_home() / "tool-violations.jsonl"):
        if os.path.realpath(line.get("cwd", "")) == state["w"] and line.get("time", "") >= state["started"]:
            found.append(line)
    return found


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [_parse_line(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]


def _parse_line(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def check_violations(state: dict) -> None:
    found = violations(state)
    if found:
        names = sorted({str(v.get("tool", "?")) for v in found})
        _invalid(state, f"子任务 agent 调用了工具 {'、'.join(names)}（细则 5.3）")


# ---------------------------------------------------------------------------------------------------
# Consolidation (细则 15.4)
# ---------------------------------------------------------------------------------------------------


def consolidate_prompt(state: dict) -> str:
    messages = L.read_messages(Path(state["run"]) / "ledger.jsonl")
    return prompt("consolidate", MESSAGES=L.consolidation_input(messages))


def consolidate_check(state: dict, answer: str, attempt: int) -> tuple[dict, str | None]:
    run = Path(state["run"])
    check_violations(state)
    S.write_text(run / f"consolidate-{attempt}.txt", answer)
    messages = L.read_messages(run / "ledger.jsonl")
    data, errors = L.check_consolidation(answer, messages)
    if errors:
        if attempt == 1:
            return state, prompt(
                "revise", WHAT="需求整理", ERRORS=_bullets(errors), PREVIOUS=answer, TASK=consolidate_prompt(state)
            )
        state["stopped"] = "需求整理不合格：" + "；".join(errors[:5])
        return state, None
    req, txt = L.build_requirement(data["entries"], data["messages"], MODE_ACCUM)
    req["preprocessed"] = [m["id"] for m in messages if m.get("replaced")]
    S.write_json(run / "requirement.json", req)
    S.write_text(run / "requirement.txt", txt)
    S.record_digests(run, "requirement")
    if req["preprocessed"]:
        _note(state, f"预处理替换了消息 {'、'.join(req['preprocessed'])} 中粘贴的代码、日志或路径（细则 15.3）")
    return state, None


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items)


# ---------------------------------------------------------------------------------------------------
# 算法1 第1行: split and classify
# ---------------------------------------------------------------------------------------------------


def split_prompt(state: dict) -> str:
    return prompt("split", REQUIREMENT=(Path(state["run"]) / "requirement.txt").read_text(encoding="utf-8"))


def split_check(state: dict, answer: str, attempt: int) -> tuple[dict, str | None]:  # 算法1 第1行
    run = Path(state["run"])
    check_violations(state)
    requirement = (run / "requirement.txt").read_text(encoding="utf-8")
    sentences, errors = T.check_split(answer, requirement)
    if errors and attempt == 1:
        return state, prompt(
            "revise", WHAT="切句与分类", ERRORS=_bullets(errors), PREVIOUS=answer, TASK=split_prompt(state)
        )
    if errors:
        sentences = T.fallback_split(requirement)
        _note(state, "切句兜底：子任务 agent 两次都没有切对，由程序按标点切句，本次没有翻译命题（细则 6.1、17.1）")
    entries = S.read_json(run / "requirement.json")["entries"]
    goal = S.read_json(run / "requirement.json")["mode"] == MODE_GOAL
    for s in sentences:
        s["quotable"] = C.quotable_for(s, None if goal else entries)
    S.write_json(run / "sentences.json", sentences)
    return state, None


# ---------------------------------------------------------------------------------------------------
# 算法1 第2–5行: translate, reason, check, generate formulas
# ---------------------------------------------------------------------------------------------------


def _sentences_text(sentences: list[dict]) -> str:
    lines = []
    for s in sentences:
        lines.append(f"{s['id']}〔{'、'.join(s['labels'])}〕{s['text'].strip()}")
        if s["labels"] != [T.NO_LABEL] and s["quotable"] != [s["text"]]:
            lines.extend(f"    可引用的原话：「{q}」" for q in s["quotable"])
    return "\n".join(lines)


def propose_prompt(state: dict) -> str:  # 算法1 第2行；算法1 第3行（翻译与推理在同一次调用中）
    sentences = S.read_json(Path(state["run"]) / "sentences.json")
    return prompt("propose", SENTENCES=_sentences_text(sentences))


def _original(run: Path) -> str:
    req = S.read_json(run / "requirement.json")
    return "\n".join(q["text"] for e in req["entries"] for q in e["quotes"])


def propose_check(state: dict, answer: str, attempt: int) -> tuple[dict, str | None]:  # 算法1 第4行；算法1 第5行
    run = Path(state["run"])
    check_violations(state)
    S.write_text(run / ("subtask.txt" if attempt == 1 else "subtask-revised.txt"), answer)
    sentences = S.read_json(run / "sentences.json")
    original = _original(run)
    parsed = T.parse_subtask(answer, original)
    if attempt == 2 and state.get("revising") == "propositions":
        parsed = _merge_revision(run, parsed)
    if parsed["errors"]:
        if attempt == 1:
            state["revising"] = "all"
            errors = _bullets(parsed["errors"] + parsed["block_errors"])
            return state, prompt(
                "revise", WHAT="声明、清单与命题", ERRORS=errors, PREVIOUS=answer, TASK=propose_prompt(state)
            )
        state["stopped"] = "子任务输出的声明或清单不合格：" + "；".join(parsed["errors"][:5])
        return state, None
    checked = C.check_propositions(parsed, sentences, original)
    bad = [p for p in checked if p["problems"]]
    if bad and attempt == 1:
        state["revising"] = "propositions"
        _save_propositions(run, parsed, checked)
        listing = "\n\n".join(f"{p['id']}　{p['sentence']}\n" + _bullets(p["problems"]) for p in bad)
        return state, prompt("revise_propositions", PROBLEMS=listing, PREVIOUS=answer, TASK=propose_prompt(state))
    _save_propositions(run, parsed, checked)
    S.record_digests(run, "propositions")
    return state, None


def _merge_revision(run: Path, revised: dict) -> dict:
    """细则 9.3: keep the first output; take only the rewritten propositions and new declarations."""
    first = S.read_json(run / "propositions.json")
    decls = Decls.from_json(first["decls"])
    for name, cat in revised["decls"].category.items():
        if name not in decls.category:
            decls.category[name] = cat
            decls.params[name] = revised["decls"].params[name]
            decls.meaning[name] = revised["decls"].meaning[name]
    bad_ids = {p["id"] for p in first["propositions"] if p["problems"]}
    raw = {p["编号"]: p for p in first["raw"]}
    for p in revised["propositions"]:
        if p.get("编号") in bad_ids:
            raw[p["编号"]] = p
    return {
        "decls": decls,
        "checklist": first["checklist"],
        "propositions": list(raw.values()),
        "errors": [],
        "block_errors": [],
    }


def _save_propositions(run: Path, parsed: dict, checked: list[dict]) -> None:
    for p in checked:
        if p.get("tree") is not None and not p["problems"]:
            p["liveness"] = p["class"] == "时序" and M.is_liveness(p["tree"])
    S.write_json(
        run / "propositions.json",
        {
            "decls": parsed["decls"].to_json(),
            "checklist": parsed["checklist"],
            "raw": parsed["propositions"],
            "propositions": checked,
        },
    )


# ---------------------------------------------------------------------------------------------------
# 算法1 第6行: the alignment agent
# ---------------------------------------------------------------------------------------------------


def _accepted(run: Path) -> list[dict]:
    data = S.read_json(run / "propositions.json") or {}
    return [p for p in data.get("propositions", []) if not p["problems"]]


def _decl_text(decls: Decls) -> str:
    lines = []
    for name, cat in decls.category.items():
        params = decls.params.get(name) or []
        sig = f"{name}({', '.join(params)})" if params else name
        lines.append(f"{cat}  {sig}  {decls.meaning.get(name, '')}".rstrip())
    return "\n".join(lines)


def align_prompt(state: dict) -> str:  # 算法1 第6行
    run = Path(state["run"])
    data = S.read_json(run / "propositions.json")
    props = _accepted(run)
    listing = "\n".join(f"{p['id']}  〔{p['class']}〕  {p['sentence']}\n      公式  {p['formula']}" for p in props)
    S.ensure_private(run / "align")
    S.write_json(run / "w-before.json", S.tree_digests(Path(state["w"])))
    return prompt(
        "align",
        DECLS=_decl_text(Decls.from_json(data["decls"])),
        PROPOSITIONS=listing,
        W=state["w"],
        ALIGN=str(run / "align"),
    )


def align_check(state: dict, answer: str, attempt: int, config: dict) -> tuple[dict, str | None]:
    run = Path(state["run"])
    S.write_text(run / f"align-answer-{attempt}.txt", answer)
    props = {p["id"]: p for p in _accepted(run)}
    data, error = A.load(run / "align")
    result = A.validate(data, props, config) if error is None else {"format_errors": [error]}
    if result["format_errors"] and attempt == 1:
        return state, prompt("align_fix", ERRORS=_bullets(result["format_errors"]), ALIGN=str(run / "align"))
    if result["format_errors"]:
        why = "checks.json 格式不合法：" + "；".join(result["format_errors"][:3])
        result = {"checks": [], "bindings": {}, "align_failures": dict.fromkeys(props, why)}
    A.freeze(run, result)
    S.record_digests(run, "checks", {"workflow": S.folder_digest(Path(state["workflow_dir"]))})
    return state, None


# ---------------------------------------------------------------------------------------------------
# 算法1 第7行: the checker
# ---------------------------------------------------------------------------------------------------


def _integrity(state: dict) -> None:
    run = Path(state["run"])
    changed = S.verify_digests(run, Path(state["workflow_dir"]))
    if changed:
        _invalid(state, f"检查期间被改动：{'、'.join(changed)}（细则 5.2）")
    before = S.read_json(run / "w-before.json")
    if before is not None:
        diff = S.compare_trees(before, S.tree_digests(Path(state["w"])))
        if diff["modified"] or diff["deleted"]:
            files = diff["modified"] + diff["deleted"]
            _invalid(state, f"W 中原有的文件被修改或删除：{'、'.join(files[:10])}（细则 5.4）")
        state["w_changes"] = diff


def run_checks(state: dict, config: dict) -> dict:  # 算法1 第7行
    run = Path(state["run"])
    if state.get("stopped") or not (run / "align" / "checks.frozen.json").exists():
        return state
    _integrity(state)
    if state.get("invalid"):
        return state
    data = S.read_json(run / "propositions.json")
    props = {p["id"]: p for p in _accepted(run)}
    entities = Decls.from_json(data["decls"]).of("实体")
    frozen = S.read_json(run / "align" / "checks.frozen.json")
    results = RUN.run_checks(run, Path(state["w"]), props, frozen, entities, config)
    S.write_json(run / "results.json", results)
    _integrity(state)
    return state


# ---------------------------------------------------------------------------------------------------
# 算法1 第8行: the report
# ---------------------------------------------------------------------------------------------------


def suspect_names(run: Path, w: Path) -> list[str]:
    """细则 14.1 第 1 项: ASCII names in R that also occur in W's code."""
    req = S.read_json(run / "requirement.json") or {}
    names = sorted({n for e in req.get("entries", []) for n in RP.ASCII_NAME.findall(e["text"])})
    if not names:
        return []
    found: set[str] = set()
    for rel in S.tree_digests(w):
        path = w / rel
        if path.suffix in (".md", ".txt") or path.stat().st_size > 1 << 20:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found |= {n for n in names if n in text}
    return sorted(found)


def report(state: dict, config: dict) -> str:  # 算法1 第8行
    run = Path(state["run"])
    check_violations(state)
    if (run / "align" / "checks.frozen.json").exists():
        _integrity(state)
    extra = {
        "invalid": state.get("invalid", []),
        "stopped": state.get("stopped"),
        "notes": state.get("notes", []),
        "w_changes": state.get("w_changes", {}),
        "suspect_names": suspect_names(run, Path(state["w"])) if (run / "requirement.json").exists() else [],
    }
    return RP.write(run, extra, config)
