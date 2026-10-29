import json
import re
import shutil
from pathlib import Path

import pytest

import cv_flow as FLOW
import cv_report as RP
import cv_state as S
from test_graph import SNAKE

ROOT = Path(__file__).resolve().parents[1]


def goal_run(home, tmp_path, variant="correct"):
    w = tmp_path / "w"
    shutil.copytree(SNAKE / variant, w, ignore=shutil.ignore_patterns("__pycache__"))
    state = FLOW.intake("实现贪吃蛇。", str(w), S.global_workflow_dir())
    state = FLOW.after_implement(state)
    state, _ = FLOW.split_check(state, (SNAKE / "split.json").read_text(encoding="utf-8"), 1)
    state, _ = FLOW.propose_check(state, (SNAKE / "subtask.txt").read_text(encoding="utf-8"), 1)
    return state, w


def aligned(state, config, align_src=SNAKE / "align"):
    FLOW.align_prompt(state)
    for f in align_src.iterdir():
        shutil.copy2(f, Path(state["run"]) / "align" / f.name)
    state, _ = FLOW.align_check(state, "已完成", 1, config)
    return state


def fast_config():
    config = FLOW.load_config()
    return config


@pytest.fixture
def light_align(tmp_path):
    """The snake checks with 3 runs instead of 50."""
    d = tmp_path / "align"
    shutil.copytree(SNAKE / "align", d)
    data = json.loads((d / "checks.json").read_text(encoding="utf-8"))
    for c in data["checks"]:
        if c["kind"] == "trace":
            c["runs"] = 3
    (d / "checks.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return d


def test_state_outside_project(home, tmp_path):
    """细则 3.1 3.2 runs live under ~/.chrys/cross-validate/projects/<key>/runs/<run>, mode 700."""
    state, w = goal_run(home, tmp_path)
    run = Path(state["run"])
    assert run.parent.parent.name == S.project_key(w) and re.fullmatch(r"[0-9a-f]{16}-w", S.project_key(w))
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{4}", run.name)
    assert run.stat().st_mode & 0o777 == 0o700 and S.project_dir(w).stat().st_mode & 0o777 == 0o700
    for name in (
        "run.json",
        "requirement.json",
        "requirement.txt",
        "sentences.json",
        "subtask.txt",
        "propositions.json",
        "digests.json",
    ):
        assert (run / name).exists(), name
    assert sorted(p.name for p in w.iterdir()) == ["snake.py"]


def test_intake_modes(home, tmp_path):
    """细则 4.1 @cv-run: must name a run directory of an enabled project; anything else is a goal."""
    w = tmp_path / "w"
    w.mkdir()
    with pytest.raises(RuntimeError, match="不是任何已启用项目的运行目录"):
        FLOW.intake("@cv-run:/tmp/nowhere", str(w), S.global_workflow_dir())
    run = S.create_run(w)
    S.write_json(run / "run.json", {"mode": "积累", "snapshot": str(tmp_path / "elsewhere")})
    with pytest.raises(RuntimeError, match="快照"):
        FLOW.intake(f"@cv-run:{run}", str(w), S.global_workflow_dir())
    with pytest.raises(RuntimeError, match="目标为空"):
        FLOW.intake("  ", str(w), S.global_workflow_dir())
    assert FLOW.intake("实现贪吃蛇。", str(w), S.global_workflow_dir())["mode"] == FLOW.MODE_GOAL


def test_only_installed_workflow_runs(home, tmp_path):
    """细则 5.1 a copy of the workflow outside the global directory refuses to run."""
    with pytest.raises(RuntimeError, match="全局目录"):
        FLOW.intake("实现贪吃蛇。", str(tmp_path), ROOT / "cross-validate")


def test_goal_requirement(home, tmp_path):
    """细则 4.2 6.3 in goal mode R is the goal itself and a sentence quotes itself."""
    state, _ = goal_run(home, tmp_path)
    run = Path(state["run"])
    req = S.read_json(run / "requirement.json")
    assert req["entries"] == [
        {"text": "实现贪吃蛇。", "quotes": [{"from": "目标", "text": "实现贪吃蛇。"}], "start": 0, "end": 6}
    ]
    assert S.read_json(run / "sentences.json")[0]["quotable"] == ["实现贪吃蛇。"]


def test_tampering_is_detected(home, tmp_path, light_align):
    """细则 5.2 14.2 a frozen input changed before the checker runs makes the report invalid."""
    config = fast_config()
    state, _ = goal_run(home, tmp_path)
    state = aligned(state, config, light_align)
    frozen = Path(state["run"]) / "align" / "checks.frozen.json"
    frozen.write_text(frozen.read_text(encoding="utf-8").replace('"runs": 3', '"runs": 1'), encoding="utf-8")
    state = FLOW.run_checks(state, config)
    text = FLOW.report(state, config)
    assert "**无效**" in text and "align/checks.frozen.json" in text


def test_subtask_tool_calls_invalidate(home, tmp_path):
    """细则 5.3 a recorded tool call of the subtask agent makes the run invalid."""
    state, _ = goal_run(home, tmp_path)
    (Path(state["run"]) / "tool-violations.jsonl").write_text(
        json.dumps({"tool": "read_file"}) + "\n", encoding="utf-8"
    )
    text = FLOW.report(state, fast_config())
    assert "**无效**" in text and "read_file" in text


def test_goal_mode_violation_log(home, tmp_path):
    """细则 5.3 without CV_RUN_DIR the hook's global log counts for runs in the same directory."""
    state, w = goal_run(home, tmp_path)
    log = S.cv_home() / "tool-violations.jsonl"
    log.write_text(
        json.dumps({"tool": "zsh", "cwd": str(w), "time": "2999-01-01T00:00:00+00:00"})
        + "\n"
        + json.dumps({"tool": "grep", "cwd": "/elsewhere", "time": "2999-01-01T00:00:00+00:00"})
        + "\n",
        encoding="utf-8",
    )
    assert [v["tool"] for v in FLOW.violations(state)] == ["zsh"]


def test_w_must_not_change(home, tmp_path, light_align):
    """细则 5.4 an original file of W changed by the checks makes the report invalid; new files are listed."""
    config = fast_config()
    state, w = goal_run(home, tmp_path)
    state = aligned(state, config, light_align)
    (w / "snake.py").write_text("# changed\n", encoding="utf-8")
    state = FLOW.run_checks(state, config)
    assert "W 中原有的文件被修改或删除：snake.py" in FLOW.report(state, config)


def test_conclusions(home, tmp_path, light_align):
    """细则 14.1 14.2 D-17 sections, statistics and the four conclusions."""
    config = fast_config()
    state, _ = goal_run(home, tmp_path, "buggy")
    state = FLOW.run_checks(aligned(state, config, light_align), config)
    text = FLOW.report(state, config)
    for section in ("## 需求", "## 代码", "## 命题", "## 绑定", "## 统计", "## 比对结论", "## 说明"):
        assert section in text
    assert "提交检查的命题 10 条：翻译 0 条，推理 10 条，未套用句式 0 条" in text and "式 (7)" in text
    assert "**比对未通过**" in text
    data = {
        "invalid": [],
        "stopped": None,
        "stats": {"submitted": 4, "discarded": 3},
        "results": {"R1": {"result": "通过"}},
    }
    assert RP.conclusion(data, 0.5)[0] == RP.NO_CONCLUSION
    assert RP.conclusion({**data, "results": {}}, 0.5)[0] == RP.NO_CONCLUSION
    ok = {**data, "stats": {"submitted": 4, "discarded": 1}, "results": {"R1": {"result": "推定为假"}}}
    assert RP.conclusion(ok, 0.5)[0] == RP.PASSED
    assert RP.conclusion({**ok, "invalid": ["x"]}, 0.5)[0] == RP.INVALID


def test_algorithm_lines_annotated():
    """细则 4.5 D-21 every line of algorithm 1 is marked in the code."""
    code = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "cross-validate").glob("*.py"))
    for n in range(1, 9):
        assert f"算法1 第{n}行" in code, n


