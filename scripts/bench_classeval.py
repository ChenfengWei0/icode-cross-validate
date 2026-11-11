#!/usr/bin/env python3
"""ClassEval: does code with a certificate pass the benchmark's hidden tests more often? (BENCHMARK.md)

    python3 scripts/bench_classeval.py --model-yaml model.yaml --out bench-out --n 20

Each task runs the cross-validate workflow in goal mode inside an offline bwrap box: the only way out is
a Unix socket to the local model's port. The hidden tests and the reference solution stay in the cache
outside every workflow box; the generated code is graded afterwards in a second offline box, the way
ClassEval's own test_pipeline.py grades it. A task with a result file is not run again.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("live_eval", ROOT / "scripts" / "live_eval.py")
LIVE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LIVE)

DATASET = "FudanSELab/ClassEval"
ROWS_URL = "https://datasets-server.huggingface.co/rows?dataset={}&config=default&split=test&offset={}&length=100"
SEED = 20261109  # B5, fixed before any result
TASK_SECONDS = 10800  # B3.2
CLASS_SECONDS = 5  # B4: ClassEval's func_set_timeout(5) per test class
REFERENCE_RUNS = 3  # B5.1
MAX_ATTEMPTS = 2  # B6: one rerun, only after an infrastructure failure
ALPHA = 0.05
MODEL_PORT = 8080
BRIDGE = "/cv-bridge"
PLUGIN_PATHS = ["cross-validate", "agents", "hooks", "cv_cli.py", "cv"]
# B5.1: tasks that overlap what the plugin was developed or prompted with.
DEV_OVERLAP = {
    "ClassEval_77": "类 Snake：插件是对着贪吃蛇例子开发的",
    "ClassEval_8": "类 BankAccount：提示里的例子用了账户",
}
# B5.3: tasks whose results were seen before the freeze; a formal run never samples them.
DEV_TASKS = (
    "ClassEval_81",
    "ClassEval_60",
    "ClassEval_71",
    "ClassEval_40",
    "ClassEval_92",
    "ClassEval_35",
    "ClassEval_98",
)
LABELS = ("规定", "未规定", "矛盾")  # B4.6
# B4.6: a four-way labelling (prompt PROMPT4.md) splits 规定 into written and inferable, 未规定 is 随意约定.
LABELS4 = {"写明": "规定", "可推出": "规定", "随意约定": "未规定", "矛盾": "矛盾"}
INSTRUCTION = (
    "在当前目录的 `{name}.py` 中实现下面这个 Python 类。保持类名、方法名和参数不变，按每个方法的说明和例子"
    "实现，只用 Python 标准库和 skeleton 里已经 import 的库。\n\n```python\n{skeleton}\n```\n"
)
CONTAMINATION = "ClassEval 于 2023 年公开，模型的训练数据可能包含它，这一点无法排除（B6.5）。"


# ---------------------------------------------------------------- data


def load_dataset(cache: Path) -> tuple[list[dict], str]:
    """The 100 tasks, downloaded once into the cache; the sha256 of the saved file is recorded."""
    path = cache / "classeval.json"
    if not path.exists():
        rows: list[dict] = []
        while True:
            with urllib.request.urlopen(ROWS_URL.format(DATASET, len(rows)), timeout=60) as r:  # noqa: S310
                page = json.loads(r.read().decode("utf-8"))
            rows += [item["row"] for item in page["rows"]]
            if not page["rows"] or len(rows) >= page["num_rows_total"]:
                break
        cache.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    data = path.read_bytes()
    return json.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest()


def skeleton_of(task: dict) -> str:
    """B3.1: after strip(), a skeleton that both starts and ends with ''' loses that one wrapper."""
    text = task["skeleton"].strip()
    if text.startswith("'''") and text.endswith("'''") and len(text) >= 6:
        text = text[3:-3].strip()
    return text


def goal_of(task: dict) -> str:
    return INSTRUCTION.format(name=task["class_name"], skeleton=skeleton_of(task))


def sample(eligible_ids: list[str], n: int) -> list[str]:
    """B5.2: shuffle the eligible tasks, in dataset order, with the fixed seed; take the first n."""
    order = list(eligible_ids)
    random.Random(SEED).shuffle(order)  # noqa: S311 — reproducible sampling, not security
    return order[:n]


# ---------------------------------------------------------------- boxes


def offline_box(cwd: Path, bound: Path, home: Path, bridge: Path | None, *command: str) -> list[str]:
    """live_eval's box without the network and without the repository.

    With `bridge`, 127.0.0.1:MODEL_PORT inside is forwarded to the model, and nothing else leaves the box.
    """
    inner = list(command)
    if bridge is not None:
        listen = f"TCP-LISTEN:{MODEL_PORT},bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:{BRIDGE}/model.sock"
        inner = ["sh", "-c", f'socat {listen} & sleep 0.5; exec "$@"', "box", *inner]
    base = LIVE.box(cwd, bound, home, *inner)
    at = base.index(LIVE.REPO_IN_BOX)  # drop "--ro-bind <repo> /cv-repo": `cv install` already copied the plugin
    if base[at - 2 : at] != ["--ro-bind", str(LIVE.ROOT)]:
        raise RuntimeError("live_eval.box 的仓库挂载变了")
    base = base[: at - 2] + base[at + 1 :]
    at = base.index("bwrap") + 1
    extra = ["--unshare-net"]
    if bridge is not None:
        extra += ["--bind", str(bridge), BRIDGE]
    return base[:at] + extra + base[at:]


class ModelBridge:
    """Outside the boxes: a Unix socket that forwards to the model's port, and nothing else."""

    def __init__(self, target_port: int = MODEL_PORT) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cvb-"))  # a socket path must stay under 108 bytes
        self.proc: subprocess.Popen | None = None
        self.target_port = target_port

    def __enter__(self) -> Path:
        sock = self.dir / "model.sock"
        self.proc = subprocess.Popen(
            ["socat", f"UNIX-LISTEN:{sock},fork", f"TCP:127.0.0.1:{self.target_port}"], stderr=subprocess.DEVNULL
        )
        for _ in range(50):
            if sock.exists():
                break
            time.sleep(0.1)
        return self.dir

    def __exit__(self, *exc) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        shutil.rmtree(self.dir, ignore_errors=True)


class AnthropicProxy:
    """Outside the boxes: the only way to the Anthropic API (B6.2).

    The key stays here and never enters a box. Only POST /v1/messages (and count_tokens) passes; any
    server-run tool (web search, web fetch, code execution, ...) and any MCP server or container in a
    request is removed first, and the removal is logged. Token usage is logged per request.
    """

    UPSTREAM = "api.anthropic.com"
    PATHS = ("/v1/messages", "/v1/messages/count_tokens")

    def __init__(self, key_file: Path, log: Path) -> None:
        self.key = key_file.read_text(encoding="utf-8").strip()
        self.log = log
        self.server = None
        self.task: str | None = None

    def _write(self, record: dict) -> None:
        record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "task_id": self.task, **record}
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def clean(body: dict) -> list[str]:
        """Remove what would let the provider act outside the box; return what was removed."""
        removed = [key for key in ("mcp_servers", "container") if body.pop(key, None) is not None]
        kept = []
        for tool in body.get("tools") or []:
            kind = tool.get("type") if isinstance(tool, dict) else None
            if kind in (None, "custom"):
                kept.append(tool)
            else:
                removed.append(f"tool:{kind}")
        if "tools" in body:
            if kept:
                body["tools"] = kept
            else:
                body.pop("tools")
                body.pop("tool_choice", None)
        return removed

    @staticmethod
    def usage_of(raw: bytes) -> dict:
        text = raw.decode("utf-8", errors="replace")
        found: dict = {}
        for m in re.finditer(r'"usage"\s*:\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})', text):
            try:
                usage = json.loads(m.group(1))
            except ValueError:
                continue
            for k, v in usage.items():
                if isinstance(v, int):
                    found[k] = max(found.get(k, 0), v)
        return found

    def __enter__(self) -> int:
        import http.client
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def _deny(self, code: int, text: str) -> None:
                data = text.encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = True

            def do_GET(self) -> None:
                if self.path == "/health":
                    self._deny(200, '{"status": "ok"}')
                else:
                    self._deny(403, "only POST /v1/messages")

            def do_POST(self) -> None:
                path = self.path.split("?")[0]
                if path not in AnthropicProxy.PATHS:
                    proxy._write({"denied": self.path})
                    self._deny(403, "only POST /v1/messages")
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                removed = AnthropicProxy.clean(body)
                data = json.dumps(body).encode()
                headers = {"content-type": "application/json", "x-api-key": proxy.key}
                for name in ("anthropic-version", "anthropic-beta", "accept"):
                    if self.headers.get(name):
                        headers[name] = self.headers[name]
                conn = http.client.HTTPSConnection(AnthropicProxy.UPSTREAM, timeout=900)
                try:
                    conn.request("POST", self.path, body=data, headers=headers)
                    resp = conn.getresponse()
                    self.send_response(resp.status)
                    for name, value in resp.getheaders():
                        if name.lower() not in (
                            "transfer-encoding",
                            "content-length",
                            "connection",
                            "content-encoding",
                        ):
                            self.send_header(name, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    seen = bytearray()
                    while chunk := resp.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        seen += chunk
                    record = {"model": body.get("model"), "status": resp.status, "usage": proxy.usage_of(bytes(seen))}
                    if removed:
                        record["removed"] = removed
                    if resp.status >= 400:
                        record["error"] = bytes(seen[:500]).decode("utf-8", errors="replace")
                    proxy._write(record)
                finally:
                    conn.close()
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server.server_address[1]

    def __exit__(self, *exc) -> None:
        if self.server:
            self.server.shutdown()


PROBE = r"""
import json, socket, urllib.request
out = {}
try:
    with urllib.request.urlopen("http://127.0.0.1:MODEL_PORT/health", timeout=10) as r:
        out["model"] = r.status == 200
except Exception:
    out["model"] = False
try:
    socket.getaddrinfo("huggingface.co", 443)
    out["dns"] = True
except Exception:
    out["dns"] = False
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5).close()
    out["internet"] = True
except Exception:
    out["internet"] = False
print("CV-PROBE " + json.dumps(out))
""".replace("MODEL_PORT", str(MODEL_PORT))


def preflight(bridge: Path, scratch: Path) -> str | None:
    """B6.2: inside the workflow box the model answers, and DNS and the internet do not."""
    work = Path(tempfile.mkdtemp(prefix="probe-", dir=scratch))
    try:
        (work / "tmp").mkdir()
        (work / "probe.py").write_text(PROBE, encoding="utf-8")
        done = subprocess.run(
            offline_box(work, work, work, bridge, "/usr/bin/python3", "-I", "probe.py"),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    line = next((x for x in done.stdout.splitlines() if x.startswith("CV-PROBE ")), None)
    if line is None:
        return f"探测程序没有输出：{done.stderr[-300:]}"
    seen = json.loads(line[len("CV-PROBE ") :])
    if seen["dns"] or seen["internet"]:
        return f"盒子没有断网：{seen}"
    return None if seen["model"] else "盒子里连不上模型"


TARGET = {"port": MODEL_PORT}  # outside the boxes: the local model, or the Anthropic proxy


def model_up() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{TARGET['port']}/health", timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------- grading (B4)


def official_preprocess(code: str, task: dict) -> str:
    """ClassEval test_pipeline.py: add_static_statement, then the task's import_statement in front."""
    lines = [line for line in code.split("\n") if "@staticmethod" not in line]
    out = []
    for line in lines:
        lead = len(line) - len(line.lstrip())
        if line.strip().startswith("def ") and "self" not in line and "cls" not in line and lead == 4:
            out.append("    @staticmethod")
        out.append(line)
    return "\n".join(task["import_statement"]) + "\n" + "\n".join(out)


RUNNER = r"""
import importlib, io, json, signal, sys, unittest
sys.path.insert(0, ".")  # -I leaves the working directory off the path
name = sys.argv[1]
def stop(*_):
    raise TimeoutError
signal.signal(signal.SIGALRM, stop)
signal.alarm(CLASS_SECONDS)
try:
    module = importlib.import_module("solution_test")
    suite = unittest.TestLoader().loadTestsFromTestCase(getattr(module, name))
    tests = [t.id().rsplit(".", 1)[-1] for t in suite]  # before running: a run empties the suite
    res = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    signal.alarm(0)
    out = {"errors": len(res.errors), "failures": len(res.failures), "testsRun": res.testsRun,
           "tests": tests,
           "failed_tests": sorted({t.id().rsplit(".", 1)[-1] for t, _ in res.errors + res.failures})}
except BaseException as e:
    signal.alarm(0)
    out = {"errors": 0, "failures": 0, "testsRun": 0, "exception": f"{type(e).__name__}: {e}"[:300]}
print("CV-GRADE " + json.dumps(out))
""".replace("CLASS_SECONDS", str(CLASS_SECONDS))


def test_answer(r: dict) -> str:
    """ClassEval get_test_answer."""
    if r["testsRun"] == 0 or r["errors"] == r["testsRun"]:
        return "error"
    if r["errors"] + r["failures"] == 0:
        return "success"
    return "partial_success" if r["errors"] + r["failures"] < r["testsRun"] else "fail"


def grade(code: str | None, task: dict, scratch: Path) -> dict:
    """B4: every test class in its own process, in an offline box, as ClassEval's pipeline runs it."""
    names = list(task["test_classes"])
    empty = {"class_ok": False, "classes_passed": 0, "classes_total": len(names), "classes": {}, "per_method": True}
    if code is None:
        return {**empty, "error": "没有代码"}
    work = Path(tempfile.mkdtemp(prefix="grade-", dir=scratch))
    classes: dict[str, dict] = {}
    try:
        (work / "tmp").mkdir()
        source = official_preprocess(code, task) + "\n" + task["test"]
        for name in names:
            # A fresh copy each time: a test class may write files, and none sees what another left.
            run_dir = work / f"c{len(classes)}"
            (run_dir / "tmp").mkdir(parents=True)
            (run_dir / "solution_test.py").write_text(source, encoding="utf-8")
            (run_dir / "_runner.py").write_text(RUNNER, encoding="utf-8")
            command = ["/usr/bin/python3", "-I", "_runner.py", name]
            try:
                done = subprocess.run(
                    offline_box(run_dir, run_dir, run_dir, None, *command),
                    capture_output=True,
                    text=True,
                    timeout=CLASS_SECONDS + 55,
                    check=False,
                )
                line = next((x for x in done.stdout.splitlines() if x.startswith("CV-GRADE ")), None)
                r = json.loads(line[len("CV-GRADE ") :]) if line else {"errors": 0, "failures": 0, "testsRun": 0}
                if line is None:
                    r["exception"] = f"没有输出（退出码 {done.returncode}）"
            except subprocess.TimeoutExpired:
                r = {"errors": 0, "failures": 0, "testsRun": 0, "exception": "超时"}
            r["answer"] = test_answer(r)
            classes[name] = r
    finally:
        shutil.rmtree(work, ignore_errors=True)
    passed = sum(1 for c in classes.values() if c["answer"] == "success")
    return {
        "class_ok": passed == len(names),
        "classes_passed": passed,
        "classes_total": len(names),
        "classes": classes,
        "per_method": True,
    }


def eligibility(tasks: list[dict], cache: Path, digest: str) -> dict[str, str | None]:
    """B5.1: the reference solution passes all its tests in every one of REFERENCE_RUNS gradings."""
    path = cache / f"eligibility-{digest[:12]}-{hashlib.sha256(RUNNER.encode()).hexdigest()[:8]}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    found: dict[str, str | None] = {}
    for task in tasks:
        if task["task_id"] in DEV_OVERLAP:
            found[task["task_id"]] = DEV_OVERLAP[task["task_id"]]
            continue
        reason = None
        for _ in range(REFERENCE_RUNS):
            g = grade(task["solution_code"], task, cache)
            if not g["class_ok"]:
                bad = {
                    n: c.get("exception") or c["answer"] for n, c in g["classes"].items() if c["answer"] != "success"
                }
                reason = f"参考解未通过：{bad}"[:300]
                break
        found[task["task_id"]] = reason
    path.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    return found


# ---------------------------------------------------------------- one task


def pick_code(proj: Path, class_name: str) -> tuple[str | None, list[str]]:
    """B3.3, and B4's local imports: the project modules the picked file imports."""
    named = proj / f"{class_name}.py"
    code = None
    if named.is_file():
        code = named.read_text(encoding="utf-8", errors="replace")
    else:
        pattern = re.compile(rf"^class\s+{re.escape(class_name)}\b", re.MULTILINE)
        found = [
            p for p in sorted(proj.glob("*.py")) if pattern.search(p.read_text(encoding="utf-8", errors="replace"))
        ]
        if len(found) == 1:
            code = found[0].read_text(encoding="utf-8", errors="replace")
    if code is None:
        return None, []
    imported = set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", code, re.MULTILINE))
    local = sorted(m for m in imported if (proj / f"{m}.py").is_file() or (proj / m / "__init__.py").is_file())
    return code, local


def run_dir_of(home: Path) -> Path | None:
    """The workflow's run directory: the one it wrote run.json into, preferring one with a report.

    Sorting names alone is not enough: an agent once wrote into a mistyped sibling such as
    20261108T2318305Z-baea, which sorts after the real 20261108T231805Z-baea and holds no report.
    """
    runs = sorted((home / ".chrys" / "cross-validate" / "projects").glob("*-proj/runs/*"))
    with_report = [r for r in runs if (r / "report.json").is_file()]
    started = [r for r in runs if (r / "run.json").is_file()]
    found = with_report or started
    return found[-1] if found else None


def report_of(home: Path) -> dict:
    run = run_dir_of(home)
    path = run / "report.json" if run else None
    return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}


def run_task(task: dict, task_dir: Path, model_yaml: Path, bridge: Path) -> dict:
    if task_dir.exists():
        shutil.rmtree(task_dir)
    task_dir.mkdir(parents=True)
    home = LIVE.prepare(task_dir, model_yaml)
    command = ["icode", "workflow", "run", "cross-validate", "--trust", "--json", "--input", goal_of(task)]
    started = time.monotonic()
    timed_out, returncode = False, None
    with open(task_dir / "run.json", "w") as out, open(task_dir / "run.err", "w") as err:
        try:
            returncode = subprocess.run(
                offline_box(task_dir / "proj", task_dir, home, bridge, *command),
                stdout=out,
                stderr=err,
                timeout=TASK_SECONDS,
                check=False,
            ).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
    seconds = round(time.monotonic() - started)
    report = report_of(home)
    code, local = pick_code(task_dir / "proj", task["class_name"])
    return {
        "task_id": task["task_id"],
        "class_name": task["class_name"],
        "conclusion": report.get("conclusion"),
        "reason": report.get("conclusion_reason"),
        "certified": report.get("conclusion") == "比对通过",
        # 细则 17.12: the same run judged as if the review had marked nothing (paired ablation).
        "certified_without_review": report.get("conclusion_without_review", report.get("conclusion")) == "比对通过",
        "timed_out": timed_out,
        "returncode": returncode,
        "seconds": seconds,
        "local_imports": local,
        "grade": grade(code, task, task_dir),
    }


def infrastructure_failure(result: dict) -> str | None:
    """B6: objective signs that the machine, not the plugin or the model's answer, stopped the task."""
    if result["timed_out"]:
        return None  # the time limit is part of the task
    if result["returncode"] is not None and result["returncode"] < 0:
        return f"工作流被信号 {-result['returncode']} 杀掉"
    if not model_up():
        return "任务结束时模型服务没有响应"
    return None


# ---------------------------------------------------------------- statistics (B7)


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float] | None:
    """Newcombe's hybrid score interval (method 10) for p1 − p2 of two independent proportions."""
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = k1 / n1, k2 / n2
    (l1, u1), (l2, u2) = wilson(k1, n1), wilson(k2, n2)
    d = p1 - p2
    return d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)


def fisher_greater(tp: int, fp: int, fn: int, tn: int) -> float | None:
    """One-sided Fisher exact p: the certified group (tp, fp) is correct more often than the rest (fn, tn)."""
    certified, rest, correct = tp + fp, fn + tn, tp + fn
    n = certified + rest
    if certified == 0 or rest == 0 or correct in (0, n):
        return None
    total = math.comb(n, certified)
    top = min(certified, correct)
    return sum(math.comb(correct, k) * math.comb(n - correct, certified - k) for k in range(tp, top + 1)) / total


def conclusion_of(r: dict) -> str:
    return r["conclusion"] or ("超时" if r["timed_out"] else "没有报告")


def in_spec(grade: dict, labels: dict | None) -> bool | None:
    """B4.6: every test method the docstring determines passes; None when there is nothing to judge."""
    if not labels or not grade.get("per_method"):
        return None  # no labels, or graded before methods were recorded one by one
    wanted = [k for k, v in labels.items() if LABELS4.get(v, v) == "规定"]
    if not wanted:
        return None
    for key in wanted:
        cls, _, method = key.partition(".")
        c = grade.get("classes", {}).get(cls)
        if not c or "tests" not in c or method not in c["tests"] or method in c.get("failed_tests", []):
            return False  # 超时、导入失败、没有代码：每个方法都算失败
    return True


def metrics(results: list[dict], ok=None) -> dict:
    """B7. `ok` says whether a result's code is correct; the official measure by default."""
    ok = ok or (lambda r: r["grade"]["class_ok"])
    n = len(results)
    tp = sum(r["certified"] and ok(r) for r in results)
    fp = sum(r["certified"] and not ok(r) for r in results)
    fn = sum(not r["certified"] and ok(r) for r in results)
    tn = n - tp - fp - fn

    def ratio(k: int, m: int) -> dict:
        return {"k": k, "n": m, "value": k / m if m else None, "ci95": wilson(k, m)}

    passed = sum(r["grade"]["classes_passed"] for r in results)
    total = sum(r["grade"]["classes_total"] for r in results)
    out = {
        "n": n,
        "confusion": {"TP": tp, "FP": fp, "FN": fn, "TN": tn},
        "pass@1": ratio(tp + fn, n),
        "fun_pass@1": ratio(passed, total),
        "coverage": ratio(tp + fp, n),
        "precision": ratio(tp, tp + fp),
        "uncertified_accuracy": ratio(fn, fn + tn),
        "recall": ratio(tp, tp + fn),
        "specificity": ratio(tn, tn + fp),
        "false_certification_rate": ratio(fp, fp + tn),
        "fisher_p": fisher_greater(tp, fp, fn, tn),
        "alpha": ALPHA,
    }
    out["difference"] = {
        "value": None if not (tp + fp and fn + tn) else tp / (tp + fp) - fn / (fn + tn),
        "ci95": newcombe(tp, tp + fp, fn, fn + tn),
    }
    p, base = out["precision"]["value"], out["pass@1"]["value"]
    out["lift"] = None if p is None or not base else p / base
    table: dict[str, dict[str, int]] = {}
    for r in results:
        row = table.setdefault(conclusion_of(r), {"正确": 0, "错误": 0})
        row["正确" if ok(r) else "错误"] += 1
    out["by_conclusion"] = table
    times = sorted(r["seconds"] for r in results)
    out["seconds_median"] = (times[(len(times) - 1) // 2] + times[len(times) // 2]) / 2 if times else None
    out["seconds_total"] = sum(times)
    return out


def _pct(m: dict) -> str:
    if m["value"] is None:
        return f"无定义（{m['k']}/{m['n']}）"
    lo, hi = m["ci95"]
    return f"{m['value']:.0%}（{m['k']}/{m['n']}，95% CI {lo:.0%}–{hi:.0%}）"


def _metric_lines(m: dict) -> list[str]:
    c = m["confusion"]
    d = m["difference"]
    diff = "无定义" if d["value"] is None else f"{d['value'] * 100:+.0f} 个百分点"
    if d["ci95"]:
        diff += f"（95% CI {d['ci95'][0] * 100:+.0f} 到 {d['ci95'][1] * 100:+.0f}）"
    lift = "无定义" if m["lift"] is None else f"{m['lift']:.2f} 倍"
    fisher = "无定义" if m["fisher_p"] is None else f"{m['fisher_p']:.3f}（α = {ALPHA}）"
    return [
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 基准正确率 pass@1（类级） | {_pct(m['pass@1'])} |",
        f"| 测试类级 pass@1（ClassEval 的 fun_success 算法） | {_pct(m['fun_pass@1'])} |",
        f"| 覆盖率 coverage（出具凭证的比例） | {_pct(m['coverage'])} |",
        f"| 精确率 precision（有凭证代码的正确率） | {_pct(m['precision'])} |",
        f"| 无凭证代码的正确率 | {_pct(m['uncertified_accuracy'])} |",
        f"| 两组正确率之差（Newcombe 区间） | {diff} |",
        f"| 提升倍数 lift（precision ÷ pass@1） | {lift} |",
        f"| 召回率 recall（正确代码拿到凭证的比例） | {_pct(m['recall'])} |",
        f"| 特异度 specificity（错误代码被拦下的比例） | {_pct(m['specificity'])} |",
        f"| 误发凭证率 FP/(FP+TN) | {_pct(m['false_certification_rate'])} |",
        f"| Fisher 精确检验（单侧）p | {fisher} |",
        f"| 每题用时 | 中位数 {(m['seconds_median'] or 0) / 60:.0f} 分钟，合计 {m['seconds_total'] / 3600:.1f} 小时 |",
        "",
        "混淆矩阵（行：是否出具凭证；列：隐藏测试是否全部通过）：",
        "",
        "| | 正确 | 错误 |",
        "| --- | --- | --- |",
        f"| 有凭证 | TP {c['TP']} | FP {c['FP']} |",
        f"| 没有凭证 | FN {c['FN']} | TN {c['TN']} |",
        "",
        "按报告结论（风险–覆盖点：risk = 1 − precision）：",
        "",
        "| 结论 | 正确 | 错误 |",
        "| --- | --- | --- |",
        *(f"| {k} | {v['正确']} | {v['错误']} |" for k, v in m["by_conclusion"].items()),
    ]


def _ablation_lines(results: list[dict], judged: list[dict], spec: dict) -> list[str]:
    """细则 17.12: the same runs as if the review had marked nothing; 误拦 and 漏拦 side by side."""
    have = [r for r in results if "certified_without_review" in r]
    if not have:
        return []

    def count(rs, ok, key):
        right = [r for r in rs if ok(r)]
        wrong = [r for r in rs if not ok(r)]
        return sum(not r[key] for r in right), len(right), sum(r[key] for r in wrong), len(wrong)

    lines = [
        "## 复查的配对消融（同一次运行，不算复查标记时的结论，细则 17.12）",
        "",
        "| 口径 | 版本 | 正确代码被误拦 | 错误代码拿到凭证 |",
        "| --- | --- | --- | --- |",
    ]
    official = lambda r: r["grade"]["class_ok"]  # noqa: E731
    inside = [r for r in have if r in judged]
    for name, rs, ok in (("官方测试", have, official), ("需求内", inside, lambda r: spec[r["task_id"]])):
        for version, key in (("有复查", "certified"), ("不算复查", "certified_without_review")):
            fr, right, fc, wrong = count(rs, ok, key)
            lines.append(f"| {name} | {version} | {fr}/{right} | {fc}/{wrong} |")
    return [*lines, ""]


def summarize(results: list[dict], meta: dict, infra: dict[str, str] | None = None, labels: dict | None = None) -> str:
    labels = labels or {}
    m = metrics(results)
    judged = [r for r in results if in_spec(r["grade"], labels.get(r["task_id"])) is not None]
    spec = {r["task_id"]: in_spec(r["grade"], labels.get(r["task_id"])) for r in judged}
    lines = [
        "# ClassEval " + ("开发运行（逐题查看，不是正式评测）" if meta.get("dev") else "试点结果"),
        "",
        f"- 基准：{DATASET}（数据 sha256 {meta['sha256'][:16]}…）；插件提交 {meta['commit']}",
        f"- 模型：{meta.get('model', '—')}；解码参数用服务端默认值（{meta.get('decoding', '未记录')}）",
        f"- 样本：计划 {meta['planned']} 题，已完成 {m['n']} 题；种子 {SEED}；排除 {len(meta['excluded'])} 题（见 meta.json）",
        f"- 基础设施失败、不计入指标的题：{'、'.join(f'{k}（{v}）' for k, v in (infra or {}).items()) or '无'}",
        "",
        "## 主口径：官方测试（B4.4）",
        "",
        *_metric_lines(m),
        "",
        f"## 第二口径：需求内（B4.6，{len(judged)} 题有可判的标注）",
        "",
        *(_metric_lines(metrics(judged, lambda r: spec[r["task_id"]])) if judged else ["没有标注，不统计。"]),
        "",
        *_ablation_lines(results, judged, spec),
        "| 题 | 结论 | 类级正确 | 需求内正确 | 测试类通过 | 用时（分钟） | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        g = r["grade"]
        note = []
        if r.get("local_imports"):
            note.append("依赖项目内其他文件：" + "、".join(r["local_imports"]))
        if g.get("error"):
            note.append(g["error"])
        mark = {True: "是", False: "否", None: "—"}[spec.get(r["task_id"])]
        lines.append(
            f"| {r['task_id']} {r['class_name']} | {conclusion_of(r)} | {'是' if g['class_ok'] else '否'} | {mark} "
            f"| {g['classes_passed']}/{g['classes_total']} | {r['seconds'] / 60:.0f} | {'；'.join(note) or ''} |"
        )
    usage = meta.get("usage_path")
    if usage and Path(usage).exists():
        total: dict[str, int] = {}
        calls = 0
        for line in Path(usage).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            calls += 1
            for k, v in (record.get("usage") or {}).items():
                total[k] = total.get(k, 0) + v
        lines += ["", f"API 用量：{calls} 次请求，" + "，".join(f"{k} {v:,}" for k, v in sorted(total.items()))]
    lines += [
        "",
        f"注：{CONTAMINATION}",
        "这里的 pass@1 是 agent 在可用题目子集上一次运行的结果，不能和论文里单次生成的 ClassEval 成绩直接比较。",
        "样本小，置信区间很宽：这是试点，不下统计结论。",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- run


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plugin_commit() -> tuple[str, bool]:
    """The plugin's commit, and whether the plugin itself has changes that are not committed."""
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    status = ["git", "status", "--porcelain", "--", *PLUGIN_PATHS]
    dirty = subprocess.run(status, cwd=ROOT, capture_output=True, text=True, check=False).stdout
    return head.stdout.strip(), bool(dirty.strip())


def server_status() -> dict:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{MODEL_PORT}/v1/status", timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def log_attempt(out: Path, record: dict) -> None:
    with open(out / "attempts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}, ensure_ascii=False) + "\n")


def attempts_of(out: Path, task_id: str) -> int:
    path = out / "attempts.jsonl"
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    return sum(1 for x in lines if json.loads(x).get("task_id") == task_id and json.loads(x).get("event") == "start")


def wait_preflight(bridge: Path, scratch: Path, minutes: int = 30) -> str | None:
    problem = None
    for _ in range(minutes):
        problem = preflight(bridge, scratch)
        if problem is None or problem.startswith("盒子没有断网"):
            return problem
        time.sleep(60)
    return problem


def load_labels(folder: Path | None, chosen: list[str], tasks: dict) -> tuple[dict, dict]:
    """B4.6: one file per task, {"tests": {"Class.method": "规定"|"未规定"|"矛盾"}, ...}; every chosen task."""
    if folder is None:
        return {}, {}
    labels, digests, missing = {}, {}, []
    for task_id in chosen:
        path = folder / f"{task_id}.json"
        if not path.exists():
            missing.append(task_id)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        tests = data["tests"]
        bad = [
            k
            for k, v in tests.items()
            if (v not in LABELS and v not in LABELS4) or k.partition(".")[0] not in tasks[task_id]["test_classes"]
        ]
        if bad:
            raise ValueError(f"{task_id} 的标注不合格：{bad[:3]}")
        labels[task_id], digests[task_id] = tests, sha256_of(path)
    if missing:
        raise ValueError(f"这些题没有标注，不能开始（B4.6）：{'、'.join(missing)}")
    return labels, digests


def rescore(out: Path, labels_dir: Path | None = None, cache: Path | None = None) -> int:
    """Read each finished task's conclusion again from its saved run directory; nothing is rerun."""
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    labels = {}
    if labels_dir:
        for task_id in meta["chosen"]:
            path = labels_dir / f"{task_id}.json"
            if path.exists():
                labels[task_id] = json.loads(path.read_text(encoding="utf-8"))["tests"]
    results, infra = [], {}
    for task_id in meta["chosen"]:
        done = out / f"{task_id}.json"
        if done.exists():
            result = json.loads(done.read_text(encoding="utf-8"))
            if cache and not result["grade"].get("per_method"):
                # Graded before each method was recorded: grade the saved code again, the same way.
                task = {t["task_id"]: t for t in load_dataset(cache)[0]}[task_id]
                code, _ = pick_code(out / "tasks" / task_id / "proj", task["class_name"])
                regraded = grade(code, task, out / "scratch")
                if regraded["class_ok"] != result["grade"]["class_ok"]:
                    print(f"{task_id}：重新判对的类级结果变了，保留原结果")
                else:
                    result["grade"] = regraded
                    done.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            report = report_of(out / "tasks" / task_id / "home")
            fixed = {"conclusion": report.get("conclusion"), "reason": report.get("conclusion_reason")}
            fixed["certified"] = fixed["conclusion"] == "比对通过"
            if "conclusion_without_review" in report:
                fixed["certified_without_review"] = report["conclusion_without_review"] == "比对通过"
            if any(result.get(k) != v for k, v in fixed.items()):
                print(f"{task_id}：{result.get('conclusion')} → {fixed['conclusion']}")
                result.update(fixed, rescored=True)
                done.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            results.append(result)
        elif (out / f"{task_id}.infra.json").exists():
            infra[task_id] = json.loads((out / f"{task_id}.infra.json").read_text(encoding="utf-8"))["reason"]
    (out / "summary.md").write_text(summarize(results, meta, infra, labels), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics(results), ensure_ascii=False, indent=1), "utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-yaml", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--cache", type=Path, help="数据缓存目录，默认 <out>/../classeval-cache，不能在仓库里")
    parser.add_argument("--rescore", action="store_true", help="只按保存的运行目录重读报告结论，不重跑")
    parser.add_argument(
        "--anthropic-key-file",
        type=Path,
        help="用 Anthropic API：key 只留在盒外的代理里，模型配置的 base_url 填 127.0.0.1",
    )
    parser.add_argument("--labels", type=Path, help="需求内口径的标注目录（B4.6），每题一个 <task_id>.json")
    parser.add_argument("--tasks", help="开发运行：逐题指定，逗号分隔；不抽样，插件可以有未提交的改动")
    args = parser.parse_args()
    if args.rescore:
        return rescore(
            args.out.resolve(),
            args.labels.resolve() if args.labels else None,
            args.cache.resolve() if args.cache else None,
        )
    out = args.out.resolve()
    cache = (args.cache or out.parent / "classeval-cache").resolve()
    for path in (out, cache):
        if path == ROOT or ROOT in path.parents:
            parser.error("数据和结果不能放在仓库里（B6.1）")
    if out in cache.parents or cache in out.parents or cache == out:
        parser.error("缓存目录和结果目录要分开：测试只在缓存里")
    commit, dirty = plugin_commit()
    if dirty and not args.tasks:
        parser.error(f"插件有未提交的改动，先提交再评测（B6.4）：{' '.join(PLUGIN_PATHS)}")
    if dirty:
        diff = subprocess.run(["git", "diff", "HEAD", "--", *PLUGIN_PATHS], cwd=ROOT, capture_output=True, check=False)
        commit += f"（开发运行，未提交改动 sha256 {hashlib.sha256(diff.stdout).hexdigest()[:12]}）"
    out.mkdir(parents=True, exist_ok=True)
    tasks, digest = load_dataset(cache)
    by_id = {t["task_id"]: t for t in tasks}
    ok = eligibility(tasks, cache, digest)
    if args.tasks:
        chosen = [t.strip() for t in args.tasks.split(",") if t.strip()]
        unknown = [t for t in chosen if t not in by_id or ok.get(t) is not None]
        if unknown:
            parser.error(f"这些题不存在或已被排除：{'、'.join(unknown)}")
    else:  # B5.3: a formal run never samples a development task
        chosen = sample(
            [t["task_id"] for t in tasks if ok.get(t["task_id"]) is None and t["task_id"] not in DEV_TASKS], args.n
        )
    try:
        labels, label_digests = load_labels(args.labels.resolve() if args.labels else None, chosen, by_id)
    except ValueError as e:
        parser.error(str(e))
    model_text = args.model_yaml.read_text(encoding="utf-8")
    status = {"provider": "anthropic", "via": "盒外代理"} if args.anthropic_key_file else server_status()
    meta = {
        "dataset": DATASET,
        "sha256": digest,
        "seed": SEED,
        "planned": args.n,
        "chosen": chosen,
        "excluded": {k: v for k, v in ok.items() if v is not None},
        "commit": commit,
        "script_sha256": sha256_of(Path(__file__)),
        "model_yaml_sha256": hashlib.sha256(model_text.encode()).hexdigest(),
        "model": re.search(r"^model_id:\s*(\S+)", model_text, re.MULTILINE).group(1),
        "decoding": "模型配置没有设置 temperature、top_p、seed",
        "server": status,
        "usage_path": str(out / "api-usage.jsonl") if args.anthropic_key_file else None,
        "dev": bool(args.tasks),
        "dev_tasks_excluded": [] if args.tasks else list(DEV_TASKS),
        "labels": label_digests,
    }
    meta_path = out / "meta.json"
    if meta_path.exists():
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        if chosen[: len(old["chosen"])] == old["chosen"] and len(chosen) > len(old["chosen"]):
            old["chosen"], old["planned"] = chosen, args.n  # B5.2: a longer run keeps the same first tasks
            meta_path.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
        keys = ("sha256", "model_yaml_sha256") if args.tasks else ("chosen", "sha256", "commit", "model_yaml_sha256")
        if args.tasks:  # a development run may add tasks and plugin changes; each task records its commit
            old["chosen"] = list(dict.fromkeys([*old["chosen"], *chosen]))
            meta_path.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")
        for key in keys:
            if old.get(key) != meta[key]:
                parser.error(f"结果目录里已有条件不同的结果（{key} 变了），不能接着跑（B6.4）")
        if old["script_sha256"] != meta["script_sha256"]:
            print("注意：评测脚本变了；已完成的题的结果保留不变", file=sys.stderr)
        meta = old
    else:
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    scratch = out / "scratch"
    scratch.mkdir(exist_ok=True)
    results, infra = [], {}
    with contextlib.ExitStack() as stack:
        proxy = None
        if args.anthropic_key_file:
            proxy = AnthropicProxy(args.anthropic_key_file.resolve(), out / "api-usage.jsonl")
            TARGET["port"] = stack.enter_context(proxy)
        bridge = stack.enter_context(ModelBridge(TARGET["port"]))
        for task_id in chosen:
            if proxy:
                proxy.task = task_id
            done = out / f"{task_id}.json"
            failed = out / f"{task_id}.infra.json"
            while not done.exists() and not failed.exists():
                problem = wait_preflight(bridge, scratch)
                if problem:
                    print(f"停止：{problem}", file=sys.stderr)
                    return 2
                tries = attempts_of(out, task_id)
                if tries >= MAX_ATTEMPTS:
                    reason = "两次都因基础设施失败（或评测进程被杀）没有完成"
                    failed.write_text(json.dumps({"task_id": task_id, "reason": reason}, ensure_ascii=False), "utf-8")
                    break
                log_attempt(out, {"task_id": task_id, "event": "start", "attempt": tries + 1})
                result = run_task(by_id[task_id], out / "tasks" / task_id, args.model_yaml.resolve(), bridge)
                problem = infrastructure_failure(result)
                log_attempt(out, {"task_id": task_id, "event": "end", "infra": problem, "seconds": result["seconds"]})
                if problem is None:
                    result["commit"] = commit
                    done.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            if done.exists():
                results.append(json.loads(done.read_text(encoding="utf-8")))
            else:
                infra[task_id] = json.loads(failed.read_text(encoding="utf-8"))["reason"]
            (out / "summary.md").write_text(summarize(results, meta, infra, labels), encoding="utf-8")
            (out / "metrics.json").write_text(json.dumps(metrics(results), ensure_ascii=False, indent=1), "utf-8")
    print((out / "summary.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
