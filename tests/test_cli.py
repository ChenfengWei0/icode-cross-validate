import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import cv_cli as CLI
import cv_ledger as L
import cv_state as S

ROOT = Path(__file__).resolve().parents[1]

FAKE_ICODE = r"""#!{python}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
log = Path(os.environ["FAKE_LOG"])
with open(log, "a") as f:
    f.write(json.dumps({{"args": args, "cwd": os.getcwd(), "internal": os.environ.get("CV_INTERNAL"), "run_dir": os.environ.get("CV_RUN_DIR")}}) + "\n")
home = Path(os.environ["CV_CHRYS_HOME"])
if args[:2] == ["workflow", "list"]:
    src = os.environ.get("FAKE_SOURCE", "global")
    print(json.dumps({{"workflows": [{{"id": "cross-validate", "source": src, "layout": "package", "title": "",
        "path": str(home / "workflows" / "cross-validate" / "cross-validate.py")}}]}}))
elif args[:2] == ["workflow", "run"]:
    run = Path(args[args.index("--input") + 1].split(":", 1)[1])
    (run / "report.md").write_text("# 交叉验证可信度报告\n\n## 比对结论\n**比对通过**：假的。\n", encoding="utf-8")
    extra = os.environ.get("FAKE_APPEND")
    if extra and not Path(extra + ".done").exists():
        Path(extra + ".done").touch()
        with open(extra, "a") as f:
            f.write(json.dumps({{"id": "M99", "time": "", "session": "s", "injected": False, "text": "又补充一条"}}, ensure_ascii=False) + "\n")
print("")
"""


@pytest.fixture
def fake_icode(tmp_path, monkeypatch, home):
    path = tmp_path / "icode"
    path.write_text(FAKE_ICODE.format(python=sys.executable), encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setenv("CV_ICODE", str(path))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "icode.log"))
    return tmp_path / "icode.log"


