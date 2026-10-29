import json
import sys
from pathlib import Path

import cv_align as A
import cv_grammar as G
import cv_monitor as M
import cv_run as RUN

LIMITS = {"max_runs": 200, "check_timeout": 120, "max_states": 10000, "budget": 3600}
PY = sys.executable


def props(decls):
    out = {}
    for pid, s in {
        "R1": "存在一个 x，使 x 是 蛇",
        "R5": "只要 蛇 撞到自身，下一步 游戏 结束",
        "R9": "代码中存在表示 蛇的长度 的元素",
    }.items():
        tree, pattern = G.parse_sentence(s, decls)
        out[pid] = {"id": pid, "tree": tree, "pattern": pattern, "class": __import__("cv_formula").classify(tree)}
    return out


def test_validate_format(decls):
    """细则 11.2 11.7 format errors go back to the agent; values outside the limits are format errors."""
    p = props(decls)
    data = {
        "bindings": {"蛇": "Snake"},
        "checks": [
            {"proposition": "R9", "kind": "trace", "command": "x", "runs": 1},
            {"proposition": "R5", "kind": "trace", "command": "x", "runs": 500},
            {"proposition": "R5", "kind": "shell", "command": "x"},
            {"proposition": "R7", "kind": "command", "command": "x"},
            {"proposition": "R1", "kind": "command", "command": "x", "timeout": 999},
        ],
    }
    out = A.validate(data, p, LIMITS)
    joined = "\n".join(out["format_errors"])
    for expect in ("结构命题 R9 不能用 trace", "runs 应是", "kind 只能是", "不是交给你的命题编号", "timeout 应是"):
        assert expect in joined
    assert A.validate([], p, LIMITS)["format_errors"] == ["checks.json 应是一个对象"]


def test_coverage(decls):
    """细则 11.5 11.6 every proposition needs a check, every temporal one a trace check."""
    p = props(decls)
    data = {"bindings": {}, "checks": [{"proposition": "R5", "kind": "command", "command": "true"}]}
    out = A.validate(data, p, LIMITS)
    assert out["format_errors"] == []
    assert out["align_failures"] == {
        "R1": "没有任何检查",
        "R5": "时序命题缺少 trace 检查（细则 11.5）",
        "R9": "没有任何检查",
    }


def write(path: Path, code: str) -> str:
    path.write_text(code, encoding="utf-8")
    return f'{PY} "{path}"'


def frozen_for(checks):
    return {
        "bindings": {},
        "checks": [{"index": i, "form": "f", "runs": 1, "timeout": 30, **c} for i, c in enumerate(checks)],
        "align_failures": {},
    }


def test_command_checks(decls, tmp_path):
    """细则 11.3 exit 0 passes, 1 fails with stdout as counterexample, others are alignment failures."""
    run, w = tmp_path / "run", tmp_path / "w"
    (run / "align").mkdir(parents=True)
    w.mkdir()
    p = {"R9": props(decls)["R9"]}
    for code, want in ((0, M.PASSED), (1, M.CHECK_FAILED), (3, M.ALIGN_FAILED)):
        cmd = write(tmp_path / f"c{code}.py", f"import os,sys; print('cex', os.environ['CV_W']); sys.exit({code})")
        out = RUN.run_checks(
            run, w, p, frozen_for([{"proposition": "R9", "kind": "command", "command": cmd}]), set(), LIMITS
        )
        assert out["propositions"]["R9"]["result"] == want
    assert "cex" in out["checks"][0]["runs_detail"][0]["stdout"]
    slow = write(tmp_path / "slow.py", "import time; time.sleep(5)")
    out = RUN.run_checks(
        run,
        w,
        p,
        frozen_for([{"proposition": "R9", "kind": "command", "command": slow, "timeout": 0.5}]),
        set(),
        LIMITS,
    )
    assert out["propositions"]["R9"]["result"] == M.ALIGN_FAILED


