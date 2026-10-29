import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import chrys_stub

ROOT = Path(__file__).resolve().parents[1]
SNAKE = ROOT / "examples" / "snake"


def load(home: Path):
    chrys_stub.install()
    entry = home / "workflows" / "cross-validate" / "cross-validate.py"
    spec = importlib.util.spec_from_file_location("cv_workflow_under_test", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snake_agents(calls):
    def answer(node, profile, prompt):
        calls.append((node, profile, prompt))
        if node == "implement":
            return "已实现"
        if node == "split":
            return (SNAKE / "split.json").read_text(encoding="utf-8")
        if node == "propose":
            return (SNAKE / "subtask.txt").read_text(encoding="utf-8")
        if node == "align":
            align = Path(re.search(r"你的目录：`([^`]+)`", prompt).group(1))
            for f in (SNAKE / "align").iterdir():
                shutil.copy2(f, align / f.name)
            return "已完成"
        raise AssertionError(f"unexpected agent node {node}")

    return answer


def test_graph_builds(home):
    """I-WF-1 I-WF-3 细则 4.4 the graph builds: unique names, joins, no cycle, everything reachable."""
    wf = load(home).workflow
    names = set(wf.b.nodes)
    for n in ("intake", "implement", "consolidate", "split", "propose", "align", "run_checks", "report"):
        assert n in names
    assert wf.b.outputs == ["report"]
    assert wf.b.nodes["run_checks"]["timeout"] is None


def test_goal_mode_snake(home, tmp_path, monkeypatch):
    """细则 4.2 4.4 D-2 D-22 I-WF-4 goal mode on the paper's snake example, through the whole graph."""
    w = tmp_path / "w"
    shutil.copytree(SNAKE / "buggy", w, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.chdir(w)
    calls = []
    outputs = load(home).workflow.run("实现贪吃蛇。", snake_agents(calls))
    report = outputs[0].text
    assert [c[0] for c in calls] == ["implement", "split", "propose", "align"]
    assert [c[1] for c in calls] == ["Code", "cv-subtask", "cv-subtask", "cv-align"]
    assert "| R5 | 推理 | 确定违反 |" in report and "**比对未通过**" in report
    # 细则 4.6, D-4: the subtask prompts hold the requirement, never W or the code.
    for _node, _, prompt in calls[1:3]:
        assert str(w) not in prompt and "snake.py" not in prompt
    # Only the checks' own byproducts (Python caches from importing snake.py) appear in W.
    assert {p.name for p in w.iterdir()} <= {"snake.py", "__pycache__"}, "细则 3.1 nothing is written into the project"


def test_accumulation_mode(home, tmp_path, monkeypatch):
    """细则 4.1 4.3 15.4 accumulation mode consolidates the ledger first, then runs the same stages."""
    sys.path.insert(0, str(ROOT / "cross-validate"))
    import cv_ledger as L
    import cv_state as S

    project = tmp_path / "proj"
    shutil.copytree(SNAKE / "correct", project)
    snap = tmp_path / "snap"
    shutil.copytree(project, snap)
    run = S.create_run(project)
    msgs = [
        {"id": "M1", "text": "实现贪吃蛇。", "time": "", "session": "s", "injected": False},
        {"id": "M2", "text": "继续", "time": "", "session": "s", "injected": False},
    ]
    (run / "ledger.jsonl").write_text(
        "\n".join(json.dumps(m, ensure_ascii=False) for m in L.preprocess(msgs)) + "\n", encoding="utf-8"
    )
    S.write_json(
        run / "run.json", {"mode": "积累", "trigger": "手动", "snapshot": str(snap), "code": {"version": "提交 abc"}}
    )
    monkeypatch.chdir(snap)
    calls = []
    agents = snake_agents(calls)

    def answer(node, profile, prompt):
        if node == "consolidate":
            calls.append((node, profile, prompt))
            return json.dumps(
                {
                    "requirement": [{"text": "实现贪吃蛇。", "quotes": [{"from": "M1", "text": "实现贪吃蛇。"}]}],
                    "messages": {"M1": "需求", "M2": "非需求"},
                },
                ensure_ascii=False,
            )
        return agents(node, profile, prompt)

    report = load(home).workflow.run(f"@cv-run:{run}", answer)[0].text
    assert [c[0] for c in calls] == ["consolidate", "split", "propose", "align"]
    assert "M1：「实现贪吃蛇。」" in report and "非需求：M2" in report and "**比对通过**" in report


def test_revision_paths(home, tmp_path, monkeypatch):
    """细则 6.1 9.3 11.7 one rewrite per stage; a split that stays wrong falls back to punctuation."""
    w = tmp_path / "w"
    shutil.copytree(SNAKE / "correct", w, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.chdir(w)
    calls = []
    base = snake_agents(calls)
    bad_r3 = (
        (SNAKE / "subtask.txt").read_text(encoding="utf-8").replace("总是 蛇的长度 不小于 1", "总是 蛇的长度 不小 1")
    )

    def answer(node, profile, prompt):
        if node in ("split", "split_redo"):
            calls.append((node, profile, prompt))
            return "[]"
        if node == "propose":
            calls.append((node, profile, prompt))
            return bad_r3
        if node == "propose_redo":
            calls.append((node, profile, prompt))
            assert "R3" in prompt and "无法解析" in prompt
            return "声明\n实体  蛇\n\n命题\n编号  R3\n来源  推理\n依据  〔不变式〕蛇至少占据一个格子\n句子  总是 蛇的长度 大于 0"
        if node == "align":
            out = base(node, profile, prompt)
            align = Path(re.search(r"你的目录：`([^`]+)`", prompt).group(1))
            (align / "checks.json").write_text("{not json", encoding="utf-8")
            (align / "keep.json").write_text(
                (SNAKE / "align" / "checks.json").read_text(encoding="utf-8"), encoding="utf-8"
            )
            return out
        if node == "align_redo":
            calls.append((node, profile, prompt))
            align = Path(re.search(r"你写出的 (\S+)/checks.json", prompt).group(1))
            shutil.copy2(align / "keep.json", align / "checks.json")
            return "已修正"
        return base(node, profile, prompt)

    report = load(home).workflow.run("实现贪吃蛇。", answer)[0].text
    assert [c[0] for c in calls if c[0] != "implement"] == [
        "split",
        "split_redo",
        "propose",
        "propose_redo",
        "align",
        "align_redo",
    ]
    assert "切句兜底" in report
    assert "R3　总是 蛇的长度 大于 0" in report and "**比对通过**" in report
