"""The ClassEval benchmark script: sampling, grading, statistics and the offline box (BENCHMARK.md)."""

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bench_classeval", ROOT / "scripts" / "bench_classeval.py")
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

TASK = {
    "task_id": "ClassEval_X",
    "class_name": "Counter",
    "import_statement": [],
    "skeleton": "'''\nclass Counter:\n    def inc(self):\n        \"\"\"add one\"\"\"\n'''",
    "test_classes": ["CounterTestInc", "CounterTestValue"],
    "test": (
        "import unittest\n\n"
        "class CounterTestInc(unittest.TestCase):\n"
        "    def test_inc(self):\n        c = Counter(); c.inc(); self.assertEqual(c.n, 1)\n\n"
        "class CounterTestValue(unittest.TestCase):\n"
        "    def test_value(self):\n        self.assertEqual(Counter().n, 0)\n"
    ),
}
RIGHT = "class Counter:\n    def __init__(self):\n        self.n = 0\n    def inc(self):\n        self.n += 1\n"
WRONG = RIGHT.replace("+= 1", "+= 2")


def test_skeleton_loses_only_a_whole_wrapper():
    assert B.skeleton_of(TASK).startswith("class Counter:")
    assert B.skeleton_of({"skeleton": "import os\n'''doc'''"}) == "import os\n'''doc'''"
    goal = B.goal_of(TASK)
    assert "Counter.py" in goal and "class Counter:" in goal
    assert "test" not in goal.lower() and "交叉验证" not in goal and "测试" not in goal


def test_sample_is_fixed_and_independent_of_results():
    ids = [f"ClassEval_{k}" for k in range(100)]
    assert B.sample(ids, 20) == B.sample(ids, 20)
    assert B.sample(ids, 20) == B.sample(ids, 30)[:20]
    assert len(set(B.sample(ids, 20))) == 20


def test_pick_code(tmp_path):
    assert B.pick_code(tmp_path, "Counter") == (None, [])
    (tmp_path / "other.py").write_text("x = 1\n")
    (tmp_path / "impl.py").write_text(RIGHT)
    assert B.pick_code(tmp_path, "Counter") == (RIGHT, [])
    (tmp_path / "impl2.py").write_text(RIGHT)
    assert B.pick_code(tmp_path, "Counter") == (None, [])  # two candidates: no code
    (tmp_path / "Counter.py").write_text("import os\nfrom other import x\n" + WRONG)
    code, local = B.pick_code(tmp_path, "Counter")
    assert code.endswith(WRONG) and local == ["other"]


def test_official_preprocess():
    task = {"import_statement": ["import math"]}
    code = "class A:\n    @staticmethod\n    def f(x):\n        return x\n    def g(self):\n        pass\n"
    assert B.official_preprocess(code, task) == (
        "import math\nclass A:\n    @staticmethod\n    def f(x):\n        return x\n    def g(self):\n        pass\n"
    )
    assert B.test_answer({"errors": 0, "failures": 0, "testsRun": 0}) == "error"
    assert B.test_answer({"errors": 0, "failures": 0, "testsRun": 3}) == "success"
    assert B.test_answer({"errors": 1, "failures": 0, "testsRun": 3}) == "partial_success"


def test_wilson_and_fisher_known_values():
    lo, hi = B.wilson(8, 10)
    assert abs(lo - 0.4902) < 1e-3 and abs(hi - 0.9433) < 1e-3
    assert B.wilson(0, 0) is None
    # scipy.stats.fisher_exact([[8, 2], [3, 7]], alternative="greater") -> 0.03489
    assert abs(B.fisher_greater(8, 2, 3, 7) - 0.034892) < 1e-5
    assert B.fisher_greater(0, 0, 3, 7) is None
    # Newcombe (1998), example (a): 56/70 vs 48/80, method 10 gives 0.0524 to 0.3339
    lo, hi = B.newcombe(56, 70, 48, 80)
    assert abs(lo - 0.0524) < 5e-4 and abs(hi - 0.3339) < 5e-4


def _result(certified, ok, conclusion=None):
    grade = {"class_ok": ok, "classes_passed": 2 if ok else 1, "classes_total": 2}
    return {
        "task_id": "ClassEval_X",
        "class_name": "Counter",
        "certified": certified,
        "grade": grade,
        "conclusion": conclusion,
        "timed_out": False,
        "seconds": 60,
    }


def test_metrics_confusion_and_undefined():
    rs = [_result(True, True, "比对通过"), _result(True, False, "比对通过"), _result(False, True, "无结论")]
    rs.append(_result(False, False, None))
    m = B.metrics(rs)
    assert m["confusion"] == {"TP": 1, "FP": 1, "FN": 1, "TN": 1}
    assert m["pass@1"]["value"] == 0.5 and m["precision"]["value"] == 0.5 and m["coverage"]["value"] == 0.5
    assert m["fun_pass@1"]["value"] == 6 / 8
    assert m["lift"] == 1.0 and m["difference"]["value"] == 0.0
    assert m["false_certification_rate"]["value"] == 0.5
    assert m["by_conclusion"] == {
        "比对通过": {"正确": 1, "错误": 1},
        "无结论": {"正确": 1, "错误": 0},
        "没有报告": {"正确": 0, "错误": 1},
    }
    none = B.metrics([_result(False, True)])
    assert none["precision"]["value"] is None and none["lift"] is None and none["difference"]["value"] is None
    meta = {"sha256": "0" * 64, "commit": "x", "planned": 1, "excluded": {}}
    assert "无定义" in B.summarize([_result(False, True)], meta)


