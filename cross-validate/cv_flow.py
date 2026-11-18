"""What each workflow node does (README 第 4 节). `cross-validate.py` only wires these into a graph.

Node state is a small dict {run, mode, w, ...}; everything else is on disk in the run directory,
outside the project (细则 3.1).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import cv_align as A
import cv_check as C
import cv_io as IO
import cv_ledger as L
import cv_monitor as M
import cv_report as RP
import cv_run as RUN
import cv_state as S
import cv_text as T
import cv_verify as V
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


def check_installed(workflow_dir: Path) -> str:
    """细则 5.1: only the globally installed copy may run, unchanged since `cv install`. Returns its digest."""
    installed = S.global_workflow_dir().resolve()
    if Path(workflow_dir).resolve() != installed:
        raise RuntimeError(f"交叉验证工作流必须从全局目录 {installed} 运行，当前运行的是 {workflow_dir}（细则 5.1）")
    record = S.read_json(S.cv_home() / "install.json", {}) or {}
    digest = S.folder_digest(installed)
    if not record.get("workflow_digest"):
        raise RuntimeError("没有安装记录 install.json，请用 cv install 安装（细则 5.1）")
    if record["workflow_digest"] != digest:
        raise RuntimeError("安装后的工作流文件夹被改动过，请重新运行 cv install（细则 5.1）")
    return digest


def code_version(w: Path) -> dict:
    """细则 14.1 第 2 项: the commit, and whether there are uncommitted changes, or a file digest."""

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=w,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    head = git("rev-parse", "--short", "HEAD")
    if head is None:
        inside = git("rev-parse", "--is-inside-work-tree") == "true"
        return {
            "version": ("git 仓库，尚无提交" if inside else "不是 git 仓库") + "，文件摘要 " + S.folder_digest(w)[:12]
        }
    dirty = git("--no-optional-locks", "status", "--porcelain")  # 细则 16.4: never touch the project's .git
    if dirty:
        return {"version": f"提交 {head}（含未提交改动：{len(dirty.splitlines())} 个文件）"}
    return {"version": f"提交 {head}"}


def intake(text: str, cwd: str, workflow_dir: Path) -> dict:
    digests = {S.WORKFLOW: check_installed(workflow_dir)}
    cwd = os.path.realpath(cwd)
    if text.startswith(RUN_PREFIX):
        run = Path(text[len(RUN_PREFIX) :].strip()).resolve()
        projects = (S.cv_home() / "projects").resolve()
        info = S.read_json(run / "run.json") if run.is_dir() else None
        if info is None or run.parent.name != "runs" or run.parent.parent.parent != projects:
            raise RuntimeError(f"{run} 不是任何已启用项目的运行目录（细则 4.1）")
        if os.path.realpath(info.get("snapshot", "")) != cwd:
            raise RuntimeError("积累模式必须在启动器做的快照中运行（细则 4.3）")
        return {
            "run": str(run),
            "mode": MODE_ACCUM,
            "w": cwd,
            "started": _now(),
            "workflow_dir": str(workflow_dir),
            "digests": digests,
        }
    if not text.strip():
        raise RuntimeError("目标为空：请输入要实现的目标，它同时是交叉验证的需求")
    run = S.create_run(cwd)
    info = {"mode": MODE_GOAL, "trigger": "目标模式", "project": cwd, "snapshot": None, "started": _now()}
    io = (S.read_json(S.project_dir(cwd) / "project.json", {}) or {}).get("settings", {}).get("io") or {}
    if io.get("command"):  # 细则 11.12: frozen with run.json
        info["io"] = {k: v for k, v in io.items() if k in ("command", "protocol", "box_args")}
    S.write_json(run / "run.json", info)
    S.write_text(run / "goal.txt", text)
    return {
        "run": str(run),
        "mode": MODE_GOAL,
        "w": cwd,
        "started": _now(),
        "workflow_dir": str(workflow_dir),
        "digests": digests,
    }


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
    state["digests"] = S.record_digests(run, "requirement", state.get("digests"))
    return state


# ---------------------------------------------------------------------------------------------------
# Tool calls of the subtask agent (细则 5.3)
# ---------------------------------------------------------------------------------------------------


def violations(state: dict) -> list[dict]:
    found = _lines(Path(state["run"]) / "tool-violations.jsonl")
    for line in _lines(S.cv_home() / "tool-violations.jsonl"):
        cwd, when = line.get("cwd"), line.get("time")
        if not isinstance(when, str) or when < state["started"]:
            continue
        # A record without a usable cwd cannot be placed; it counts against every run (fail closed).
        if not isinstance(cwd, str) or os.path.realpath(cwd) == state["w"]:
            found.append(line)
    return found


GUARD_ID = "cv-subtask-guard"


def _icode(config: dict) -> str:
    """细则 5.3: CV_ICODE, else config's icode on PATH, else the icode beside iCode's own Python."""
    named = os.environ.get("CV_ICODE") or config.get("icode") or "icode"
    found = shutil.which(named)
    if found:
        return found
    beside = Path(sys.executable).parent / "icode"
    return str(beside) if beside.exists() else named


def guard_problem(cwd: str, config: dict) -> str | None:
    """细则 5.3: why iCode would not load the blocking guard of the subtask agent in cwd, or None."""
    try:
        out = subprocess.run(
            [_icode(config), "hook", "list", "--json"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"无法运行 icode hook list：{e}"
    if out.returncode != 0:
        return f"icode hook list 失败：{(out.stderr or out.stdout).strip()[-300:]}"
    try:
        files = json.loads(out.stdout)["files"]
        found = [(f, h) for f in files for h in f.get("hooks") or [] if h.get("id") == GUARD_ID]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return "icode hook list --json 的输出无法解析"
    if not found:
        return f"iCode 没有配置 hook {GUARD_ID}（请运行 cv install）"
    problems = []
    for f, h in found:
        execution, match, run = h.get("execution") or {}, h.get("match") or {}, h.get("run") or {}
        if f.get("state") != "loaded":
            problems.append(f"所在文件 {f.get('path')} 没有加载（{f.get('state')}）")
        elif h.get("enabled") is not True:
            problems.append("被停用")
        elif h.get("event") != "before_tool_call":
            problems.append(f"事件是 {h.get('event')}")
        elif execution.get("mode") != "blocking" or execution.get("on_error") != "block":
            problems.append(f"执行方式是 {execution.get('mode')}、出错时 {execution.get('on_error')}")
        elif match.get("profile") != "cv-subtask" or any(
            match.get(k) for k in ("profiles", "tool_kind", "tool_name", "args")
        ):
            problems.append("匹配条件不是只限定 cv-subtask")
        elif run.get("type") != "command" or not run.get("program"):
            problems.append("运行方式不是命令，调用不会被记录")
        else:
            return None
    return f"hook {GUARD_ID} 不会拦截子任务 agent 的工具调用：{'；'.join(problems)}（请运行 cv install）"


def check_guard(state: dict, config: dict) -> dict:
    """细则 5.3: at intake, before any agent, the guard must be loaded and blocking, else the run is invalid."""
    problem = guard_problem(state["w"], config)
    if problem:
        _invalid(state, f"{problem}（细则 5.3）")
    return state


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [_parse_line(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw.strip()]


def _parse_line(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def history_reads(state: dict) -> int:
    """细则 5.6: blocked tool calls that touched the state directory during this run, from its W."""
    return sum(
        1
        for line in _lines(S.cv_home() / "history-reads.jsonl")
        if isinstance(line.get("time"), str)
        and line["time"] >= state["started"]
        and isinstance(line.get("cwd"), str)
        and os.path.realpath(line["cwd"]) == state["w"]
    )


def _tool_names(records: list[dict]) -> str:
    return "、".join(sorted({str(v.get("tool", "?")) for v in records}))


def check_violations(state: dict) -> None:
    """细则 5.3: records beyond those excused by a rerun make the run invalid."""
    found = violations(state)
    excused = state.get("tool_excused", 0)
    if len(found) > excused:
        _invalid(state, f"子任务 agent 调用了工具 {_tool_names(found[excused:])}（细则 5.3）")


RERUN_PREFIX = "你没有可用的工具，请直接依据下面的提示作答。\n\n"


def tool_gate(state: dict, node: str, task: str) -> str | None:
    """细则 5.3: calls during this node's answer void it; returns the prompt of the one rerun, or None."""
    found = violations(state)
    excused = state.get("tool_excused", 0)
    if len(found) <= excused:
        return None
    state["tool_excused"] = len(found)
    _note(
        state,
        f"子任务节点 {node} 的回答期间调用了工具 {_tool_names(found[excused:])}，已被拦下；这次回答作废，用新会话重跑一次（细则 5.3）",
    )
    return RERUN_PREFIX + task


