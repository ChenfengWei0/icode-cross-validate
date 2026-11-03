"""Broken versions of seed/snake.c for scripts/live_eval.py (README 细则 11.11 evaluation).

The bugs in bugs.json were written by an agent that saw only the requirement and the seed, not the
checks, the rules or earlier results, so the score is not fitted to what the checks already catch.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = "snake.c"
MODE = "积累"  # no main agent, so the code stays the seed and the bugs apply to every round
BUGS = json.loads((HERE / "bugs.json").read_text(encoding="utf-8"))
SEED = (HERE / "seed" / TARGET).read_text(encoding="utf-8")


def prepare(code: str) -> dict[str, str] | None:
    """The edits fit the seed only."""
    if code.strip() != SEED.strip():
        return None
    return {bug["name"]: SEED.replace(bug["old"], bug["new"]) for bug in BUGS}