def test_workflow_box_is_offline_with_only_the_model_bridge(tmp_path):
    cmd = B.offline_box(tmp_path, tmp_path, tmp_path, Path("/b"), "icode", "x")
    at = cmd.index("bwrap")
    assert "--unshare-net" in cmd[at:]
    assert cmd[cmd.index("/b") + 1] == B.BRIDGE
    assert "UNIX-CONNECT:/cv-bridge/model.sock" in " ".join(cmd)
    assert B.LIVE.REPO_IN_BOX not in cmd and str(ROOT) not in cmd  # the repository is not in the box
    grading = B.offline_box(tmp_path, tmp_path, tmp_path, None, "python3")
    assert "--unshare-net" in grading and B.BRIDGE not in grading and B.LIVE.REPO_IN_BOX not in grading


@pytest.mark.skipif(not shutil.which("bwrap"), reason="needs bwrap")
def test_grade_in_the_box(tmp_path):
    right = B.grade(RIGHT, TASK, tmp_path)
    assert right["class_ok"] and right["classes_passed"] == 2, right
    wrong = B.grade(WRONG, TASK, tmp_path)
    assert not wrong["class_ok"] and wrong["classes_passed"] == 1
    broken = B.grade("class Counter(:\n", TASK, tmp_path)
    assert not broken["class_ok"] and broken["classes_passed"] == 0
    assert B.grade(None, TASK, tmp_path)["error"] == "没有代码"
    net = "\nimport urllib.request\nurllib.request.urlopen('https://github.com', timeout=3)\n"
    assert not B.grade(RIGHT + net, TASK, tmp_path)["class_ok"]  # no network in the grading box
    slow = RIGHT.replace("self.n += 1", "self.n += 1\n        import time; time.sleep(30)")
    hung = B.grade(slow, TASK, tmp_path)
    assert hung["classes"]["CounterTestInc"]["answer"] == "error"
    assert hung["classes"]["CounterTestValue"]["answer"] == "success"


def test_run_dir_prefers_the_one_with_a_report(tmp_path):
    runs = tmp_path / ".chrys" / "cross-validate" / "projects" / "x-proj" / "runs"
    real, typo = runs / "20261108T231805Z-baea", runs / "20261108T2318305Z-baea"
    (real).mkdir(parents=True)
    (typo / "align").mkdir(parents=True)
    (real / "run.json").write_text("{}")
    assert B.run_dir_of(tmp_path) == real
    (real / "report.json").write_text('{"conclusion": "比对未通过"}')
    assert B.report_of(tmp_path)["conclusion"] == "比对未通过"


def test_proxy_removes_what_acts_outside_the_box():
    body = {
        "model": "m",
        "tools": [{"name": "read_file", "input_schema": {}}, {"type": "web_search_20250305", "name": "web_search"}],
        "mcp_servers": [{"url": "https://x"}],
    }
    assert B.AnthropicProxy.clean(body) == ["mcp_servers", "tool:web_search_20250305"]
    assert body == {"model": "m", "tools": [{"name": "read_file", "input_schema": {}}]}
    only = {"tools": [{"type": "web_fetch_20250910", "name": "web_fetch"}], "tool_choice": {"type": "any"}}
    assert B.AnthropicProxy.clean(only) == ["tool:web_fetch_20250910"] and only == {}
    sse = b'event: message_start\ndata: {"message": {"usage": {"input_tokens": 12, "cache_read_input_tokens": 3}}}\n'
    sse += b'event: message_delta\ndata: {"usage": {"output_tokens": 40}}\n'
    assert B.AnthropicProxy.usage_of(sse) == {"input_tokens": 12, "cache_read_input_tokens": 3, "output_tokens": 40}


@pytest.mark.skipif(not shutil.which("bwrap"), reason="needs bwrap")
def test_in_spec_judges_only_the_methods_the_docstring_determines(tmp_path):
    wrong = B.grade(WRONG, TASK, tmp_path)
    assert wrong["classes"]["CounterTestInc"]["failed_tests"] == ["test_inc"]
    assert wrong["classes"]["CounterTestValue"]["tests"] == ["test_value"]
    labels = {"CounterTestInc.test_inc": "未规定", "CounterTestValue.test_value": "规定"}
    assert B.in_spec(wrong, labels) is True  # what fails was not in the docstring
    assert B.in_spec(wrong, {**labels, "CounterTestInc.test_inc": "规定"}) is False
    assert B.in_spec(wrong, {"CounterTestInc.test_inc": "矛盾"}) is None  # nothing to judge
    assert B.in_spec(B.grade(None, TASK, tmp_path), labels) is False  # no code: every method fails
    assert B.in_spec(wrong, None) is None


def test_formal_sampling_skips_development_tasks():
    ids = [f"ClassEval_{k}" for k in range(100)]
    formal = B.sample([i for i in ids if i not in B.DEV_TASKS], 30)
    assert not set(formal) & set(B.DEV_TASKS)