# ---------------------------------------------------------------------------------------------------
# Consolidation (细则 15.4)
# ---------------------------------------------------------------------------------------------------


def consolidate_prompt(state: dict) -> str:
    messages = L.read_messages(Path(state["run"]) / "ledger.jsonl")
    return prompt("consolidate", MESSAGES=L.consolidation_input(messages))


NO_ANSWER = "子任务 agent 没有回答"


def consolidate_check(state: dict, answer: str | None, attempt: int) -> tuple[dict, str | None]:
    run = Path(state["run"])
    check_violations(state)
    if answer is None:  # 细则 4.8
        state["stopped"] = f"需求整理：{NO_ANSWER}（细则 4.8）"
        return state, None
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
    state["digests"] = S.record_digests(run, "requirement", state.get("digests"))
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


def split_check(state: dict, answer: str | None, attempt: int) -> tuple[dict, str | None]:  # 算法1 第1行
    run = Path(state["run"])
    check_violations(state)
    requirement = (run / "requirement.txt").read_text(encoding="utf-8")
    if answer is None:
        _note(state, f"切句：{NO_ANSWER}（细则 4.8）")
        sentences, errors = [], [NO_ANSWER]
        attempt = 2
    else:
        sentences, errors = T.check_split(answer, requirement)
    if errors and attempt == 1:
        return state, prompt(
            "revise", WHAT="切句与分类", ERRORS=_bullets(errors), PREVIOUS=answer, TASK=split_prompt(state)
        )
    if errors:
        sentences = T.fallback_split(requirement)
        _note(state, "切句兜底：子任务 agent 两次都没有切对，由程序按标点切句，本次没有翻译命题（细则 6.1、17.1）")
    else:
        entries = S.read_json(run / "requirement.json")["entries"]
        cuts = [e["start"] for e in entries if "start" in e] + [e["end"] for e in entries if "end" in e]
        sentences = T.merge_examples(sentences, requirement, cuts)  # 细则 6.4
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


