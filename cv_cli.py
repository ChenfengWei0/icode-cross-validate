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


def enabled_root(cwd: str | Path) -> Path | None:
    """细则 15.1: the nearest enabled directory at or above the session's working directory."""
    here = Path(os.path.realpath(cwd))
    for candidate in (here, *here.parents):
        if enabled(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------------------------------
# install / uninstall (细则 16.7)
# ---------------------------------------------------------------------------------------------------


def _profile_name(path: Path) -> str | None:
    m = re.search(r"^name:\s*['\"]?([^'\"\n]+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else None


HOOK_IDS = (
    "cv-record",
    "cv-turn",
    "cv-session-end",
    "cv-subtask-guard",
    "cv-history-guard-path",
    "cv-history-guard-command",
    "cv-history-guard-workdir",
)
HOOK_FILES = ("hooks.yaml", "hooks.yml", "hooks.json")


class MergeError(Exception):
    pass


def _hooks_block(python: str, hook: str, indent: str) -> str:
    """Our entries between marker lines; paths are written as JSON strings, valid in YAML (细则 16.7)."""
    template = (HERE / "hooks" / "hooks.yaml").read_text(encoding="utf-8")
    body = template.split("hooks:\n", 1)[1]
    body = body.replace('"{{PYTHON}}"', json.dumps(python, ensure_ascii=False))
    body = body.replace('"{{HOOK}}"', json.dumps(hook, ensure_ascii=False))
    lines = [indent + line[2:] if line.startswith("  ") else line for line in body.splitlines()]
    return "\n".join([indent + HOOK_BEGIN, *lines, indent + HOOK_END]) + "\n"


def _hooks_region(lines: list[str], at: int) -> int:
    """Index after the last line of the block list that starts below `hooks:` at line `at`."""
    end = at + 1
    for k in range(at + 1, len(lines)):
        line = lines[k]
        if line.strip() == "" or line.startswith((" ", "\t", "#", "-")):
            if line.strip() and not line.startswith("#"):
                end = k + 1
            continue
        break
    return end


HOOKS_KEY = re.compile(r"^\ufeff?(?:hooks|'hooks'|\"hooks\")[ \t]*:[ \t]*(?P<rest>[^#\n]*?)[ \t]*(?:#.*)?$")


def _check_markers(text: str) -> None:
    begins, ends = text.count(HOOK_BEGIN), text.count(HOOK_END)
    if begins != ends or begins > 1:
        raise MergeError("插件的标记行不成对（文件可能被手动改过），请手动删去 cv- 条目和标记行后再试")


def merged_yaml(text: str, python: str, hook: str) -> str:
    """细则 16.7: the hooks file with our block added, computed in memory; MergeError when unsafe."""
    _check_markers(text)
    crlf = "\r\n" in text
    text = _strip_block(text.replace("\r\n", "\n"))
    if not text.strip():
        return "version: 1\nhooks:\n" + _hooks_block(python, hook, "  ")
    lines = text.splitlines()
    keys = [k for k, line in enumerate(lines) if HOOKS_KEY.match(line)]
    if len(keys) > 1:
        raise MergeError("文件中有不止一个 hooks 键")
    if not keys and re.search(r"^\S*hooks\S*\s*:", text, re.MULTILINE):
        raise MergeError("hooks 键的写法无法识别")
    at = keys[0] if keys else None
    if at is None:
        return text.rstrip("\n") + "\nhooks:\n" + _hooks_block(python, hook, "  ")
    rest = HOOKS_KEY.match(lines[at]).group("rest").replace(" ", "")
    if rest == "[]":
        lines[at] = "hooks:"
    elif rest:
        raise MergeError(f"hooks 的值 {rest} 无法安全合并，请改成每行一项的列表")
    end = _hooks_region(lines, at)
    items = [re.match(r"^([ \t]*)- ", line) for line in lines[at + 1 : end]]
    indent = next((m.group(1) for m in items if m), "  ")
    block = _hooks_block(python, hook, indent).rstrip("\n").split("\n")
    out = "\n".join(lines[:end] + block + lines[end:]) + "\n"
    return out.replace("\n", "\r\n") if crlf else out


def unmerged_yaml(text: str) -> str:
    """The hooks file without our block; an emptied list becomes `hooks: []` so the file stays valid."""
    _check_markers(text)
    crlf = "\r\n" in text
    text = _strip_block(text.replace("\r\n", "\n"))
    lines = text.splitlines()
    at = next((k for k, line in enumerate(lines) if HOOKS_KEY.match(line)), None)
    if at is not None and not HOOKS_KEY.match(lines[at]).group("rest"):
        end = _hooks_region(lines, at)
        if not any(re.match(r"^[ \t]*- ", line) for line in lines[at + 1 : end]):
            lines[at] = "hooks: []"
    out = "\n".join(lines) + ("\n" if lines else "")
    return out.replace("\n", "\r\n") if crlf else out


def merged_json(text: str, python: str, hook: str) -> str:
    try:
        data = json.loads(text) if text.strip() else {"version": 1, "hooks": []}
    except json.JSONDecodeError as e:
        raise MergeError(f"hooks.json 不能解析：{e}") from e
    if not isinstance(data, dict) or not isinstance(data.setdefault("hooks", []), list):
        raise MergeError("hooks.json 的 hooks 应是列表")
    data["hooks"] = [h for h in data["hooks"] if not (isinstance(h, dict) and h.get("id") in HOOK_IDS)]
    template = (HERE / "hooks" / "hooks.json").read_text(encoding="utf-8")
    ours = json.loads(
        template.replace("{{PYTHON}}", json.dumps(python, ensure_ascii=False)[1:-1]).replace(
            "{{HOOK}}", json.dumps(hook, ensure_ascii=False)[1:-1]
        )
    )
    data["hooks"].extend(ours["hooks"])
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def unmerged_json(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MergeError(f"hooks.json 不能解析：{e}") from e
    data["hooks"] = [h for h in data.get("hooks", []) if not (isinstance(h, dict) and h.get("id") in HOOK_IDS)]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _hooks_file() -> Path:
    hooks_dir = _chrys_home() / "hooks"
    return next((hooks_dir / n for n in HOOK_FILES if (hooks_dir / n).exists()), hooks_dir / "hooks.yaml")


def _ours_in(text: str, path: Path) -> bool:
    if path.suffix == ".json":
        return any(f'"{i}"' in text for i in HOOK_IDS)
    return HOOK_BEGIN in text or HOOK_END in text


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


def _hook_validate() -> bool | None:
    out = _run_icode(["hook", "validate"])
    return None if out.returncode == 127 else out.returncode == 0


def cmd_install(_args) -> int:
    home = _chrys_home()
    source = HERE / "cross-validate"
    target = home / "workflows" / "cross-validate"
    if not (source / "cv_flow.py").exists():
        raise SystemExit("cv install 要在插件仓库中运行（./cv install），安装后的 cv 不能重新安装自己")
    if target.exists() and not (target / "cv_flow.py").exists():
        raise SystemExit(f"{target} 已存在且不是本插件，不覆盖")
    bin_dir = S.cv_home() / "bin"
    hook_path = bin_dir / "cv_hook.py"
    # 细则 16.7: merge the hooks in memory first, so an unsafe layout stops before anything changes.
    hooks_file = _hooks_file()
    original = hooks_file.read_text(encoding="utf-8") if hooks_file.exists() else ""
    try:
        merge = merged_json if hooks_file.suffix == ".json" else merged_yaml
        merged = merge(original, sys.executable, str(hook_path))
    except MergeError as e:
        raise SystemExit(f"{hooks_file}：{e}。没有改动任何文件。") from e
    valid_before = _hook_validate() if hooks_file.exists() else True
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    agents = home / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    for name in PROFILES:
        for old in [*agents.glob("*.yaml"), *agents.glob("*.yml")]:
            if _profile_name(old) == name:
                old.unlink()
        shutil.copy2(HERE / "agents" / f"{name}.yaml", agents / f"{name}.yaml")
    S.ensure_private(bin_dir)
    for name in ("cv_cli.py", "cv"):
        shutil.copy2(HERE / name, bin_dir / name)
    shutil.copy2(HERE / "hooks" / "cv_hook.py", hook_path)
    (bin_dir / "hooks").mkdir(exist_ok=True)
    for name in ("hooks.yaml", "hooks.json"):
        shutil.copy2(HERE / "hooks" / name, bin_dir / "hooks" / name)
    (bin_dir / "agents").mkdir(exist_ok=True)
    for name in PROFILES:
        shutil.copy2(HERE / "agents" / f"{name}.yaml", bin_dir / "agents" / f"{name}.yaml")
    os.chmod(bin_dir / "cv", 0o700)
    hooks_file.parent.mkdir(parents=True, exist_ok=True)
    backup = hooks_file.with_name(hooks_file.name + ".cv-backup")
    if original and not _ours_in(original, hooks_file):
        backup.write_text(original, encoding="utf-8")
    hooks_file.write_text(merged, encoding="utf-8")
    S.write_json(
        S.ensure_private(S.cv_home()) / "install.json",
        {"workflow_digest": S.folder_digest(target), "installed": _now(), "workflow": str(target)},
    )
    print(f"已安装：工作流 {target}，agent 配置 {agents}，hooks {hooks_file}，命令 {bin_dir / 'cv'}")
    if valid_before and _hook_validate() is False:
        if original:
            hooks_file.write_text(original, encoding="utf-8")
        else:
            hooks_file.unlink()
        print(f"icode hook validate 在合并后不通过，已恢复 {hooks_file} 的原样；请手动加入 hooks/hooks.yaml 中的条目")
        return 1
    for args in (["hook", "validate"], ["workflow", "validate", str(target)]):
        out = _run_icode(args)
        status = "通过" if out.returncode == 0 else f"未通过（{out.returncode}）"
        print(f"icode {' '.join(args)}：{status}")
        if out.returncode != 0:
            print((out.stdout + out.stderr).strip()[-2000:])
    return 0


def cmd_uninstall(_args) -> int:
    home = _chrys_home()
    # 细则 16.7: work out the hooks files first; if one cannot be cleaned, change nothing at all.
    edits: list[tuple[Path, str]] = []
    for name in HOOK_FILES:
        path = home / "hooks" / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if text and _ours_in(text, path):
            try:
                edits.append((path, (unmerged_json if name.endswith(".json") else unmerged_yaml)(text)))
            except MergeError as e:
                print(f"{path}：{e}。没有卸载任何东西；请先手动删去其中的 cv- 条目，再运行 cv uninstall。")
                return 1
    for path, text in edits:
        path.write_text(text, encoding="utf-8")
    for name in HOOK_FILES:
        (home / "hooks" / (name + ".cv-backup")).unlink(missing_ok=True)
    target = home / "workflows" / "cross-validate"
    if (target / "cv_flow.py").exists():
        shutil.rmtree(target)
    for path in [*(home / "agents").glob("*.yaml"), *(home / "agents").glob("*.yml")]:
        if _profile_name(path) in PROFILES:
            path.unlink()
    shutil.rmtree(S.cv_home() / "bin", ignore_errors=True)
    (S.cv_home() / "install.json").unlink(missing_ok=True)
    print("已卸载。各项目的需求记录和报告仍在 " + str(S.cv_home() / "projects"))
    return 0


# ---------------------------------------------------------------------------------------------------
# init / new / status (细则 15.1, 15.6, 16.6)
# ---------------------------------------------------------------------------------------------------


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )


def _is_git(path: Path) -> bool:
    """A git work tree whose root directory is not itself ignored by an enclosing repository (细则 15.1)."""
    out = _git(path, "rev-parse", "--is-inside-work-tree")
    if out.returncode != 0 or out.stdout.strip() != "true":
        return False
    return _git(path, "check-ignore", "-q", ".").returncode != 0


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
        print(f"最新结论：{_conclusion(latest)}\n最新报告：{latest}")
    if is_running(pdir):  # 细则 16.3: never probe the lock, a trigger could lose its run
        print("正在运行一次交叉验证")
    return 0


# ---------------------------------------------------------------------------------------------------
# Snapshot (细则 16.4)
# ---------------------------------------------------------------------------------------------------


class SnapshotError(Exception):
    pass


def _git_files(project: Path) -> list[str]:
    def ls(*args: str) -> list[str]:
        out = subprocess.run(
            ["git", "--no-optional-locks", "ls-files", "-z", *args],
            cwd=project,
            capture_output=True,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
        return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]

    return sorted(set(ls("--cached", "--others", "--exclude-standard")) | set(ls("--recurse-submodules", "--cached")))


def _all_files(project: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".chrys")]
        # Links to directories are listed too, so the snapshot copies or reports them (细则 16.4).
        links = [d for d in dirnames if (Path(dirpath) / d).is_symlink()]
        out.extend((Path(dirpath) / name).relative_to(project).as_posix() for name in [*filenames, *links])
    return sorted(out)


def _excluded(rel: str, exclude: list[str]) -> bool:
    parts = rel.split("/")
    return (
        parts[0] in (".git", ".chrys")
        or ".git" in parts
        or any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in exclude)
    )


