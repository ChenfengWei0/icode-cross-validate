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
    assert EV.ATTRIBUTE.search(line).group(1) == name


def results(checks, **props):
    return {"checks": checks, "propositions": {k: {"result": v} for k, v in props.items()}}


def test_how_a_bug_was_found():
    trace = {"kind": "trace", "result": "确定违反"}
    crash = {"kind": "trace", "result": "失败（检查）"}
    command = {"kind": "command", "result": "失败（检查）"}
    passed = {"kind": "trace", "result": "推定为真"}
    assert EV.how_caught(results([trace, crash], R1="确定违反")) == "公式"
    assert EV.how_caught(results([command, crash], R1="失败（检查）")) == "脚本"
    assert EV.how_caught(results([crash], R1="失败（检查）")) == "崩溃"
    assert EV.how_caught(results([passed], R1="推定为真")) == "漏掉"
    assert EV.how_caught(results([passed], R1="失败（对齐）")) == "对齐失败"


def test_summary():
    clean = results([], R1="通过")
    bad = results([{"kind": "trace", "result": "确定违反"}], R1="确定违反")
    report = {"conclusion": "比对通过", "conclusion_reason": "x", "stats": {}}
    one = EV.summarize_round(report, {"correct": clean, "a": bad, "b": clean, "c": None}, "_items")
    assert one["control_clean"] is True and one["mutants"] == {"a": "公式", "b": "漏掉", "c": "没有结果"}
    assert EV.summarize_round(report, {"correct": bad}, "_items")["control_clean"] is False
    text = EV.summarize([one, EV.summarize_round(None, {}, None)])
    assert "1/3" in text and "平均抓到率：33%（1 轮）" in text and "没有结论" in text and "b 1 次" in text