def propose_check(state: dict, answer: str | None, attempt: int) -> tuple[dict, str | None]:  # 算法1 第4行；算法1 第5行
    run = Path(state["run"])
    check_violations(state)
    revising = state.get("revising")
    if answer is None:  # 细则 4.8
        _note(state, f"命题：{NO_ANSWER}（细则 4.8）")
        if revising == "propositions":
            state["digests"] = S.record_digests(run, "propositions", state.get("digests"))
        else:
            state["stopped"] = f"命题：{NO_ANSWER}（细则 4.8）"
        return state, None
    S.write_text(run / ("subtask.txt" if attempt == 1 else "subtask-revised.txt"), answer)
    sentences = S.read_json(run / "sentences.json")
    original = _original(run)
    parsed = T.parse_subtask(answer, original)
    if attempt == 2 and revising == "propositions":
        parsed = _merge_revision(state, run, parsed, original)
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
        listing = "\n\n".join(f"{p['id'] or '（无编号）'}　{p['sentence']}\n" + _bullets(p["problems"]) for p in bad)
        return state, prompt("revise_propositions", PROBLEMS=listing, PREVIOUS=answer, TASK=propose_prompt(state))
    _save_propositions(run, parsed, checked)
    state["digests"] = S.record_digests(run, "propositions", state.get("digests"))
    return state, None


def _merge_revision(state: dict, run: Path, revised: dict, original: str) -> dict:
    """细则 9.3: keep what passed; replace what failed by the rewritten propositions; check new names."""
    first = S.read_json(run / "propositions.json")
    decls = Decls.from_json(first["decls"])
    added = [n for n in revised["decls"].category if n not in decls.category]
    if added:
        trial = Decls.from_json(decls.to_json())
        for name in added:
            trial.category[name] = revised["decls"].category[name]
            trial.params[name] = revised["decls"].params[name]
            trial.meaning[name] = revised["decls"].meaning[name]
        for name in added:
            problems = T.declaration_problems(name, trial, original)
            if problems:
                _note(state, f"重写中新增的声明 {name} 不合格，没有加入：{'；'.join(problems)}（细则 9.3）")
                continue
            decls.category[name] = trial.category[name]
            decls.params[name] = trial.params[name]
            decls.meaning[name] = trial.meaning[name]
    # Keep what passed; every other id is taken from the rewrite as a whole, so duplicates and
    # renumbered blocks map by the agent's own numbering. Failed ids the rewrite leaves out stay failed.
    passed = {p["id"] for p in first["propositions"] if not p["problems"]}
    rewritten = [p for p in revised["propositions"] if p.get("编号", "") and p.get("编号") not in passed]
    named = {p["编号"] for p in rewritten}
    first_ids = {p.get("编号", "") for p in first["raw"]}
    renumbered = bool(named - first_ids)
    merged = [
        old
        for old, checked in zip(first["raw"], first["propositions"], strict=True)
        if (not checked["problems"] and old.get("编号") in passed)
        or (checked["problems"] and old.get("编号", "") not in named and not (old.get("编号", "") == "" and renumbered))
    ]
    # A failed block whose sentence reappears under a new id was renumbered (e.g. one of two R1s): drop it.
    moved = {p.get("句子", "").strip() for p in rewritten if p["编号"] not in first_ids}
    failed = {id(old) for old, checked in zip(first["raw"], first["propositions"], strict=True) if checked["problems"]}
    merged = [old for old in merged if not (id(old) in failed and old.get("句子", "").strip() in moved)]
    merged += rewritten
    # 细则 9.3: a renumbered reasoning proposition needs its id in the checklist; take both checklists.
    checklist = {k: dict(v) for k, v in first["checklist"].items()}
    for item, entry in revised["checklist"].items():
        if item in checklist and entry["ids"]:
            checklist[item]["ids"] = sorted(set(checklist[item]["ids"]) | set(entry["ids"]))
    return {"decls": decls, "checklist": checklist, "propositions": merged, "errors": [], "block_errors": []}


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
# 细则 7.5: the subtask reviews its accepted propositions, only to mark them
# ---------------------------------------------------------------------------------------------------