def calls(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def git_project(tmp_path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=p, check=True)
    (p / "snake.py").write_text("print('snake')\n")
    (p / ".gitignore").write_text(".env\nnode_modules/\nbuild/\n")
    subprocess.run(["git", "add", "."], cwd=p, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=p, check=True)
    return p


def installed(home, fake_icode):
    """Record the digest the launcher compares against (what cv install writes)."""
    S.write_json(
        S.ensure_private(S.cv_home()) / "install.json", {"workflow_digest": S.folder_digest(S.global_workflow_dir())}
    )


def hook(event, payload, tmp_path, monkeypatch, env=None):
    pf, rf = tmp_path / "payload.json", tmp_path / "result.json"
    pf.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rf.write_text("")
    monkeypatch.setenv("CHRYS_HOOK_PAYLOAD_FILE", str(pf))
    monkeypatch.setenv("CHRYS_HOOK_RESULT", str(rf))
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    assert CLI.hook(event) == 0
    return rf.read_text()


def test_init_rules(home, tmp_path, monkeypatch):
    """细则 15.1 cv init refuses home and root, and non-git projects without --allow-non-git."""
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(SystemExit, match="家目录"):
        CLI.main(["init", str(tmp_path)])
    with pytest.raises(SystemExit, match="家目录或根目录"):
        CLI.main(["init", "/"])
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(SystemExit, match="allow-non-git"):
        CLI.main(["init", str(plain)])
    assert CLI.main(["init", str(plain), "--allow-non-git"]) == 0 and CLI.enabled(plain)
    assert sorted(p.name for p in plain.iterdir()) == []


def test_record_hook_filters(home, tmp_path, monkeypatch):
    """细则 15.2 16.5 16.6 only enabled projects, never internal runs, workflow sessions, / commands; nothing injected."""
    p = git_project(tmp_path)
    base = {
        "event": "user_prompt_submit",
        "session_id": "s1",
        "profile": "Code",
        "cwd": str(p),
        "text": "实现贪吃蛇。",
        "injected": False,
    }
    assert hook("record", base, tmp_path, monkeypatch) == ""
    assert not CLI.enabled(p)
    CLI.main(["init", str(p)])
    hook("record", {**base, "text": "/help"}, tmp_path, monkeypatch)
    hook("record", {**base, "session_kind": "workflow"}, tmp_path, monkeypatch)
    hook("record", {**base, "profile": "cv-align"}, tmp_path, monkeypatch)
    hook("record", base, tmp_path, monkeypatch, {"CV_INTERNAL": "1"})
    monkeypatch.delenv("CV_INTERNAL")
    result = hook("record", {**base, "injected": True}, tmp_path, monkeypatch)
    assert result == ""
    msgs = L.read_messages(L.ledger_file(p))
    assert [(m["id"], m["text"], m["injected"], m["session"]) for m in msgs] == [("M1", "实现贪吃蛇。", True, "s1")]


def test_guard_hook(home, tmp_path, monkeypatch):
    """细则 5.3 I-HK-2 the guard refuses every call and records it in the run directory."""
    run = tmp_path / "run"
    run.mkdir()
    payload = {
        "event": "before_tool_call",
        "profile": "cv-subtask",
        "cwd": str(tmp_path),
        "tool": {"name": "read_file", "args": {}},
    }
    result = json.loads(hook("guard", payload, tmp_path, monkeypatch, {"CV_RUN_DIR": str(run)}))
    assert result["action"] == "block"
    assert json.loads((run / "tool-violations.jsonl").read_text())["tool"] == "read_file"


def test_turn_and_session_triggers(home, tmp_path, monkeypatch, fake_icode):
    """细则 16.1 16.2 14.3 15.5 after_turn needs N new messages; session_end any; the launcher runs icode in a snapshot."""
    installed(home, fake_icode)
    p = git_project(tmp_path)
    CLI.main(["init", str(p)])
    for t in ("实现贪吃蛇。", "继续"):
        L.append_message(p, "s", t, False)
    hook("turn", {"event": "after_turn", "status": "ok", "cwd": str(p), "profile": "Code"}, tmp_path, monkeypatch)
    assert not calls(fake_icode)
    L.append_message(p, "s", "蛇撞墙也要结束", False)
    hook("turn", {"event": "after_turn", "status": "failed", "cwd": str(p), "profile": "Code"}, tmp_path, monkeypatch)
    assert not calls(fake_icode)
    hook("turn", {"event": "after_turn", "status": "ok", "cwd": str(p), "profile": "Code"}, tmp_path, monkeypatch)
    run_calls = [c for c in calls(fake_icode) if c["args"][:2] == ["workflow", "run"]]
    assert len(run_calls) == 1
    c = run_calls[0]
    assert c["internal"] == "1" and c["run_dir"] and Path(c["cwd"]).parent == S.cv_home() / "snapshots"
    assert not Path(c["cwd"]).exists(), "细则 16.4 the snapshot is deleted after the run"
    run = Path(c["run_dir"])
    info = S.read_json(run / "run.json")
    assert (
        info["trigger"] == "一轮结束" and info["last_message"] == "M3" and info["code"]["version"].startswith("提交 ")
    )
    assert (S.project_dir(p) / "reports" / "latest.md").exists() and (
        S.project_dir(p) / "reports" / f"{run.name}.md"
    ).exists()
    # 细则 15.5: the run gets the whole ledger, not only the new messages.
    assert [m["id"] for m in L.read_messages(run / "ledger.jsonl")] == ["M1", "M2", "M3"]
    hook("session_end", {"event": "session_end", "cwd": str(p), "profile": "Code"}, tmp_path, monkeypatch)
    assert len([c for c in calls(fake_icode) if c["args"][:2] == ["workflow", "run"]]) == 1
    L.append_message(p, "s", "最后一条", False)
    hook("session_end", {"event": "session_end", "cwd": str(p), "profile": "Code"}, tmp_path, monkeypatch)
    runs = [c for c in calls(fake_icode) if c["args"][:2] == ["workflow", "run"]]
    assert len(runs) == 2 and S.read_json(Path(runs[1]["run_dir"]) / "run.json")["trigger"] == "会话结束"


def test_lock_and_pending(home, tmp_path, monkeypatch, fake_icode):
    """细则 16.3 a trigger that finds the lock leaves pending; the holder runs again for unseen messages."""
    installed(home, fake_icode)
    p = git_project(tmp_path)
    CLI.main(["init", str(p)])
    L.append_message(p, "s", "实现贪吃蛇。", False)
    pdir = S.project_dir(p)
    with open(pdir / "lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert CLI.launch(p, "手动") is None
        assert (pdir / "pending").exists()
    monkeypatch.setenv("FAKE_APPEND", str(L.ledger_file(p)))
    CLI.launch(p, "手动")
    runs = [c for c in calls(fake_icode) if c["args"][:2] == ["workflow", "run"]]
    assert len(runs) == 2 and not (pdir / "pending").exists()
    assert S.read_json(Path(runs[1]["run_dir"]) / "run.json")["last_message"] == "M99"


def test_snapshot(home, tmp_path):
    """细则 16.4 git files, no .git/.chrys, linked and copied ignored files, symlinks, limits."""
    p = git_project(tmp_path)
    (p / "untracked.py").write_text("x = 1\n")
    (p / ".env").write_text("SECRET=1\n")
    (p / "node_modules" / "m").mkdir(parents=True)
    (p / "build").mkdir()
    (p / "build" / "out.bin").write_text("b")
    (p / ".chrys" / "workflows" / "cross-validate").mkdir(parents=True)
    (p / ".chrys" / "workflows" / "cross-validate" / "cross-validate.py").write_text("evil")
    os.symlink("snake.py", p / "inside_link.py")
    os.symlink("/etc/hostname", p / "outside_link")
    config = CLI._config()
    config["snapshot"] = {**config["snapshot"], "copy": [".env"], "link": ["node_modules"]}
    target = tmp_path / "snap"
    notes = CLI.snapshot(p, target, config)
    names = sorted(x.relative_to(target).as_posix() for x in target.rglob("*"))
    assert "snake.py" in names and "untracked.py" in names and ".env" in names and "inside_link.py" in names
    assert not any(n.startswith((".git/", ".chrys", "build")) for n in names) and "outside_link" not in names
    assert (target / "node_modules").is_symlink()
    assert (
        any("node_modules" in n for n in notes)
        and any("outside_link" in n for n in notes)
        and any(".env" in n for n in notes)
    )
    config["snapshot"]["max_files"] = 1
    with pytest.raises(CLI.SnapshotError, match="上限"):
        CLI.snapshot(p, tmp_path / "snap2", config)


def test_verify_workflow(home, tmp_path, monkeypatch, fake_icode):
    """细则 5.1 the launcher refuses a project copy of the workflow or a changed installed copy."""
    installed(home, fake_icode)
    assert CLI.verify_workflow(tmp_path) is None
    monkeypatch.setenv("FAKE_SOURCE", "project")
    assert "不是全局安装" in CLI.verify_workflow(tmp_path)
    monkeypatch.setenv("FAKE_SOURCE", "global")
    (S.global_workflow_dir() / "cv_run.py").write_text("# changed\n")
    assert "改动" in CLI.verify_workflow(tmp_path)


def test_install_uninstall(tmp_path, monkeypatch):
    """细则 16.7 2.1 I-HK-1 install merges cv- hooks without touching others; uninstall removes only ours."""
    h = tmp_path / "chrys"
    monkeypatch.setenv("CV_CHRYS_HOME", str(h))
    monkeypatch.setenv("CV_ICODE", str(tmp_path / "no-icode"))
    (h / "hooks").mkdir(parents=True)
    (h / "hooks" / "hooks.yaml").write_text(
        "version: 1\nhooks:\n  - id: mine\n    event: after_turn\n    run:\n      type: shell\n      shell: echo hi\n"
    )
    (h / "agents").mkdir()
    (h / "agents" / "renamed.yaml").write_text("name: cv-subtask\nid: abc\n")
    (h / "agents" / "other.yaml").write_text("name: Other\n")
    assert CLI.main(["install"]) == 0
    text = (h / "hooks" / "hooks.yaml").read_text()
    assert (
        "id: mine" in text and text.count("- id: cv-") == 4 and str(h / "cross-validate" / "bin" / "cv_hook.py") in text
    )
    assert sorted(p.name for p in (h / "agents").iterdir()) == ["cv-align.yaml", "cv-subtask.yaml", "other.yaml"]
    assert (h / "workflows" / "cross-validate" / "cross-validate.py").exists()
    assert S.read_json(h / "cross-validate" / "install.json")["workflow_digest"] == S.folder_digest(
        h / "workflows" / "cross-validate"
    )
    assert CLI.main(["install"]) == 0 and (h / "hooks" / "hooks.yaml").read_text().count("- id: cv-") == 4
    assert CLI.main(["uninstall"]) == 0
    text = (h / "hooks" / "hooks.yaml").read_text()
    assert "id: mine" in text and "cv-" not in text
    assert sorted(p.name for p in (h / "agents").iterdir()) == ["other.yaml"]
    assert not (h / "workflows" / "cross-validate").exists()