def test_align_input(home, tmp_path):
    """细则 11.1 D-12 the alignment agent gets declarations, propositions, W and its directory, never R."""
    state, w = goal_run(home, tmp_path)
    text = FLOW.align_prompt(state)
    assert "谓词  吃到(蛇, 食物)" in text and "G(吃到(蛇, 食物) → 长度(蛇)′ = 长度(蛇) + 1)" in text
    assert str(w) in text and str(Path(state["run"]) / "align") in text
    # Neither R nor the quoted or reasoned grounds of the propositions (细则 17.4).
    assert "实现贪吃蛇" not in text and "吃到食物是蛇变长的方式" not in text


def test_frozen_checks_run(home, tmp_path, light_align):
    """细则 11.9 the checker runs the frozen checks, not the agent's file as it is later."""
    config = fast_config()
    state, _ = goal_run(home, tmp_path)
    state = aligned(state, config, light_align)
    agent_file = Path(state["run"]) / "align" / "checks.json"
    data = json.loads(agent_file.read_text(encoding="utf-8"))
    data["checks"] = [c for c in data["checks"] if c["proposition"] != "R5"]
    agent_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    state = FLOW.run_checks(state, config)
    results = S.read_json(Path(state["run"]) / "results.json")
    assert any(c["proposition"] == "R5" for c in results["checks"])
