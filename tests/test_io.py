import re
import shutil
from pathlib import Path

import pytest

import cv_io as IO

pytestmark = pytest.mark.skipif(shutil.which("bwrap") is None, reason="io mode runs code in bwrap")

PROMPT = (Path(__file__).resolve().parents[1] / "cross-validate" / "prompts" / "io_spec.md").read_text(encoding="utf-8")
REQ = re.search(r'需求："(.*?)"', PROMPT).group(1)
GOOD = re.search(r"```python\n(.*?)```", PROMPT.split("# 一个例子")[1], re.DOTALL).group(1)
EXAMPLES = [("2\n", "-1\n"), ("5\n", "1 3\n")]
FAST = IO.Settings(selftest_inputs=20, run_inputs=30, run_timeout=2.0)

RIGHT = """n = int(input())
print(-1 if n == 2 else "1 3")
"""
WRONG = """n = int(input())
print(-1 if n == 2 else "1 2")
"""


def frozen(spec=GOOD):
    s, log = IO.write_spec(lambda p: f"```python\n{spec}```", REQ, EXAMPLES, FAST)
    assert s is not None, log[-1]["problems"]
    return s


def test_prompt_example_passes_selftest():
    """20.2 20.3 the worked example in the subtask prompt passes its own self-test."""
    assert IO.selftest(GOOD, REQ, EXAMPLES, FAST)["problems"] == []


def test_selftest_catches_wrong_specs():
    """20.3 17.13 rejected example output, accepted empty output, ungrounded quote, invalid generated input."""
    strict = GOOD.replace("if (a + b) % 2:", "if (a + b) % 2 or a < b:")
    assert any("示例 2 给出的输出被 check 判为违反" in p for p in IO.selftest(strict, REQ, EXAMPLES, FAST)["problems"])
    lax = GOOD.replace(
        "def check(input_text, output_text):",
        "def check(input_text, output_text):\n    return []\n\ndef _unused(input_text, output_text):",
    )
    assert any("配空输出" in p for p in IO.selftest(lax, REQ, EXAMPLES, FAST)["problems"])
    ungrounded = GOOD.replace('"quote": "a + b 是偶数"', '"quote": "a 与 b 互质"')
    assert any("不是需求中的原文" in p for p in IO.selftest(ungrounded, REQ, EXAMPLES, FAST)["problems"])
    badgen = GOOD.replace('return f"{n}\\n"', 'return f"{n + 200}\\n"')
    assert any("被 valid_input 判为不合法" in p for p in IO.selftest(badgen, REQ, EXAMPLES, FAST)["problems"])


def test_fix_round_sees_only_its_own_problems():
    """20.3 20.1 a failing spec is sent back with its problems and frozen once fixed; no program is shown."""
    prompts = []
    bad = GOOD.replace("if (a + b) % 2:", "if (a + b) % 2 or a < b:")
    answers = iter([f"```python\n{bad}```", f"```python\n{GOOD}```"])

    def ask(p):
        prompts.append(p)
        return next(answers)

    spec, log = IO.write_spec(ask, REQ, EXAMPLES, FAST)
    assert spec is not None and len(log) == 2 and log[0]["problems"] and not log[1]["problems"]
    assert "示例 2 给出的输出被 check 判为违反" in prompts[1] and RIGHT not in prompts[1]


def test_unfixable_spec_gives_no_spec():
    """20.3 20.7 still failing after fix_rounds: no spec, so no conclusion."""
    bad = GOOD.replace("if (a + b) % 2:", "if (a + b) % 2 or a < b:")
    spec, log = IO.write_spec(lambda p: f"```python\n{bad}```", REQ, EXAMPLES, FAST)
    assert spec is None and len(log) == FAST.fix_rounds + 1


def test_frozen_inputs_are_fixed():
    """20.4 the same spec freezes the same inputs, examples first."""
    a, b = frozen(), frozen()
    assert a.inputs == b.inputs and a.inputs[:2] == [i for i, _ in EXAMPLES] and len(set(a.inputs)) == len(a.inputs)


def test_cross_run_kinds():
    """20.5 pass, crash, timeout and violation."""
    spec = frozen()
    assert {r["kind"] for r in IO.cross_run(spec, RIGHT, FAST)} == {"pass"}
    assert {r["kind"] for r in IO.cross_run(spec, WRONG, FAST)} >= {"violation"}
    assert {r["kind"] for r in IO.cross_run(spec, "raise SystemExit(3)\n", FAST)} == {"crash"}
    slow = IO.Settings(run_inputs=2, run_timeout=0.5, selftest_inputs=20)
    s2, _ = IO.write_spec(lambda p: f"```python\n{GOOD}```", REQ, EXAMPLES, slow)
    assert {r["kind"] for r in IO.cross_run(s2, "while True: pass\n", slow)} == {"timeout"}


