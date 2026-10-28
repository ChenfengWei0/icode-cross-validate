"""What each component can read (§3 组件; 约束 3) and where the frozen record lives (§3 集成)."""

from __future__ import annotations

import json

import cv_store
import judge

PLAN = ("requirements_with_example.json", "generator/segments_with_example.txt")


def _prepared(project):
    state = judge.intake(PLAN[0], project.root)
    state = judge.plan_prepare(state, "plan")
    return judge.plan_requests(state, project.read(PLAN[1]))


def test_generator_prompts_never_contain_the_implementation(make_project):
    project = make_project("discount")
    marker = "/* IMPLEMENTATION-MARKER */"
    (project.root / "src/once.c").write_text(project.read("src/once.c") + marker, encoding="utf-8")
    state = _prepared(project)
    prompts = [judge.segment_prompt(state), judge.generation_prompt(state, "A"), judge.generation_prompt(state, "B")]
    for prompt in prompts:
        assert marker not in prompt and "/* 只减一次。 */" not in prompt  # nothing from src/
        assert "long discount(long total);" in prompt  # the header is there


def test_prompt_examples_do_not_suggest_a_reading_of_the_design_examples():
    """Both sources see the conventions; an example from a real requirement would correlate them (约束 3)."""
    text = (judge.HERE / "prompts" / "conventions.md").read_text(encoding="utf-8")
    for phrase in ("满 100", "减 20", "余额", "退款", "account", "discount"):
        assert phrase not in text


def test_source_a_reads_the_item_only_and_source_b_adds_the_examples(make_project):
    project = make_project("discount")
    state = _prepared(project)
    assert "买 200 元的商品付 180 元" not in judge.generation_prompt(state, "A")
    assert "买 200 元的商品付 180 元" in judge.generation_prompt(state, "B")


def test_frozen_hashes_are_recorded_outside_the_project(make_project):
    project = make_project("discount")
    project.plan(*PLAN, "generator/source_a.txt", "generator/source_b.txt")
    store = cv_store.Store(project.root)
    assert project.root not in store.root.parents
    frozen = store.load("frozen", {})
    assert frozen["artifacts"] and all(path.startswith(".cv/subtasks/") for path in frozen["artifacts"])
    assert not list(project.root.rglob("frozen.json"))


def test_same_model_and_no_examples_is_reported(make_project):
    project = make_project("discount")
    project.plan("requirements.json", "generator/segments.txt", "generator/source_a.txt", "generator/source_b.txt")
    assert judge.SAME_SOURCE_NOTE in project.segment()["notes"]


def test_feedback_and_contracts_on_pass(make_project):
    project = make_project("discount")
    state = project.plan(*PLAN, "generator/source_a.txt", "generator/source_b.txt")
    state = project.complete(state, ["src/once.c"])
    contract = json.loads((project.root / ".cv/contracts/R1.S1.json").read_text(encoding="utf-8"))
    assert contract["ambiguous"] is False and contract["reading"] == "只减一次"
    assert state["report"]["signal"] == "pass"
