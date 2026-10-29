"""`cv`: install, init, new, check, status, uninstall, and the hooks and launcher (README 第 15、16 节)."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILES = ("cv-subtask", "cv-align")
HOOK_BEGIN = "# >>> cross-validate (cv install) >>>"
HOOK_END = "# <<< cross-validate (cv install) <<<"


def _chrys_home() -> Path:
    return Path(os.environ.get("CV_CHRYS_HOME") or Path.home() / ".chrys")


def _workflow_source() -> Path:
    """The workflow modules: the repository copy next to this file, or the installed one."""
    for candidate in (HERE / "cross-validate", _chrys_home() / "workflows" / "cross-validate"):
        if (candidate / "cv_flow.py").exists():
            return candidate
    raise SystemExit("找不到交叉验证工作流，请先运行 cv install")


sys.path.insert(0, str(_workflow_source()))

import cv_flow as FLOW  # noqa: E402
import cv_ledger as L  # noqa: E402
import cv_report as RP  # noqa: E402
import cv_state as S  # noqa: E402


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _config(project: Path | None = None) -> dict:
    config = FLOW.load_config()
    if project is not None:
        settings = (S.read_json(S.project_dir(project) / "project.json", {}) or {}).get("settings", {})
        for key, value in settings.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key] = {**config[key], **value}
            else:
                config[key] = value
    return config


def enabled(project: str | Path) -> bool:
    return (S.project_dir(project) / "project.json").exists()


# ---------------------------------------------------------------------------------------------------
# install / uninstall (细则 16.7)
# ---------------------------------------------------------------------------------------------------


def _profile_name(path: Path) -> str | None:
    m = re.search(r"^name:\s*['\"]?([^'\"\n]+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else None


def _hooks_block(python: str, hook: str, indent: str) -> str:
    template = (HERE / "hooks" / "hooks.yaml").read_text(encoding="utf-8")
    body = template.split("hooks:\n", 1)[1]
    body = body.replace("{{PYTHON}}", python).replace("{{HOOK}}", hook)
    lines = [indent + line[2:] if line.startswith("  ") else line for line in body.splitlines()]
    return "\n".join([indent + HOOK_BEGIN, *lines, indent + HOOK_END]) + "\n"


def _merge_hooks(python: str, hook: str) -> Path:
    hooks_dir = _chrys_home() / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    existing = next(
        (hooks_dir / n for n in ("hooks.yaml", "hooks.yml", "hooks.json") if (hooks_dir / n).exists()), None
    )
    if existing is None:
        path = hooks_dir / "hooks.yaml"
        path.write_text("version: 1\nhooks:\n" + _hooks_block(python, hook, "  "), encoding="utf-8")
        return path
    shutil.copy2(existing, existing.with_name(existing.name + ".cv-backup"))
    if existing.suffix == ".json":
        data = json.loads(existing.read_text(encoding="utf-8"))
        data["hooks"] = [h for h in data.get("hooks", []) if not str(h.get("id", "")).startswith("cv-")]
        template = (HERE / "hooks" / "hooks.json").read_text(encoding="utf-8")
        ours = json.loads(
            template.replace("{{PYTHON}}", json.dumps(python)[1:-1]).replace("{{HOOK}}", json.dumps(hook)[1:-1])
        )
        data["hooks"].extend(ours["hooks"])
        existing.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return existing
    text = _strip_block(existing.read_text(encoding="utf-8"))
    m = re.search(r"^hooks:[ \t]*(\[\s*\])?[ \t]*$", text, re.MULTILINE)
    if not m:
        raise SystemExit(f"{existing} 的 hooks 不是块状列表，请手动加入 hooks/hooks.yaml 中的条目")
    after = text[m.end() :]
    if re.search(r"^\S", after, re.MULTILINE):
        raise SystemExit(f"{existing} 中 hooks: 之后还有别的顶层键，请手动加入 hooks/hooks.yaml 中的条目")
    items = re.findall(r"^([ \t]*)- ", after, re.MULTILINE)
    indent = items[0] if items else "  "
    text = text[: m.start()] + "hooks:" + after.rstrip("\n") + "\n" + _hooks_block(python, hook, indent)
    existing.write_text(text, encoding="utf-8")
    return existing


def _strip_block(text: str) -> str:
    return re.sub(
        rf"^[ \t]*{re.escape(HOOK_BEGIN)}\n.*?^[ \t]*{re.escape(HOOK_END)}\n?", "", text, flags=re.MULTILINE | re.DOTALL
    )


def _run_icode(args: list[str], cwd: Path | None = None, env: dict | None = None, timeout: float | None = 120):
    icode = os.environ.get("CV_ICODE") or _config()["icode"]
    try:
        return subprocess.run(
            [icode, *args],
            cwd=cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess([icode, *args], 127, "", str(e))


def cmd_install(_args) -> int:
    home = _chrys_home()
    source = HERE / "cross-validate"
    target = home / "workflows" / "cross-validate"
    if target.exists():
        if not (target / "cv_flow.py").exists():
            raise SystemExit(f"{target} 已存在且不是本插件，不覆盖")
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    for name in PROFILES:
        for old in [*agents.glob("*.yaml"), *agents.glob("*.yml")]:
            if _profile_name(old) == name:
                old.unlink()
        shutil.copy2(HERE / "agents" / f"{name}.yaml", agents / f"{name}.yaml")
    bin_dir = S.ensure_private(S.cv_home() / "bin")
    for name in ("cv_cli.py", "cv"):
        shutil.copy2(HERE / name, bin_dir / name)
    shutil.copy2(HERE / "hooks" / "cv_hook.py", bin_dir / "cv_hook.py")
    for name in ("hooks.yaml", "hooks.json"):
        (bin_dir / "hooks").mkdir(exist_ok=True)
        shutil.copy2(HERE / "hooks" / name, bin_dir / "hooks" / name)
    (bin_dir / "agents").mkdir(exist_ok=True)
    for name in PROFILES:
        shutil.copy2(HERE / "agents" / f"{name}.yaml", bin_dir / "agents" / f"{name}.yaml")
    os.chmod(bin_dir / "cv", 0o700)
    hooks_file = _merge_hooks(sys.executable, str(bin_dir / "cv_hook.py"))
    S.write_json(
        S.cv_home() / "install.json",
        {"workflow_digest": S.folder_digest(target), "installed": _now(), "workflow": str(target)},
    )
    print(f"已安装：工作流 {target}，agent 配置 {agents}，hooks {hooks_file}，命令 {bin_dir / 'cv'}")
    for args in (["hook", "validate"], ["workflow", "validate", str(target)]):
        out = _run_icode(args)
        status = "通过" if out.returncode == 0 else f"未通过（{out.returncode}）"
        print(f"icode {' '.join(args)}：{status}")
        if out.returncode != 0:
            print((out.stdout + out.stderr).strip()[-2000:])
    return 0


def cmd_uninstall(_args) -> int:
    home = _chrys_home()
    target = home / "workflows" / "cross-validate"
    if (target / "cv_flow.py").exists():
        shutil.rmtree(target)
    for path in [*(home / "agents").glob("*.yaml"), *(home / "agents").glob("*.yml")]:
        if _profile_name(path) in PROFILES:
            path.unlink()
    for name in ("hooks.yaml", "hooks.yml"):
        path = home / "hooks" / name
        if path.exists():
            path.write_text(_strip_block(path.read_text(encoding="utf-8")), encoding="utf-8")
    path = home / "hooks" / "hooks.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["hooks"] = [h for h in data.get("hooks", []) if not str(h.get("id", "")).startswith("cv-")]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(S.cv_home() / "bin", ignore_errors=True)
    print("已卸载。各项目的需求记录和报告仍在 " + str(S.cv_home() / "projects"))
    return 0


# ---------------------------------------------------------------------------------------------------
# init / new / status (细则 15.1, 15.6, 16.6)
# ---------------------------------------------------------------------------------------------------


def _is_git(path: Path) -> bool:
    out = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=path, capture_output=True, text=True, check=False
    )
    return out.returncode == 0 and out.stdout.strip() == "true"


def cmd_init(args) -> int:
    project = Path(args.path).resolve()
    if project in (Path.home().resolve(), Path("/")):
        raise SystemExit("不能在家目录或根目录启用交叉验证（细则 15.1）")
    if not _is_git(project) and not args.allow_non_git:
        raise SystemExit("这不是 git 仓库；确实要启用时加 --allow-non-git（细则 15.1）")
    pdir = S.ensure_private(S.project_dir(project))
    info = S.read_json(pdir / "project.json", {}) or {}
    info.update(project=str(project), enabled=info.get("enabled", _now()), settings=info.get("settings", {}))
    S.write_json(pdir / "project.json", info)
    L.current_id(project)
    L.ledger_file(project).touch()
    print(f"已在 {project} 启用交叉验证；状态目录 {pdir}")
    return 0


def cmd_new(args) -> int:
    project = Path(args.path).resolve()
    if not enabled(project):
        raise SystemExit("这个项目没有启用交叉验证，先运行 cv init")
    print(f"新的需求记录：{L.new_ledger(project)}")
    return 0


def last_run(project: Path) -> dict | None:
    runs = S.project_dir(project) / "runs"
    for run in sorted(runs.iterdir(), reverse=True) if runs.exists() else []:
        info = S.read_json(run / "run.json")
        if info and info.get("mode") == FLOW.MODE_ACCUM:
            return {**info, "run": run.name}
    return None


def _msg_num(mid: str | None) -> int:
    return int(mid[1:]) if mid and mid[1:].isdigit() else 0


def new_messages(project: Path) -> list[dict]:
    """细则 16.3: messages of the current ledger after the last message the latest run saw."""
    qid = L.current_id(project)
    messages = L.read_messages(L.ledger_file(project, qid))
    last = last_run(project)
    seen = _msg_num(last.get("last_message")) if last and last.get("ledger") == qid else 0
    return [m for m in messages if _msg_num(m["id"]) > seen]


def cmd_status(args) -> int:
    project = Path(args.path).resolve()
    if not enabled(project):
        print("这个项目没有启用交叉验证")
        return 1
    pdir = S.project_dir(project)
    qid = L.current_id(project)
    messages = L.read_messages(L.ledger_file(project, qid))
    print(
        f"项目：{project}\n状态目录：{pdir}\n当前需求记录：{qid}，{len(messages)} 条消息，其中 {len(new_messages(project))} 条尚未检查"
    )
    latest = pdir / "reports" / "latest.md"
    if latest.exists():
        text = latest.read_text(encoding="utf-8")
        m = re.search(r"## 比对结论\n\*\*(.+?)\*\*", text)
        print(f"最新结论：{m.group(1) if m else '未知'}\n最新报告：{latest}")
    if (pdir / "lock").exists() and _locked(pdir / "lock"):
        print("正在运行一次交叉验证")
    return 0


def _locked(path: Path) -> bool:
    with open(path, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False


# ---------------------------------------------------------------------------------------------------
# Snapshot (细则 16.4)
# ---------------------------------------------------------------------------------------------------


class SnapshotError(Exception):
    pass


def _git_files(project: Path) -> list[str]:
    def ls(*args: str) -> list[str]:
        out = subprocess.run(["git", "ls-files", "-z", *args], cwd=project, capture_output=True, check=False)
        return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]

    return sorted(set(ls("--cached", "--others", "--exclude-standard")) | set(ls("--recurse-submodules", "--cached")))


def _all_files(project: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".chrys")]
        out.extend((Path(dirpath) / name).relative_to(project).as_posix() for name in filenames)
    return sorted(out)


def _excluded(rel: str, exclude: list[str]) -> bool:
    parts = rel.split("/")
    return (
        parts[0] in (".git", ".chrys")
        or ".git" in parts
        or any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in exclude)
    )


def snapshot(project: Path, target: Path, config: dict) -> list[str]:
    """Copy the project to target and return notes for the report."""
    cfg = config["snapshot"]
    files = _git_files(project) if _is_git(project) else _all_files(project)
    files = [f for f in files if not _excluded(f, cfg["exclude"] + cfg["link"])]
    files += [c for c in cfg["copy"] if (project / c).is_file() and c not in files]
    notes: list[str] = []
    skipped_links: list[str] = []
    for attempt in range(3):
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        count, size = 0, 0
        before: dict[str, str] = {}
        for rel in files:
            src = project / rel
            if src.is_symlink():
                link = os.readlink(src)
                inside = not os.path.isabs(link) and os.path.realpath(src).startswith(str(project) + os.sep)
                if inside:
                    (target / rel).parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(link, target / rel)
                elif rel not in skipped_links:
                    skipped_links.append(rel)
                continue
            if not src.is_file():
                continue
            count += 1
            size += src.stat().st_size
            if count > cfg["max_files"] or size > cfg["max_bytes"]:
                raise SnapshotError(f"项目超过快照上限（{cfg['max_files']} 个文件或 {cfg['max_bytes']} 字节）")
            before[rel] = S.sha256_file(src)
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target / rel)
        after = {rel: S.sha256_file(project / rel) for rel in before if (project / rel).is_file()}
        if after == before:
            break
        notes.append(f"快照期间项目文件发生变化，重新复制（第 {attempt + 1} 次）")
    else:
        raise SnapshotError("项目文件在快照期间持续变化，无法得到一致的快照")
    for name in cfg["link"]:
        if (project / name).is_dir():
            os.symlink(project / name, target / name)
            notes.append(f"依赖目录 {name} 以符号链接接入快照（细则 16.4）")
    notes.extend(f"按设置复制了被忽略的文件 {name}" for name in cfg["copy"] if (project / name).is_file())
    if skipped_links:
        notes.append(f"没有复制指向项目之外的符号链接：{'、'.join(skipped_links[:10])}")
    return notes


# ---------------------------------------------------------------------------------------------------
# The launcher (细则 16.2, 16.3)
# ---------------------------------------------------------------------------------------------------


def verify_workflow(cwd: Path) -> str | None:
    """细则 5.1: the workflow iCode would run here is the installed global one, unchanged."""
    out = _run_icode(["workflow", "list", "--json"], cwd=cwd)
    if out.returncode != 0:
        return f"无法列出 iCode 工作流：{(out.stderr or out.stdout).strip()[-300:]}"
    try:
        items = json.loads(out.stdout)["workflows"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return "icode workflow list --json 的输出无法解析"
    entry = next((w for w in items if w.get("id") == S.WORKFLOW_ID), None)
    installed = S.global_workflow_dir()
    if entry is None or entry.get("source") != "global":
        return "将要运行的 cross-validate 工作流不是全局安装的那一份"
    if Path(entry["path"]).resolve().parent != installed.resolve():
        return "将要运行的 cross-validate 工作流路径与安装位置不符"
    record = S.read_json(S.cv_home() / "install.json", {}) or {}
    if record.get("workflow_digest") != S.folder_digest(installed):
        return "安装后的工作流文件夹被改动过"
    return None


def _stop_report(run: Path, reason: str, invalid: bool) -> None:
    extra = {"invalid": [reason]} if invalid else {"stopped": reason}
    RP.write(run, extra, _config())


def _publish(project: Path, run: Path) -> None:
    reports = S.ensure_private(S.project_dir(project) / "reports")
    if (run / "report.md").exists():
        shutil.copy2(run / "report.md", reports / f"{run.name}.md")
        shutil.copy2(run / "report.md", reports / "latest.md")


def _prune(project: Path, keep: int) -> None:
    runs = sorted((S.project_dir(project) / "runs").iterdir())
    for old in runs[: max(0, len(runs) - keep)]:
        shutil.rmtree(old, ignore_errors=True)


def run_once(project: Path, trigger: str, config: dict) -> Path | None:
    qid = L.current_id(project)
    messages = L.read_messages(L.ledger_file(project, qid))
    if not messages:
        return None
    run = S.create_run(project)
    processed = L.preprocess(messages)
    with open(run / "ledger.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(m, ensure_ascii=False) + "\n" for m in processed)
    snap = S.ensure_private(S.cv_home() / "snapshots") / run.name
    info = {
        "mode": FLOW.MODE_ACCUM,
        "trigger": trigger,
        "project": str(project),
        "snapshot": str(snap),
        "ledger": qid,
        "first_message": messages[0]["id"],
        "last_message": messages[-1]["id"],
        "started": _now(),
        "code": {**FLOW.code_version(project), "snapshot_time": _now()},
        "notes": [],
    }
    S.write_json(run / "run.json", info)
    try:
        info["notes"] = snapshot(project, snap, config)
        S.write_json(run / "run.json", info)
        problem = verify_workflow(snap)
        if problem:
            _stop_report(run, problem + "（细则 5.1）", invalid=True)
        else:
            env = {"CV_INTERNAL": "1", "CV_RUN_DIR": str(run)}
            out = _run_icode(
                [
                    "workflow",
                    "run",
                    S.WORKFLOW_ID,
                    "--trust",
                    "--json",
                    "--quiet",
                    "--input",
                    FLOW.RUN_PREFIX + str(run),
                ],
                cwd=snap,
                env=env,
                timeout=None,
            )
            S.write_text(run / "icode-output.json", out.stdout or "")
            if not (run / "report.md").exists():
                _stop_report(
                    run,
                    f"工作流没有完成（退出码 {out.returncode}）：{(out.stderr or out.stdout).strip()[-500:]}",
                    invalid=False,
                )
    except SnapshotError as e:
        _stop_report(run, str(e), invalid=False)
    finally:
        if config["keep_snapshots"] <= 0:
            shutil.rmtree(snap, ignore_errors=True)
    _publish(project, run)
    _prune(project, config["keep_runs"])
    return run


def launch(project: Path, trigger: str) -> Path | None:
    """细则 16.2: one run at a time per project; a trigger that finds the lock leaves a pending mark."""
    pdir = S.ensure_private(S.project_dir(project))
    config = _config(project)
    with open(pdir / "lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            (pdir / "pending").touch()
            return None
        last = None
        while True:
            last = run_once(project, trigger, config) or last
            pending = pdir / "pending"
            if not pending.exists():
                break
            pending.unlink()
            if not new_messages(project):
                break
            trigger = "待办（运行期间又有触发）"
        return last


def cmd_check(args) -> int:
    project = Path(args.path).resolve()
    if not enabled(project):
        raise SystemExit("这个项目没有启用交叉验证，先运行 cv init")
    run = launch(project, "手动")
    if run is None:
        print("没有可检查的需求，或者已有一次运行正在进行（已记下待办）")
        return 1
    print((run / "report.md").read_text(encoding="utf-8") if (run / "report.md").exists() else f"运行目录：{run}")
    return 0


# ---------------------------------------------------------------------------------------------------
# Hooks (细则 15.2, 16.1, 16.5, 5.3)
# ---------------------------------------------------------------------------------------------------


def hook(event: str) -> int:
    payload = json.loads(Path(os.environ["CHRYS_HOOK_PAYLOAD_FILE"]).read_text(encoding="utf-8"))
    if event == "guard":
        return _guard(payload)
    cwd = payload.get("cwd") or os.getcwd()
    if os.environ.get("CV_INTERNAL") == "1" or payload.get("session_kind") == "workflow" or not enabled(cwd):
        return 0
    if payload.get("profile") in PROFILES:
        return 0
    project = Path(os.path.realpath(cwd))
    config = _config(project)
    if event == "record":
        text = payload.get("text") or ""
        if text.strip() and not text.lstrip().startswith("/"):
            L.append_message(project, payload.get("session_id", ""), text, bool(payload.get("injected")))
        return 0
    if event == "turn":
        if (
            config["auto"]["enabled"]
            and payload.get("status") == "ok"
            and len(new_messages(project)) >= config["auto"]["min_new_messages"]
        ):
            launch(project, "一轮结束")
        return 0
    if event == "session_end":
        if config["auto"]["on_session_end"] and new_messages(project):
            launch(project, "会话结束")
        return 0
    return 0


def _guard(payload: dict) -> int:
    """细则 5.3: the subtask agent must not call tools; record and refuse every call."""
    record = {
        "time": _now(),
        "cwd": payload.get("cwd"),
        "session": payload.get("session_id"),
        "tool": (payload.get("tool") or {}).get("name"),
    }
    run_dir = os.environ.get("CV_RUN_DIR")
    path = (
        Path(run_dir) / "tool-violations.jsonl" if run_dir else S.ensure_private(S.cv_home()) / "tool-violations.jsonl"
    )
    with contextlib.suppress(OSError), open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    result = os.environ.get("CHRYS_HOOK_RESULT")
    if result:
        Path(result).write_text(
            json.dumps(
                {"action": "block", "reason": "交叉验证的子任务 agent 不得调用工具，只能依据提示中的需求作答"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cv", description="iCode 交叉验证插件")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="安装工作流、agent 配置和 hooks")
    sub.add_parser("uninstall", help="卸载本插件的文件和 hooks 条目")
    p = sub.add_parser("init", help="在项目中启用交叉验证")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--allow-non-git", action="store_true")
    for name, text in (
        ("new", "开始一份新的需求记录"),
        ("check", "立即运行一次交叉验证"),
        ("status", "显示最新结论和报告路径"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("path", nargs="?", default=".")
    p = sub.add_parser("hook", help="供 hooks 调用")
    p.add_argument("event", choices=["record", "turn", "session_end", "guard"])
    args = parser.parse_args(argv)
    if args.command == "hook":
        return hook(args.event)
    return {
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "init": cmd_init,
        "new": cmd_new,
        "check": cmd_check,
        "status": cmd_status,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