TRACER = """
import json, os, sys
i = int(os.environ["CV_RUN"])
states = [{"值": {"撞到自身(蛇)": i == 1, "结束(游戏)": False}, "动作": "右"}, {"值": {"撞到自身(蛇)": False, "结束(游戏)": %s}, "动作": None}]
with open(os.environ["CV_TRACE"], "w") as f:
    for s in states:
        f.write(json.dumps(s, ensure_ascii=False) + "\\n")
sys.exit(%s)
"""


def test_trace_checks(decls, tmp_path):
    """细则 11.4 13.3 13.4 13.6 runs with CV_RUN/CV_TRACE, worst result, counterexample, records."""
    run, w = tmp_path / "run", tmp_path / "w"
    (run / "align").mkdir(parents=True)
    w.mkdir()
    p = {"R5": props(decls)["R5"]}
    cmd = write(tmp_path / "t.py", TRACER % ("False", 0))
    frozen = frozen_for([{"proposition": "R5", "kind": "trace", "command": cmd, "runs": 3}])
    out = RUN.run_checks(run, w, p, frozen, set(), LIMITS)
    rec = out["checks"][0]
    assert [d["result"] for d in rec["runs_detail"]] == [M.PRESUMED_TRUE, M.VIOLATED, M.PRESUMED_TRUE]
    assert out["propositions"]["R5"]["result"] == M.VIOLATED
    cex = out["propositions"]["R5"]["counterexample"]
    assert cex["run"] == 1 and cex["state"] == 1 and cex["actions"] == ["右", None] and len(cex["last_states"]) == 2
    assert {"exit", "seconds", "trace", "trace_sha256", "result"} <= set(rec["runs_detail"][0])


def test_nonzero_exit_fails(decls, tmp_path):
    """细则 12.6 a crash of the program or of the script is a failed check."""
    run, w = tmp_path / "run", tmp_path / "w"
    (run / "align").mkdir(parents=True)
    w.mkdir()
    cmd = write(tmp_path / "t.py", TRACER % ("True", 2))
    out = RUN.run_checks(
        run,
        w,
        {"R5": props(decls)["R5"]},
        frozen_for([{"proposition": "R5", "kind": "trace", "command": cmd}]),
        set(),
        LIMITS,
    )
    assert out["propositions"]["R5"]["result"] == M.CHECK_FAILED


def test_budget(decls, tmp_path):
    """细则 11.8 checks left when the budget is spent are alignment failures."""
    run, w = tmp_path / "run", tmp_path / "w"
    (run / "align").mkdir(parents=True)
    w.mkdir()
    out = RUN.run_checks(
        run,
        w,
        {"R9": props(decls)["R9"]},
        frozen_for([{"proposition": "R9", "kind": "command", "command": "true"}]),
        set(),
        {**LIMITS, "budget": 0},
    )
    assert out["propositions"]["R9"] == {"result": M.ALIGN_FAILED, "reason": "预算用尽，未运行", "counterexample": None}


def test_truncation(tmp_path):
    """细则 11.8 traces longer than the state limit are cut and marked."""
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps({"值": {}}) for _ in range(5)), encoding="utf-8")
    states, truncated = RUN.read_trace(path, 3)
    assert len(states) == 3 and truncated


def test_trace_format(tmp_path):
    """细则 12.1 a trace is JSON Lines, one object per state; anything else is an alignment failure."""
    path = tmp_path / "t.jsonl"
    path.write_text('{"值": {"a": 1}, "动作": "右"}\n\n{"值": {"a": 2}}\n', encoding="utf-8")
    states, _ = RUN.read_trace(path, 100)
    assert [s["值"]["a"] for s in states] == [1, 2]
    for bad in ("{not json}\n", "[1, 2]\n"):
        path.write_text(bad, encoding="utf-8")
        try:
            RUN.read_trace(path, 100)
        except M.AlignError:
            continue
        raise AssertionError(bad)