CONFLICT = "〔需求矛盾〕"
_ID = re.compile(r"R\d+")


def review_prompt(state: dict) -> str | None:
    run = Path(state["run"])
    data = S.read_json(run / "propositions.json") or {}
    props = [p for p in data.get("propositions", []) if not p["problems"]]
    if not props:
        return None
    listing = "\n\n".join(
        f"编号  {p['id']}\n来源  {p['source']}\n依据  {p['basis']}\n句子  {p['sentence']}" for p in props
    )
    return prompt(
        "review",
        REQUIREMENT=(run / "requirement.txt").read_text(encoding="utf-8"),
        DECLS=_decl_text(Decls.from_json(data["decls"])),
        PROPOSITIONS=listing,
    )


def _grounded(p: dict, requirement: str) -> list[str] | None:
    """细则 7.5: the verbatim quotes a side of a conflict rests on, or None when it rests on none."""
    said = re.sub(r"\s+", "", requirement)
    quotes = sorted({re.sub(r"\s+", "", q) for q in C.quotes_in(p["basis"])} - {""})
    quotes = [q for q in quotes if q in said]
    return quotes or None


def _grounded_side(p: dict, requirement: str) -> list[str] | None:
    if p["source"] == "翻译":
        return sorted({re.sub(r"\s+", "", x) for x in C.quotes_in(p["basis"])})
    return _grounded(p, requirement)


def parse_review(answer: str, props: dict[str, dict], requirement: str) -> tuple[list[dict], list[tuple[str, str]]]:
    """细则 7.5: the marks of a review answer, and (id, problem) for every line that is not a valid mark."""
    marks, problems = [], []
    lines = [ln.strip().strip("*").strip() for ln in answer.splitlines()]
    lines = [ln[2:].strip() if ln.startswith("- ") else ln for ln in lines if ln and not ln.startswith("```")]
    if [ln.rstrip("。.") for ln in lines] in ([], ["无"]):
        return [], []
    for line in lines:
        fields = [f.strip().strip("*").strip() for f in re.split(r"\s{2,}|\t", line) if f.strip()]
        pid = fields[0] if fields else ""
        if pid not in props:
            problems.append((pid, f"「{line[:60]}」：{pid or '（空）'} 不是通过检查的命题编号"))
            continue
        mark = fields[1] if len(fields) > 1 else ""
        if mark == C.UNSTATED:
            reason = "  ".join(fields[2:])
            if props[pid]["source"] != "推理":
                problems.append((pid, f"{pid}：{C.UNSTATED} 只能标推理命题，翻译逐字引用原话，原话本身就是规定"))
            elif not all(w in reason for w in ("可以", "也可以", "这里按")):
                problems.append((pid, f"{pid}：理由要按“需求没说……，可以……，也可以……，这里按……处理”写出两种读法"))
            else:
                marks.append({"id": pid, "mark": mark, "reason": reason})
        elif mark == CONFLICT:
            others = [x for x in re.split(r"[、,，\s]+", fields[2]) if x] if len(fields) > 2 else []
            reason = "  ".join(fields[3:])
            bad = [x for x in others if x not in props or x == pid]
            # 细则 7.5: a side with no verbatim quote is dropped; the rest stand when one of them is left.
            dropped = [x for x in others if x not in bad and _grounded_side(props[x], requirement) is None]
            others = [x for x in others if x not in dropped]
            if dropped and not others:
                problems.append(
                    (
                        pid,
                        f"{pid}：{dropped[0]} 的依据中没有需求原文的逐字引文；没有原话为据的命题与原话不符，是推理错了，不是需求矛盾",
                    )
                )
                continue
            group = [pid, *others]
            grounds = {q: _grounded_side(props[q], requirement) for q in group if q in props}
            if not others or bad:
                problems.append(
                    (
                        pid,
                        f"{pid}：{CONFLICT} 之后要列出与它矛盾的其他通过检查的命题编号"
                        + (f"，{bad[0]} 不是" if bad else ""),
                    )
                )
            elif not reason:
                problems.append((pid, f"{pid}：理由不能为空，要写出矛盾在哪里"))
            elif any(g is None for g in grounds.values()):
                q = next(q for q, g in grounds.items() if g is None)
                problems.append(
                    (
                        pid,
                        f"{pid}：{q} 的依据中没有需求原文的逐字引文；没有原话为据的命题与原话不符，是推理错了，不是需求矛盾",
                    )
                )
            elif len({tuple(g) for g in grounds.values()}) == 1:
                problems.append((pid, f"{pid}：矛盾的各方引用了同样的原话，不是需求自相矛盾"))
            else:
                marks.append(
                    {
                        "id": pid,
                        "mark": mark,
                        "with": others,
                        "reason": reason,
                        **({"dropped": dropped} if dropped else {}),
                    }
                )
        else:
            problems.append((pid, f"{pid}：标记只能是 {C.UNSTATED} 或 {CONFLICT}"))
    return marks, problems


