import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cross-validate"))
sys.path.insert(0, str(ROOT))

import cv_formula as F  # noqa: E402


def snake_decls() -> F.Decls:
    """The declarations of the document's snake example (细则 7 示例)."""
    d = F.Decls()
    for e in ("蛇", "食物", "游戏"):
        d.category[e] = "实体"
        d.params[e] = []
    preds = {
        "吃到": ["蛇", "食物"],
        "撞到自身": ["蛇"],
        "新生成": ["食物"],
        "位于蛇身": ["食物"],
        "移动": ["蛇"],
        "结束": ["游戏"],
    }
    for name, params in preds.items():
        d.category[name], d.params[name] = "谓词", params
    for name in ("头", "尾"):
        d.category[name], d.params[name] = "位置", ["蛇"]
    d.category["长度"], d.params["长度"] = "数量", ["蛇"]
    d.category["格子总数"], d.params["格子总数"] = "数量", []
    for a in ("右", "下", "左", "上"):
        d.category[a], d.params[a] = "动作", []
    d.category["move"], d.params["move"] = "接口", []
    return d


@pytest.fixture
def decls() -> F.Decls:
    return snake_decls()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An iCode configuration directory with the workflow installed globally."""
    h = tmp_path / "chrys"
    monkeypatch.setenv("CV_CHRYS_HOME", str(h))
    shutil.copytree(
        ROOT / "cross-validate", h / "workflows" / "cross-validate", ignore=shutil.ignore_patterns("__pycache__")
    )
    import cv_state as S

    S.write_json(
        S.ensure_private(S.cv_home()) / "install.json",
        {"workflow_digest": S.folder_digest(h / "workflows" / "cross-validate")},
    )
    fake_hook_list(tmp_path, monkeypatch, GUARD_LISTING)
    return h


GUARD = {
    "id": "cv-subtask-guard",
    "event": "before_tool_call",
    "enabled": True,
    "execution": {"mode": "blocking", "detach": False, "timeout_seconds": 10.0, "on_error": "block"},
    "match": {"profile": "cv-subtask", "profiles": [], "tool_kind": None, "tool_name": None, "args": {}},
    "run": {"type": "command", "path": None, "program": "/usr/bin/python3"},
}
GUARD_LISTING = {
    "version": 1,
    "files": [{"scope": "global", "path": "hooks.yaml", "state": "loaded", "hooks": [GUARD]}],
}


def fake_hook_list(tmp_path, monkeypatch, listing, returncode=0):
    """细则 5.3 an icode whose `hook list --json` prints `listing` (what an installed plugin shows by default)."""
    out = tmp_path / "hook-list.json"
    out.write_text(listing if isinstance(listing, str) else json.dumps(listing), encoding="utf-8")
    exe = tmp_path / "icode-hooks"
    exe.write_text(
        f"#!{sys.executable}\nimport sys\nprint(open({str(out)!r}, encoding='utf-8').read())\nsys.exit({returncode})\n",
        encoding="utf-8",
    )
    exe.chmod(0o755)
    monkeypatch.setenv("CV_ICODE", str(exe))


@pytest.fixture
def light_align(tmp_path):
    """The snake checks with 3 runs instead of 50."""
    d = tmp_path / "align"
    shutil.copytree(ROOT / "examples" / "snake" / "align", d)
    data = json.loads((d / "checks.json").read_text(encoding="utf-8"))
    for c in data["checks"]:
        if c["kind"] == "trace":
            c["runs"] = 3
    (d / "checks.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d
