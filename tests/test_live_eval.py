"""The evaluation script: its mutants really are broken, and its verdicts say how a bug was found."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "stack"))
import mutants as MUT  # noqa: E402

spec = importlib.util.spec_from_file_location("live_eval", ROOT / "scripts" / "live_eval.py")
EV = importlib.util.module_from_spec(spec)
spec.loader.exec_module(EV)


def stack_class(source):
    space = {}
    exec(compile(source, "stack.py", "exec"), space)  # noqa: S102 — our own fixed test sources
    return space["Stack"]


def behaves(cls) -> bool:
    """The requirement, with the depth and duplicates the shallow runs missed."""
    s = cls()
    if not hasattr(s, "size") or not hasattr(s, "pop") or s.size() != 0:
        return False
    for x in [*range(40), 7, 7]:
        s.push(x)
    if s.size() != 42:
        return False
    for x in [7, 7, *reversed(range(40))]:
        if s.pop() != x:
            return False
    if s.size() != 0:
        return False
    try:
        s.pop()
    except IndexError:
        return True
    except Exception:
        return False
    return False


def test_reference_is_right_and_every_mutant_is_wrong():
    assert behaves(stack_class(MUT.REFERENCE.replace("{a}", "_items")))
    for name in MUT.NAMES:
        for attribute in ("_items", "data"):
            source = MUT.source(name, attribute)
            assert "{a}" not in source and f"self.{attribute}" in source
            assert not behaves(stack_class(source)), name


@pytest.mark.parametrize(
    ("line", "name"),
    [("self._items = []", "_items"), ("self.data: list = []", "data"), ("self.xs = list()", "xs")],
)
def test_list_attribute_is_found(line, name):
    assert MUT.attribute_of(line) == name


def test_stack_mutants_need_a_list_attribute():
    assert set(MUT.prepare("self._items = []")) == set(MUT.NAMES)
    assert MUT.prepare("self.head = None") is None


def results(checks, **props):
    return {"checks": checks, "propositions": {k: {"result": v} for k, v in props.items()}}


def test_how_a_bug_was_found():
    trace = {"proposition": "R1", "kind": "trace", "result": "确定违反"}
    crash = {"proposition": "R1", "kind": "trace", "result": "失败（检查）"}
    command = {"proposition": "R1", "kind": "command", "result": "失败（检查）"}
    passed = {"proposition": "R1", "kind": "trace", "result": "推定为真"}
    assert EV.how_caught(results([trace, crash], R1="确定违反")) == "公式"
    assert EV.how_caught(results([command, crash], R1="失败（检查）")) == "脚本"
    assert EV.how_caught(results([crash], R1="失败（检查）")) == "崩溃"
    assert EV.how_caught(results([passed], R1="推定为真")) == "漏掉"
    assert EV.how_caught(results([passed], R1="失败（对齐）")) == "对齐失败"


def test_summary():
    clean = results([], R1="通过")
    bad = results([{"proposition": "R1", "kind": "trace", "result": "确定违反"}], R1="确定违反")
    report = {"conclusion": "比对通过", "conclusion_reason": "x", "stats": {}}
    one = EV.summarize_round(report, {"correct": clean, "a": bad, "b": clean, "c": None})
    assert one["control_clean"] is True and one["mutants"] == {"a": "公式", "b": "漏掉", "c": "没有结果"}
    assert EV.summarize_round(report, {"correct": bad})["control_clean"] is False
    text = EV.summarize([one, EV.summarize_round(None, {}, "不重放的原因")])
    assert "不重放的原因" in text
    assert "1/3" in text and "平均抓到率：33%（1 轮）" in text and "没有结论" in text and "b 1 次" in text


def test_a_failure_the_correct_code_also_has_does_not_count():
    control = results([], R1="确定违反", R2="推定为真")
    same = results([{"proposition": "R1", "kind": "trace", "result": "确定违反"}], R1="确定违反", R2="推定为真")
    assert EV.how_caught(same, control) == "漏掉"
    new = {"proposition": "R2", "kind": "command", "result": "失败（检查）"}
    both = results(
        [{"proposition": "R1", "kind": "trace", "result": "确定违反"}, new], R1="确定违反", R2="失败（检查）"
    )
    assert EV.how_caught(both, control) == "脚本"


def load_snake():
    return EV.load_mutants(ROOT / "examples" / "snake_logic")


def test_snake_bugs_apply_and_stay_importable():
    snake = load_snake()
    assert len(snake.BUGS) == 20
    assert len({b["name"] for b in snake.BUGS}) == 20
    made = snake.prepare(snake.SEED)
    for bug in snake.BUGS:
        assert snake.SEED.count(bug["old"]) == 1, bug["name"]
        assert made[bug["name"]] != snake.SEED
        compile(made[bug["name"]], bug["name"], "exec")


def test_snake_round_is_not_measured_when_the_code_changed():
    snake = load_snake()
    assert snake.MODE == "积累" and not hasattr(MUT, "MODE")
    assert snake.prepare(snake.SEED + "\n") is not None
    assert snake.prepare(snake.SEED + "# changed\n") is None
