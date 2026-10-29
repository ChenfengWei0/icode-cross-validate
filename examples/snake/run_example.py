"""Run the paper's snake example (第 3 章) through every deterministic step of the workflow.

The two agents are replaced by their recorded answers: `split.json` and `subtask.txt` stand for the
subtask agent, `align/` for the alignment agent. Everything else is the plugin's own code: sentence
-> formula, the proposition checks, the checker running the traces, and the report.

    python3 examples/snake/run_example.py correct|buggy
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "cross-validate"))

import cv_flow as FLOW  # noqa: E402
import cv_state as S  # noqa: E402

GOAL = "实现贪吃蛇。"


def run(variant: str, home: Path) -> Path:
    """Goal mode: the main agent's code is `variant`; returns the run directory."""
    os.environ["CV_CHRYS_HOME"] = str(home)
    installed = S.global_workflow_dir()
    if not installed.exists():
        shutil.copytree(ROOT / "cross-validate", installed, ignore=shutil.ignore_patterns("__pycache__"))
    w = home / "work" / variant
    shutil.copytree(HERE / variant, w, ignore=shutil.ignore_patterns("__pycache__"))
    config = FLOW.load_config()
    state = FLOW.intake(GOAL, str(w), installed)
    state = FLOW.after_implement(state)  # the main agent has finished: goal reached
    state, _ = FLOW.split_check(state, (HERE / "split.json").read_text(encoding="utf-8"), 1)
    state, revise = FLOW.propose_check(state, (HERE / "subtask.txt").read_text(encoding="utf-8"), 1)
    if revise is not None:
        raise SystemExit(revise)
    FLOW.align_prompt(state)
    run_dir = Path(state["run"])
    for f in (HERE / "align").iterdir():
        shutil.copy2(f, run_dir / "align" / f.name)
    state, _ = FLOW.align_check(state, "已完成", 1, config)
    state = FLOW.run_checks(state, config)
    FLOW.report(state, config)
    return run_dir


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "correct"
    with tempfile.TemporaryDirectory() as tmp:
        print((run(variant, Path(tmp)) / "report.md").read_text(encoding="utf-8"))
