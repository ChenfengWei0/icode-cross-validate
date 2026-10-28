"""The whole workflow through a real `icode workflow run` (needs an iCode checkout: ICODE_SRC)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).with_name("e2e")))


@pytest.mark.skipif(not os.environ.get("ICODE_SRC"), reason="set ICODE_SRC to an iCode checkout to run")
def test_block_then_pass_through_icode():
    from run_icode_e2e import run

    outcome = run(Path(os.environ["ICODE_SRC"]))
    assert outcome["ok"], outcome
