"""What each workflow node does (README 第 4 节). `cross-validate.py` only wires these into a graph.

Node state is a small dict {run, mode, w, ...}; everything else is on disk in the run directory,
outside the project (细则 3.1).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
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
    S.write_json(
        run / "run.json",
        {"mode": MODE_GOAL, "trigger": "目标模式", "project": cwd, "snapshot": None, "started": _now()},
    )
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


def _verify_note(run: Path, props: list[dict], entities: set[str], config: dict) -> str:
    """细则 11.11: which propositions a verifier can decide, and the headers to write against (stubs)."""
    engine = config.get("verify_engine", "cbmc")
    if shutil.which(engine) is None:
        return f"环境中没有验证器 {engine}，不能使用 verify 检查。"
    ids = V.write_headers(
        run / "verify-headers", {p["id"]: p for p in props}, entities, config.get("verify_max_steps", 50), stubs=True
    )
    if not ids:
        return f"环境中有验证器 {engine}，但没有命题适合 verify 检查。"
    return (
        f"环境中有验证器 {engine}。下列命题可以用 verify 检查：{'、'.join(ids)}。"
        f"试验用的头文件在 `{run / 'verify-headers'}`（编译时加 `-I` 这个目录）；它们只有名字，不判定命题，"
        "检查时程序换成真正的头文件。"
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
