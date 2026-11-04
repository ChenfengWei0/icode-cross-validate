"""Broken versions of seed/lru.py for scripts/live_eval.py: the sealed evaluation.

The bugs in bugs.json were written by an agent that saw only goal.txt and seed/lru.py, not the checks,
the rules, the prompts or any earlier result. No bug was dropped or edited after that.

Sealed: this example measures the plugin as it is. Its results must not be used to change the prompts,
the rules or the checker; otherwise it measures them against an answer key, like the stack did. After a
change made for other reasons it may be run again, and the report says which commit it measured.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = "lru.py"
MODE = "积累"  # no main agent, so the code stays the seed and the bugs apply to every round
BUGS = json.loads((HERE / "bugs.json").read_text(encoding="utf-8"))
SEED = (HERE / "seed" / TARGET).read_text(encoding="utf-8")


def prepare(code: str) -> dict[str, str] | None:
    """The edits fit the seed only."""
    if code.strip() != SEED.strip():
        return None
    return {bug["name"]: SEED.replace(bug["old"], bug["new"]) for bug in BUGS}