def _hardlink_tree(src: Path, dst: Path) -> None:
    """细则 16.4: rebuild the directory tree, with each file a hard link of the project's file."""

    def link(s: str, d: str) -> None:
        os.link(s, d, follow_symlinks=False)

    shutil.copytree(src, dst, symlinks=True, copy_function=link)


def snapshot(project: Path, target: Path, config: dict) -> list[str]:
    """Copy the project to target and return notes for the report."""
    try:
        return _snapshot(project, target, config)
    except OSError as e:  # 细则 16.2: e.g. a file the user cannot read
        raise SnapshotError(f"无法做快照：{e}") from e


def _snapshot(project: Path, target: Path, config: dict) -> list[str]:
    cfg = config["snapshot"]
    files = _git_files(project) if _is_git(project) else _all_files(project)
    files = [f for f in files if not _excluded(f, cfg["exclude"] + cfg["link"])]
    files += [c for c in cfg["copy"] if (project / c).is_file() and c not in files]
    notes: list[str] = []
    skipped_links: list[str] = []
    missing: list[str] = []
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
                if not src.exists():
                    missing.append(rel)
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
        if (project / name).is_symlink():
            notes.append(f"依赖目录 {name} 本身是符号链接，没有接入快照（细则 16.4）")
        elif (project / name).is_dir():
            try:
                _hardlink_tree(project / name, target / name)
                notes.append(f"依赖目录 {name} 以硬链接副本接入快照（细则 16.4）")
            except OSError:
                shutil.rmtree(target / name, ignore_errors=True)
                os.symlink(project / name, target / name)
                notes.append(f"依赖目录 {name} 无法建立硬链接副本，以符号链接接入快照，检查可能写到项目中（细则 16.4）")
    notes.extend(f"按设置复制了被忽略的文件 {name}" for name in cfg["copy"] if (project / name).is_file())
    if skipped_links:
        notes.append(f"没有复制指向项目之外的符号链接：{'、'.join(skipped_links[:10])}")
    if missing:
        notes.append(f"有 {len(set(missing))} 个列出的文件没有找到（可能是文件名无法解码），没有复制")
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
    conclusion = (S.read_json(run / "report.json", {}) or {}).get("conclusion") or _md_conclusion(run / "report.md")
    S.write_json(reports / "latest.json", {"run": run.name, "conclusion": conclusion})
    S.write_json(reports / f"{run.name}.json", {"run": run.name, "conclusion": conclusion})


