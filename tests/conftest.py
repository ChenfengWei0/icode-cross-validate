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
    return h