def test_violation_needs_judges_and_unanimous_dismissal():
    """20.6 20.7 a violation stands unless every judge says 不违反; judges never see the program."""
    spec = frozen()
    res = IO.cross_run(spec, WRONG, FAST)
    seen = []

    def judge(answer):
        def ask(p):
            seen.append(p)
            return answer

        return ask

    c = IO.conclude(judge("违反\n4 是偶数"), REQ, spec, res, FAST)
    assert (
        c["conclusion"] == IO.FAIL
        and c["judged"][0]["condition"] == "C4"
        and len(c["judged"][0]["votes"]) == FAST.judges
    )
    assert all(WRONG not in p and "def check" not in p for p in seen)
    mixed = iter(["不违反", "违反"] * 5)
    assert IO.conclude(lambda p: next(mixed), REQ, spec, res, FAST)["conclusion"] == IO.FAIL
    c = IO.conclude(judge("不违反\n看错了"), REQ, spec, res, FAST)
    assert c["dismissed"] == ["C4"] and c["conclusion"] == IO.PASS


def test_too_many_dismissed_is_no_conclusion():
    """20.7 more than half of the conditions dismissed: no conclusion."""
    spec = frozen()
    res = IO.cross_run(spec, "print(7, 7)\n", FAST)
    c = IO.conclude(lambda p: "不违反", REQ, spec, res, FAST, dismissed={"C1", "C2"})
    assert c["conclusion"] == IO.NONE and len(c["dismissed"]) * 2 > len(spec.conditions)
    assert IO.conclude(lambda p: "不违反", REQ, spec, res, FAST, dismissed={"C1"})["conclusion"] == IO.PASS


def test_crash_fails_without_judges():
    """20.6 20.7 crashes and timeouts are not judged."""
    spec = frozen()
    c = IO.conclude(lambda p: pytest.fail("judged a crash"), REQ, spec, IO.cross_run(spec, "1/0\n", FAST), FAST)
    assert c["conclusion"] == IO.FAIL and c["judged"] == [] and c["counterexample"]["kind"] == "crash"


def test_repair_sees_one_counterexample_only():
    """20.8 17.14 the main task gets one input, its output and one condition, never the check module."""
    spec = frozen()
    prompts = []

    def fixer(p):
        prompts.append(p)
        return f"```python\n{RIGHT}```"

    rounds = IO.validate(WRONG, spec, REQ, FAST, lambda p: "违反", fixer)
    assert [r["conclusion"] for r in rounds] == [IO.FAIL, IO.PASS] and rounds[1]["program"] == RIGHT
    assert (
        len(prompts) == 1
        and "def check" not in prompts[0]
        and "a + b 是偶数" in prompts[0]
        and WRONG.strip() in prompts[0]
    )


def test_box_isolation(tmp_path):
    """20.9 programs in the box see no home directory and no network."""
    secret = Path.home() / ".cv-io-test-secret"
    spec = frozen()
    probe = f"""import socket, os
try:
    open({str(secret)!r}).read(); print("home")
except OSError:
    pass
try:
    socket.create_connection(("1.1.1.1", 80), timeout=1); print("net")
except OSError:
    pass
n = int(input()); print(-1 if n == 2 else "1 3")
"""
    secret.write_text("x")
    try:
        assert {r["kind"] for r in IO.cross_run(spec, probe, FAST)} == {"pass"}
    finally:
        secret.unlink()


def test_settings_from_config():
    """20.10 config.json["io"] overrides the defaults, unknown keys are ignored."""
    s = IO.Settings.load({"io": {"judges": 3, "nonsense": 1}})
    assert s.judges == 3 and s.fix_rounds == IO.Settings().fix_rounds


def test_prompts_have_no_evaluated_words():
    """20.1 the io prompts take their example from no evaluated problem and show no program to the judge."""
    judge = (IO.PROMPTS / "io_judge.md").read_text(encoding="utf-8")
    assert "{{CODE}}" not in judge and "{{SPEC}}" not in judge
    repair = (IO.PROMPTS / "io_repair.md").read_text(encoding="utf-8")
    assert "{{SPEC}}" not in repair