def _prune(project: Path, keep: int) -> None:
    runs = sorted((S.project_dir(project) / "runs").iterdir())
    for old in runs[: max(0, len(runs) - keep)]:
        shutil.rmtree(old, ignore_errors=True)


def _prune_snapshots(project: Path, keep: int, current: str | None = None) -> None:
    """细则 16.4: keep the newest `keep` snapshots; also clears those left by interrupted runs."""
    snaps = S.cv_home() / "snapshots"
    if not snaps.exists():
        return
    busy = set()  # snapshots of runs in progress in other projects
    for pdir in (S.cv_home() / "projects").iterdir() if (S.cv_home() / "projects").exists() else []:
        if is_running(pdir) and pdir != S.project_dir(project):
            busy |= {r.name for r in (pdir / "runs").iterdir()} if (pdir / "runs").exists() else set()
    others = sorted(d for d in snaps.iterdir() if d.name != current and d.name not in busy)
    keep = max(0, keep - (1 if current else 0))  # the current snapshot counts towards keep
    for old in others[: max(0, len(others) - keep)]:
        shutil.rmtree(old, ignore_errors=True)


def run_once(project: Path, trigger: str, config: dict) -> Path | None:
    qid = L.current_id(project)
    messages = L.read_messages(L.ledger_file(project, qid))
    if not messages:
        return None
    run = S.create_run(project)
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
        "code": {"snapshot_time": _now()},
        "notes": [],
    }
    # 细则 16.2: run.json first, so a run that stops still counts the messages it saw.
    S.write_json(run / "run.json", info)
    try:
        processed = L.preprocess(messages)
        with open(run / "ledger.jsonl", "w", encoding="utf-8") as f:
            f.writelines(json.dumps(m, ensure_ascii=False) + "\n" for m in processed)
        info["code"] = {**FLOW.code_version(project), "snapshot_time": _now()}
        S.write_json(run / "run.json", info)
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
    except Exception as e:  # 细则 16.2: the run stops with the reason, never silently
        _stop_report(run, f"启动器出错：{type(e).__name__}: {e}", invalid=False)
    finally:
        if config["keep_snapshots"] <= 0:
            shutil.rmtree(snap, ignore_errors=True)
        _prune_snapshots(project, max(0, config["keep_snapshots"]), None if config["keep_snapshots"] <= 0 else run.name)
    _publish(project, run)
    _prune(project, config["keep_runs"])
    return run