def _apply_review(run: Path, marks: list[dict]) -> None:
    """Write the marks into propositions.json; a mark already there stays (细则 7.5)."""
    data = S.read_json(run / "propositions.json")
    by_id = {p["id"]: p for p in data["propositions"] if not p["problems"]}
    parent = {pid: pid for pid in by_id}

    def root(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    for m in marks:
        p = by_id[m["id"]]
        p.setdefault("review", []).append({k: m[k] for k in ("mark", "with", "reason") if k in m})
        if m["mark"] == C.UNSTATED:
            if not p.get("unstated"):
                p["unstated"], p["unstated_by"] = True, "review"
                p["basis"] += f"（复查：{C.UNSTATED}{m['reason']}）"
            continue
        for q in m["with"]:
            parent[root(q)] = root(m["id"])
            by_id[q].setdefault("review", []).append({"mark": m["mark"], "with": [m["id"]], "reason": m["reason"]})
    for p in by_id.values():  # earlier groups are kept: their members are joined first
        for q in p.get("conflict", []):
            parent[root(q)] = root(p["id"])
    groups: dict[str, set] = {}
    for pid in by_id:
        groups.setdefault(root(pid), set()).add(pid)
    for members in groups.values():
        if len(members) > 1:
            for q in members:
                by_id[q]["conflict"] = sorted(members - {q}, key=_num_id)
    S.write_json(run / "propositions.json", data)


def _num_id(pid: str) -> int:
    return int(pid[1:]) if pid[1:].isdigit() else 10**9


def _same(m: dict) -> str:
    return json.dumps([m["id"], m["mark"], sorted(m.get("with", []))], ensure_ascii=False)


def review_check(state: dict, answer: str | None, attempt: int) -> tuple[dict, str | None]:
    """细则 7.5: check the review's marks; the bad lines go back once, and only they are taken from the rewrite."""
    run = Path(state["run"])
    check_violations(state)
    earlier = state.pop("review_marks", None) or []
    redo = set(state.pop("review_redo_ids", None) or [])
    if answer is None:  # 细则 4.8
        _note(
            state, f"复查：{NO_ANSWER}，" + ("不合格的行被丢弃" if attempt == 2 else "按没有标记处理") + "（细则 7.5）"
        )
        marks = earlier
    else:
        S.write_text(run / ("review.txt" if attempt == 1 else "review-revised.txt"), answer)
        props = {p["id"]: p for p in _accepted(run)}
        requirement = (run / "requirement.txt").read_text(encoding="utf-8")
        marks, problems = parse_review(answer, props, requirement)
        if attempt == 2:
            # Only the lines sent back are taken from the rewrite, so it is not another round of review.
            marks = [m for m in marks if m["id"] in redo]
            problems = [(pid, why) for pid, why in problems if pid in redo]
        elif problems:
            state["review_marks"] = marks
            state["review_redo_ids"] = sorted({pid for pid, _ in problems})
            return state, prompt(
                "review_fix",
                ERRORS=_bullets([why for _, why in problems]),
                PREVIOUS=answer,
                TASK=review_prompt(state) or "",
            )
        if problems:
            _note(state, "复查中不合格的标记被丢弃：" + "；".join(why for _, why in problems[:5]) + "（细则 7.5）")
        marks = earlier + marks
        for m in marks:
            if m.get("dropped"):
                _note(state, f"复查：{m['id']} 的需求矛盾中去掉了没有原话为据的 {'、'.join(m['dropped'])}（细则 7.5）")
    unique = list({_same(m): m for m in marks}.values())
    if unique:
        _apply_review(run, unique)
        _note(state, f"复查标记了 {len(unique)} 项（细则 7.5）")
    # 细则 5.2: propositions.json is frozen again after the review.
    state["digests"] = S.record_digests(run, "propositions", state.get("digests"))
    return state, None


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
    # 细则 5.2, 5.4: frozen before the alignment agent starts, not after it has run.
    state["digests"] = S.record_digests(run, "align_start", state.get("digests"))
    return prompt(
        "align",
        DECLS=_decl_text(Decls.from_json(data["decls"])),
        PROPOSITIONS=listing,
        W=state["w"],
        ALIGN=str(run / "align"),
        VERIFY=_verify_note(run, props, Decls.from_json(data["decls"]).of("实体"), load_config()),
    )


TRIAL_STEPS = 6  # 细则 11.11: trials start small; a harness looping 50 steps exhausted the memory


def _verify_note(run: Path, props: list[dict], entities: set[str], config: dict) -> str:
    """细则 11.11: which propositions a verifier can decide, and the headers to write against (stubs)."""
    engine = config.get("verify_engine", "cbmc")
    if shutil.which(engine) is None:
        return f"环境中没有验证器 {engine}，不能使用 verify 检查。"
    ids = V.write_headers(run / "verify-headers", {p["id"]: p for p in props}, entities, TRIAL_STEPS, stubs=True)
    if not ids:
        return f"环境中有验证器 {engine}，但没有命题适合 verify 检查。"
    return (
        f"环境中有验证器 {engine}。下列命题可以用 verify 检查：{'、'.join(ids)}。"
        f"试验用的头文件在 `{run / 'verify-headers'}`（编译时加 `-I` 这个目录）；它们只有名字，不判定命题，"
        f"检查时程序换成真正的头文件。试验头文件的步数默认是 {TRIAL_STEPS}，可以用 `-DCV_Rn_STEPS=k` 改。"
    )


def _preflight(state: dict, run: Path, props: dict, result: dict, config: dict) -> list[str]:
    """细则 11.7: trouble in the trial run itself must not cost the run; the real checks still decide."""
    try:
        entities = Decls.from_json(S.read_json(run / "propositions.json")["decls"]).of("实体")
        return RUN.preflight(Path(state["w"]), run / "align", props, result["checks"], entities, config)
    except Exception as e:  # 细则 4.8: untrusted scripts must not crash the node
        _note(state, f"冻结前试跑出错，已跳过：{type(e).__name__}（细则 11.7）")
        return []


def align_check(state: dict, answer: str | None, attempt: int, config: dict) -> tuple[dict, str | None]:
    run = Path(state["run"])
    props = {p["id"]: p for p in _accepted(run)}
    if answer is None:  # 细则 4.8, 11.6
        _note(state, "对齐 agent 没有回答，所有命题记为对齐失败（细则 4.8）")
        result = {"checks": [], "bindings": {}, "align_failures": dict.fromkeys(props, "对齐 agent 没有回答")}
    else:
        S.write_text(run / f"align-answer-{attempt}.txt", answer)
        data, error = A.load(run / "align")
        entities = Decls.from_json(S.read_json(run / "propositions.json")["decls"]).of("实体")
        result = A.validate(data, props, config, entities) if error is None else {"format_errors": [error]}
        if not result["format_errors"] and attempt == 1:
            result["format_errors"] = _preflight(state, run, props, result, config)
        if result["format_errors"] and attempt == 1:
            return state, prompt("align_fix", ERRORS=_bullets(result["format_errors"]), ALIGN=str(run / "align"))
        if result["format_errors"]:
            why = "checks.json 格式不合法：" + "；".join(result["format_errors"][:3])
            result = {"checks": [], "bindings": {}, "align_failures": dict.fromkeys(props, why)}
    A.freeze(run, result)
    state["digests"] = S.record_digests(run, "checks", state.get("digests"))
    return state, None


# ---------------------------------------------------------------------------------------------------
# 算法1 第6行 for programs that only read input and write output (细则 11.12)
# ---------------------------------------------------------------------------------------------------


def io_settings(state: dict) -> dict | None:
    return (S.read_json(Path(state["run"]) / "run.json", {}) or {}).get("io")


def _io_props(run: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """Propositions for io alignment, and those dropped before it because no one reads the code."""
    props, dropped = {}, {}
    for p in _accepted(run):
        if IO.struct_kind(p) in IO.UNCHECKABLE:
            dropped[p["id"]] = "io 对齐判不了调用、只由这类结构命题（细则 11.12）"
        else:
            props[p["id"]] = p
    return props, dropped


def io_align_prompt(state: dict) -> str | None:  # 算法1 第6行（细则 11.12）
    run = Path(state["run"])
    data = S.read_json(run / "propositions.json")
    props, _ = _io_props(run)
    if not props:
        return None
    ex = IO.from_examples(props, S.read_json(run / "sentences.json") or [])
    listing = "\n".join(
        f"{p['id']}  〔{p['class']}〕{'〔来自示例〕' if pid in ex else ''}  {p['sentence']}\n      公式  {p['formula']}"
        for pid, p in props.items()
    )
    S.ensure_private(run / "align")
    S.write_json(run / "w-before.json", S.tree_digests(Path(state["w"])))
    state["digests"] = S.record_digests(run, "align_start", state.get("digests"))
    io = io_settings(state) or {}
    return prompt(
        "io_align",
        DECLS=_decl_text(Decls.from_json(data["decls"])),
        PROPOSITIONS=listing,
        REQUIREMENT=(run / "requirement.txt").read_text(encoding="utf-8"),
        PROTOCOL=io.get("protocol") or "输入文本就是程序的标准输入，输出文本就是程序的标准输出。",
        EXAMPLE_PROPS="、".join(sorted(ex, key=_num_id)) or "（没有）",
    )


def _python_block(answer: str) -> str | None:
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", answer, re.DOTALL)
    return blocks[-1] if blocks else None


def io_align_check(state: dict, answer: str | None, attempt: int, config: dict) -> tuple[dict, str | None]:
    """细则 11.12: trial on the requirement's examples, one correction, then freeze and the mutation test."""
    run = Path(state["run"])
    align = S.ensure_private(run / "align")
    props, dropped = _io_props(run)
    entities = Decls.from_json(S.read_json(run / "propositions.json")["decls"]).of("实体")
    cfg = IO.settings(config, io_settings(state))
    ex_props = IO.from_examples(props, S.read_json(run / "sentences.json") or [])
    code = _python_block(answer) if answer is not None else None
    if answer is not None:
        S.write_text(run / f"io-align-answer-{attempt}.txt", answer)
    report: dict = {"problems": ["对齐 agent 没有回答" if answer is None else "回答中没有 python 代码块"],
                    "contradicted": {}, "unmapped": {}, "examples": [], "example_of": {}, "derived": []}  # fmt: skip
    if code is not None:
        S.write_text(align / IO.MODULE, code)
        module = IO.Module(align / IO.MODULE, cfg)
        try:
            requirement = (run / "requirement.txt").read_text(encoding="utf-8")
            report = IO.trial(module, props, entities, requirement, ex_props, cfg)
        finally:
            module.close()
    S.write_json(run / f"io-trial-{attempt}.json", report)
    trouble = report["problems"] + [
        f"{pid} {why}" for pid, why in {**report["contradicted"], **report["unmapped"]}.items()
    ]
    if trouble and attempt == 1 and code is not None:
        return state, prompt(
            "io_align_fix", ORIGINAL=io_align_prompt(state) or "", PREVIOUS=code, PROBLEMS=_bullets(trouble[:30])
        )
    excluded = dict(dropped)
    excluded.update({pid: f"与示例矛盾：{why}" for pid, why in report["contradicted"].items()})
    excluded.update({pid: f"示例对照缺失：{why}" for pid, why in report["unmapped"].items()})
    failures = {}
    if report["problems"]:
        why = "io 对齐的试跑没有通过：" + "；".join(report["problems"][:3])
        failures = {pid: why for pid in props if pid not in excluded}
    kills: dict[str, int] = {}
    inputs: list[str] = []
    if not failures:
        module = IO.Module(align / IO.MODULE, cfg)
        try:
            inputs = IO.freeze_inputs(module, report["examples"], int(cfg["inputs"]))
            live = {pid: p for pid, p in props.items() if pid not in excluded}
            kills = IO.mutation_kills(module, live, entities, report["examples"], report["example_of"], ex_props, cfg)
        finally:
            module.close()
    S.write_json(align / IO.INPUTS, inputs)
    usable = not failures and any(
        kills.get(pid) and p["class"] != "结构" for pid, p in props.items() if pid not in excluded
    )
    frozen = {
        "kind": "io",
        "bindings": {},
        "checks": [],
        "align_failures": failures,
        "excluded": excluded,
        "io": {
            "settings": io_settings(state),
            "examples": report["examples"],
            "example_of": report["example_of"],
            "example_props": sorted(ex_props),
            "derived": report["derived"],
            "kills": kills,
            "usable": usable,
            "inputs": len(inputs),
        },
    }
    S.write_json(align / "checks.frozen.json", frozen)
    state["digests"] = S.record_digests(run, "checks", state.get("digests"))
    return state, None


# ---------------------------------------------------------------------------------------------------
# 算法1 第7行: the checker
# ---------------------------------------------------------------------------------------------------


def _integrity(state: dict) -> None:
    """细则 5.2, 5.4: compare with the digests carried in the workflow state; anything missing fails."""
    run = Path(state["run"])
    digests = state.get("digests") or {}
    changed, added = S.verify_digests(run, digests, Path(state["workflow_dir"]))
    if changed:
        _invalid(state, f"检查期间被改动或删除：{'、'.join(changed[:10])}（细则 5.2）")
    if added:
        state["align_added"] = added
    if "w-before.json" in digests:
        before = S.read_json(run / "w-before.json")
        if not isinstance(before, dict):
            _invalid(state, "w-before.json 缺失或不可读（细则 5.4）")
            return
        diff = S.compare_trees(before, S.tree_digests(Path(state["w"])))
        if diff["modified"] or diff["deleted"]:
            files = diff["modified"] + diff["deleted"]
            _invalid(state, f"W 中原有的文件被修改或删除：{'、'.join(files[:10])}（细则 5.4）")
        state["w_changes"] = diff


def run_checks(state: dict, config: dict) -> dict:  # 算法1 第7行
    run = Path(state["run"])
    if state.get("stopped") or state.get("invalid"):
        return state
    if not (run / "align" / "checks.frozen.json").exists():
        if not _accepted(run):
            return state
        # 细则 4.8: the alignment stage gave nothing at all; every proposition is an alignment failure.
        state, _ = align_check(state, None, 2, config)
    _integrity(state)
    if state.get("invalid"):
        return state
    data = S.read_json(run / "propositions.json")
    props = {p["id"]: p for p in _accepted(run)}
    entities = Decls.from_json(data["decls"]).of("实体")
    frozen = S.read_json(run / "align" / "checks.frozen.json")
    if frozen.get("kind") == "io":  # 细则 11.12
        live = {pid: p for pid, p in props.items() if pid not in frozen.get("excluded", {})}
        checks = IO.run(run, Path(state["w"]), live, frozen, entities, config)
        results = RUN.summarize(checks, live, frozen)
    else:
        results = RUN.run_checks(run, Path(state["w"]), props, frozen, entities, config)
    S.write_json(run / "results.json", results)
    state["digests"] = S.record_digests(run, "results", state.get("digests"))
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
        try:
            st = path.lstat()
            if path.suffix in (".md", ".txt") or not stat.S_ISREG(st.st_mode) or st.st_size > 1 << 20:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found |= {n for n in names if n in text}
    return sorted(found)


def report(state: dict, config: dict) -> str:  # 算法1 第8行
    run = Path(state["run"])
    try:
        check_violations(state)
        _integrity(state)
        notes = list(state.get("notes", []))
        if state.get("align_added"):
            notes.append(f"检查期间 align/ 中新增的文件：{'、'.join(state['align_added'][:10])}")
        blocked = history_reads(state)
        if blocked:
            notes.append(f"运行期间有 {blocked} 次访问状态目录的工具调用被拦下（细则 5.6）")
        extra = {
            "invalid": state.get("invalid", []),
            "stopped": state.get("stopped"),
            "notes": notes,
            "w_changes": state.get("w_changes", {}),
            "suspect_names": suspect_names(run, Path(state["w"]))
            if state.get("mode") == MODE_ACCUM and (run / "requirement.json").exists()
            else [],
        }
        return RP.write(run, extra, config)
    except Exception as e:  # 细则 4.8: a short report rather than none
        return RP.write_minimal(run, f"报告节点出错：{type(e).__name__}: {e}")


def guarded(name: str, fn, state: dict, *args):
    """细则 4.8: a program node that fails stops the run with the reason instead of crashing the workflow."""
    try:
        return fn(state, *args)
    except Exception as e:  # 细则 4.8: untrusted input must not crash the node
        state["stopped"] = f"{name} 节点出错：{type(e).__name__}: {e}"
        return (state, None) if args else state
