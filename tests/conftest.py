"""Shared fixtures: copy an example project, plan it from recorded generator answers, judge completions."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "cross-validate"
EXAMPLES = ROOT / "examples"
sys.path.insert(0, str(PLUGIN))

import judge  # noqa: E402


class Project:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifests = 0

    def read(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")

    def plan(self, requirements: str, segments: str, a: str | None, b: str | None, *, rewrite_a=None, rewrite_b=None):
        state = judge.intake(requirements, self.root)
        state = judge.plan_prepare(state, "plan")
        if state["has_work"]:
            state = judge.plan_requests(state, self.read(segments))
            state = judge.plan_check(state, a and self.read(a), b and self.read(b))
        return judge.plan_freeze(state, rewrite_a, rewrite_b)

    def complete(self, state: dict, files: list[str], *, set_id: str | None = None) -> dict:
        self.manifests += 1
        set_id = set_id or judge.Store(self.root).load("requirements", {})["id"]
        path = self.root / ".cv" / "manifests" / f"{self.manifests:03d}.json"
        path.write_text(json.dumps({"requirements_id": set_id, "files": files}), encoding="utf-8")
        return judge.compare(judge.verify(state))

    def plan_record(self) -> dict:
        return judge.Store(self.root).load("plan", {})

    def segment(self, item: str = "R1", seg: str = "S1") -> dict:
        record = self.plan_record()["items"][item]
        return next(s for s in record["segments"] if s["id"] == seg)


@pytest.fixture
def make_project(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_STATE_DIR", str(tmp_path / "state"))

    def make(example: str, name: str | None = None) -> Project:
        root = tmp_path / (name or example)
        shutil.copytree(EXAMPLES / example, root)
        (root / ".cv").mkdir(exist_ok=True)
        shutil.copy(root / "interface.json", root / ".cv" / "interface.json")
        return Project(root)

    return make