def _try_lock(lock) -> bool:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def launch(project: Path, trigger: str) -> Path | None:
    """细则 16.2, 16.3: one run at a time per project; every trigger marks `pending` before trying the lock."""
    pdir = S.ensure_private(S.project_dir(project))
    config = _config(project)
    pending, running = pdir / "pending", pdir / "running"
    pending.touch()
    last = None
    while True:
        with open(pdir / "lock", "a") as lock:
            if not _try_lock(lock):
                return last  # the holder will see `pending` after it releases the lock
            running.write_text(str(os.getpid()), encoding="utf-8")
            try:
                while pending.exists():
                    pending.unlink()
                    if last is not None and not new_messages(project):
                        continue
                    last = run_once(project, trigger, config) or last
                    trigger = "待办（运行期间又有触发）"
            finally:
                running.unlink(missing_ok=True)
                fcntl.flock(lock, fcntl.LOCK_UN)
        if not pending.exists():
            return last


def is_running(pdir: Path) -> bool:
    try:
        pid = int((pdir / "running").read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def cmd_check(args) -> int:
    project = Path(args.path).resolve()
    if not enabled(project):
        raise SystemExit("这个项目没有启用交叉验证，先运行 cv init")
    run = launch(project, "手动")
    if run is None:
        print("没有可检查的需求，或者已有一次运行正在进行（已记下待办）")
        return 1
    # 细则 16.6: the conclusion and the path only, never the report itself.
    report = S.project_dir(project) / "reports" / f"{run.name}.md"
    print(f"比对结论：{_conclusion(report)}\n报告：{report}")
    return 0


def _conclusion(report: Path) -> str:
    """The conclusion as the program recorded it, not as text in the report (which quotes program output)."""
    return (S.read_json(report.with_suffix(".json"), {}) or {}).get("conclusion") or "未知"


def _md_conclusion(report: Path) -> str | None:
    """Only for reports the workflow did not write itself (a stopped launcher): the last conclusion heading."""
    text = report.read_text(encoding="utf-8") if report.exists() else ""
    found = re.findall(r"^## 比对结论\n\*\*(.+?)\*\*", text, re.MULTILINE)
    return found[-1] if found else None


# ---------------------------------------------------------------------------------------------------
# Hooks (细则 15.2, 16.1, 16.5, 5.3)
# ---------------------------------------------------------------------------------------------------


def hook(event: str) -> int:
    payload = json.loads(Path(os.environ["CHRYS_HOOK_PAYLOAD_FILE"]).read_text(encoding="utf-8"))
    if event == "guard":
        return _guard(payload)
    if event == "history":
        return _history_guard(payload)
    if os.environ.get("CV_INTERNAL") == "1" or payload.get("session_kind") == "workflow":
        return 0
    if payload.get("profile") in PROFILES:
        return 0
    project = enabled_root(payload.get("cwd") or os.getcwd())
    if project is None:
        return 0
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
        "cwd": payload.get("cwd") or os.getcwd(),
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


STATE_MENTION = re.compile(r"""[^\s'"`;|&()<>=]*(?:chrys/cross-validate|cross-validate/projects)[^\s'"`;|&()<>]*""")


def _state_access_allowed(token: str, payload: dict) -> bool:
    """细则 5.6: the user's cv command; the alignment agent's own W and a run still in progress."""
    home = str(Path.home())
    path = token.replace("${HOME}", home).replace("$HOME", home)
    path = os.path.normpath(home + path[1:] if path.startswith("~/") else path)
    if re.search(r"(?:^|/)(?:\.?chrys)/cross-validate/bin(?:/|$)", path):
        return True
    if payload.get("profile") != "cv-align":
        return False
    cwd = os.path.realpath(payload.get("cwd") or os.getcwd())
    # W inside the state directory is a snapshot. A W above it (say the home directory) grants nothing.
    if STATE_MENTION.search(cwd) and (path == cwd or path.startswith(cwd + os.sep)):
        return True
    # 细则 11.11: the trial headers of verify checks sit next to align/ in the same run.
    m = re.match(r"(.*/runs/[^/]+)/(?:align|verify-headers)(?:/|$)", path)
    return bool(m) and not Path(m.group(1), "report.md").exists()


def _history_guard(payload: dict) -> int:
    """细则 5.6: agents do not read other runs. Only explicit mentions of the state directory can be seen."""
    if payload.get("profile") == "cv-subtask":  # 细则 5.3 has its own guard
        return 0
    args = (payload.get("tool") or {}).get("args") or {}
    text = "\n".join(str(args.get(k) or "") for k in ("path", "command", "working_dir"))
    if all(_state_access_allowed(m, payload) for m in STATE_MENTION.findall(text)):
        return 0
    record = {
        "time": _now(),
        "cwd": payload.get("cwd") or os.getcwd(),
        "profile": payload.get("profile"),
        "tool": (payload.get("tool") or {}).get("name"),
    }
    with (
        contextlib.suppress(OSError),
        open(S.ensure_private(S.cv_home()) / "history-reads.jsonl", "a", encoding="utf-8") as f,
    ):
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    result = os.environ.get("CHRYS_HOOK_RESULT")
    if result:
        reason = "交叉验证的状态目录保存其他运行的结果，agent 不得读取。需要最新结论时运行 cv status。"
        Path(result).write_text(json.dumps({"action": "block", "reason": reason}, ensure_ascii=False), encoding="utf-8")
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
