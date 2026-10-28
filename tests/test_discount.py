"""The design's ambiguity example (§3 流程 4): 下单时总价满 100 元减 20 元."""

from __future__ import annotations

import json

import cv_protocol as P
import judge

PLAN = ("requirements.json", "generator/segments.txt", "generator/source_a.txt", "generator/source_b.txt")


def test_two_readings_are_kept_with_a_distinguishing_input(make_project):
    project = make_project("discount")
    project.plan(*PLAN)
    seg = project.segment()
    assert seg["status"] == P.SEG_AMBIGUOUS
    assert seg["distinguishing"] and seg["distinguishing"].startswith("discount(")
    assert {r["label"] for r in seg["readings"]} == {"只减一次", "每满 100 元减一次"}


def test_code_matching_no_reading_is_blocked_with_counterexample_only(make_project):
    project = make_project("discount")
    state = project.plan(*PLAN)
    state = project.complete(state, ["src/wrong.c"])
    assert state["signal"] == P.SIGNAL_BLOCK
    feedback = state["feedback"]
    assert "下单时总价满 100 元减 20 元" in feedback and "discount(total=" in feedback
    for leaked in ("cv_post", "assert", "CHECK_REACH", "只减一次", "每满"):
        assert leaked not in feedback  # 反馈里不放子任务产物本身


def test_any_reading_passing_passes_and_records_the_reading(make_project):
    for impl, reading in (("src/once.c", "只减一次"), ("src/every.c", "每满 100 元减一次")):
        project = make_project("discount", name=f"discount-{reading}")
        state = project.complete(project.plan(*PLAN), [impl])
        assert state["signal"] == P.SIGNAL_PASS
        row = state["report"]["segments"][0]
        assert (row["result"], row["reading"], row["strength"]) == ("pass", reading, "proof")
        contract = json.loads((project.root / ".cv/contracts/R1.S1.json").read_text(encoding="utf-8"))
        assert contract["reading"] == reading and contract["ambiguous"] is True


def test_example_removes_the_contradicting_reading(make_project):
    project = make_project("discount")
    state = project.plan(
        "requirements_with_example.json", "generator/segments_with_example.txt",
        "generator/source_a.txt", "generator/source_b.txt",
    )  # fmt: skip
    seg = project.segment()
    assert seg["status"] == P.SEG_DETERMINED
    assert [r["label"] for r in seg["readings"]] == ["只减一次"]
    assert any("示例" in r["why"] for r in seg["removed"])
    assert project.complete(state, ["src/every.c"])["signal"] == P.SIGNAL_BLOCK
    assert project.complete(state, ["src/once.c"])["signal"] == P.SIGNAL_PASS


def test_pause_when_disambiguation_is_required(make_project):
    project = make_project("discount")
    (project.root / ".cv" / "config.json").write_text(json.dumps({"require_disambiguation": True}), encoding="utf-8")
    state = project.complete(project.plan(*PLAN), ["src/once.c"])
    assert state["signal"] == P.SIGNAL_PAUSE
    assert "区分输入：discount(" in state["feedback"]


def test_retry_limit_rejects(make_project):
    project = make_project("discount")
    state = project.plan(*PLAN)
    signals = []
    for _ in range(3):
        state = project.complete(state, ["src/wrong.c"])
        signals.append(state["signal"])
    assert signals == [P.SIGNAL_BLOCK, P.SIGNAL_BLOCK, P.SIGNAL_REJECT]


def test_report_tells_first_pass_from_pass_after_modification(make_project):
    project = make_project("discount")
    state = project.plan(*PLAN)
    state = project.complete(state, ["src/wrong.c"])
    state = project.complete(state, ["src/once.c"])
    assert state["report"]["functions"]["discount"]["pass"] == "修改后通过"


def test_first_pass_is_reported_as_such(make_project):
    project = make_project("discount")
    state = project.complete(project.plan(*PLAN), ["src/once.c"])
    assert state["report"]["functions"]["discount"]["pass"] == "第一次比对即通过"


def test_tampered_artifact_is_rejected(make_project):
    project = make_project("discount")
    state = project.plan(*PLAN)
    path = project.root / ".cv/subtasks/R1.S1.A1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["content"]["spec"] = data["content"]["spec"].replace("total - 20", "total - 30")
    path.write_text(json.dumps(data), encoding="utf-8")
    state = project.complete(state, ["src/wrong.c"])
    assert state["signal"] == P.SIGNAL_REJECT
    assert "R1.S1.A1" in state["feedback"]


def test_no_new_declaration_passes_without_checking(make_project):
    project = make_project("discount")
    state = project.plan(*PLAN)
    state = judge.compare(judge.verify(state))
    assert state["signal"] == P.SIGNAL_PASS
    assert any("没有新的完成声明" in n for n in state["report"]["notes"])


def test_invalid_declaration_is_sent_back(make_project):
    project = make_project("discount")
    state = project.plan(*PLAN)
    state = project.complete(state, ["src/once.c"], set_id="Q-other")
    assert state["signal"] == P.SIGNAL_BLOCK
    assert "requirements_id" in state["feedback"]


def test_header_change_replans_the_affected_items(make_project):
    project = make_project("discount")
    state = project.plan(*PLAN)
    header = project.root / "include/discount.h"
    header.write_text(
        header.read_text(encoding="utf-8").replace("long discount(long total);", "long discount(long amount);"),
        encoding="utf-8",
    )
    manifest = project.root / ".cv/manifests/001.json"
    manifest.write_text(json.dumps({"requirements_id": "Q-discount", "files": ["src/once.c"]}), encoding="utf-8")
    verified = judge.verify(state)
    assert verified["signal"] is None and verified["replan_items"] == ["R1"]
    # The plan stage runs again for R1; with the recorded answers the readings come back.
    replanned = judge.plan_prepare(verified, "replan")
    assert replanned["has_work"]
    replanned = judge.plan_requests(replanned, project.read("generator/segments.txt"))
    replanned = judge.plan_check(
        replanned, project.read("generator/source_a.txt"), project.read("generator/source_b.txt")
    )
    replanned = judge.plan_freeze(replanned, None, None)
    (project.root / "src/once.c").write_text(
        project.read("src/once.c").replace("long total", "long amount").replace("total", "amount"), encoding="utf-8"
    )
    assert judge.compare(replanned)["signal"] == P.SIGNAL_PASS


def test_results_are_deterministic(make_project):
    from checker import compare
    from cv_clang import read_implementation

    project = make_project("discount")
    project.plan(*PLAN)
    store = judge.Store(project.root)
    interface = store.load("interface", None)
    artifact = project.segment()["readings"][0]["artifacts"][0]
    outcomes = []
    for run in range(2):
        ctx = judge._context(store, judge.load_config(project.root), interface, judge._workdir(store, f"det{run}"))
        ctx.impl_files = [project.root / "src/wrong.c"]
        impl = read_implementation(project.root, ["src/wrong.c"], ["include"], ["-std=c11"], interface["snapshot"])
        outcomes.append(compare(ctx, artifact, impl))
    assert outcomes[0] == outcomes[1] and outcomes[0]["status"] == "fail"
