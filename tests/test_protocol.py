"""Protocol messages (§3 协议) and the intake of the requirement item set."""

from __future__ import annotations

import json

import pytest

import cv_protocol as P
import judge


def test_item_set_fields_and_default_id():
    q = P.validate_item_set({"items": [{"id": "R1", "text": "t", "examples": ["e"], "source": "s"}]})
    assert q["status"] == P.STATUS_NORMAL and q["id"].startswith("Q-")
    partial = P.validate_item_set({"items": [{"id": "R1", "text": "t"}], "unprocessed": ["图片"]})
    assert partial["status"] == P.STATUS_PARTIAL
    assert P.validate_item_set({"items": []})["status"] == P.STATUS_EMPTY


@pytest.mark.parametrize(
    "bad",
    [
        {"items": [{"id": "R1", "text": ""}]},
        {"items": [{"id": "R1", "text": "a"}, {"id": "R1", "text": "b"}]},
        {"items": "R1"},
        {"status": "empty", "items": [{"id": "R1", "text": "a"}]},
    ],
)
def test_item_set_rejects_malformed_input(bad):
    with pytest.raises(P.ProtocolError):
        P.validate_item_set(bad)


def test_manifest_must_name_the_item_set_and_files():
    assert P.validate_manifest({"requirements_id": "Q-1", "files": ["a.c"]}, "Q-1")["files"] == ["a.c"]
    with pytest.raises(P.ProtocolError):
        P.validate_manifest({"requirements_id": "Q-2", "files": ["a.c"]}, "Q-1")
    with pytest.raises(P.ProtocolError):
        P.validate_manifest({"requirements_id": "Q-1", "files": []}, "Q-1")


def test_free_text_is_passed_through_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_STATE_DIR", str(tmp_path / "state"))
    state = judge.intake("实现一个计数器。\n每次加一。", tmp_path)
    q = judge.Store(tmp_path).load("requirements", {})
    assert q["items"] == [
        {"id": "R1", "text": "实现一个计数器。\n每次加一。", "examples": [], "source": "workflow input"}
    ]
    assert any("需求处理不属于交叉验证" in n for n in state["notes"])


def test_empty_item_set_passes_with_a_note(tmp_path, monkeypatch):
    monkeypatch.setenv("CV_STATE_DIR", str(tmp_path / "state"))
    state = judge.intake(json.dumps({"items": [], "unprocessed": ["一张截图"]}), tmp_path)
    assert state["empty"] and state["task"] == "一张截图"
    state = judge.plan_freeze(judge.plan_prepare(state, "plan"), None, None)
    (tmp_path / ".cv/manifests/001.json").write_text(json.dumps({"requirements_id": "x", "files": ["a.c"]}))
    state = judge.compare(judge.verify(state))
    assert state["signal"] == P.SIGNAL_PASS
    assert "无可检查的需求" in state["report"]["notes"]


def test_missing_interface_leaves_everything_uncovered(make_project):
    project = make_project("discount")
    (project.root / ".cv/interface.json").unlink()
    state = project.plan("requirements.json", "generator/segments.txt", "generator/source_a.txt", None)
    seg = project.segment("R1", "S0")
    assert seg["status"] == P.SEG_UNCOVERED and "接口声明" in seg["reason"]
    assert state["plan_summary"]["segments"] == {"uncovered": 1}


def test_config_rejects_unknown_keys(tmp_path):
    (tmp_path / ".cv").mkdir()
    (tmp_path / ".cv/config.json").write_text(json.dumps({"retry_limt": 2}))
    with pytest.raises(ValueError, match="unknown configuration keys"):
        judge.load_config(tmp_path)


def test_build_keys_cannot_be_overridden_by_the_project(tmp_path):
    (tmp_path / ".cv").mkdir()
    (tmp_path / ".cv/config.json").write_text(json.dumps({"generator_profile": "Code", "retry_limit": 5}))
    config = judge.load_config(tmp_path)
    assert config["generator_profile"] == "cv-generator" and config["retry_limit"] == 5
