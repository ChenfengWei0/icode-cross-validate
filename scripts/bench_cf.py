"""Benchmark the io mode (README 第 20 节) on Codeforces problems whose every valid output is accepted.

    python3 scripts/bench_cf.py run --data DIR --gen DIR --problems FILE --samples 0 1 2 3 --out DIR [--repair]
    python3 scripts/bench_cf.py summary --out DIR [--problems FILE]

DIR layout (outside the repository, see BENCHMARK_CF.md):
  data/problems/<pid>/problem.json, checker.py, tests/NNN.in, NNN.out   hidden tests and the dataset's checker
  gen/<pid>-<model>-<i>.py                                               the main task's one-shot programs

The hidden tests decide correctness; the io mode never sees them. Model calls go to the Anthropic API with
the key in $CV_BENCH_KEY_FILE (default ~/.config/cv-bench/anthropic.key), which never enters a box.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-validate"))
import cv_io as IO

MODEL = "claude-haiku-5-5"
_usage_lock = threading.Lock()
USAGE = {"calls": 0, "input": 0, "output": 0}


def asker(model: str, max_tokens: int):
    key = Path(os.environ.get("CV_BENCH_KEY_FILE", Path.home() / ".config/cv-bench/anthropic.key")).read_text().strip()

    def ask(prompt: str) -> str:
        body = json.dumps(
            {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        ).encode()
        for attempt in range(6):
            req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
                "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})  # fmt: skip
            try:
                with urllib.request.urlopen(req, timeout=900) as r:  # noqa: S310
                    data = json.loads(r.read())
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                code = getattr(e, "code", None)
                if code is not None and code < 500 and code != 429:
                    raise
                time.sleep(10 * (attempt + 1))
        else:
            raise RuntimeError("API unavailable")
        with _usage_lock:
            u = data.get("usage", {})
            USAGE["calls"] += 1
            USAGE["input"] += u.get("input_tokens", 0)
            USAGE["output"] += u.get("output_tokens", 0)
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")

    return ask


def requirement(p: dict) -> tuple[str, list[tuple[str, str]]]:
    """The statement as the main task saw it (without the instruction line), and its examples."""
    ex = p.get("examples")
    ex = ast.literal_eval(ex) if isinstance(ex, str) and ex not in ("None", "") else (ex or [])
    pairs = [(e["input"].replace("\r\n", "\n"), e["output"].replace("\r\n", "\n")) for e in ex]
    exs = "\n\n".join(f"Input:\n{i}\nOutput:\n{o}" for i, o in pairs)
    note = p.get("note")
    text = (f"# {p['title']}\n\nTime limit: {p['time_limit']} s, memory limit: {p['memory_limit']} MB\n\n{p['description']}\n\n"
            f"## Input\n\n{p['input_format']}\n\n## Output\n\n{p['output_format']}\n\n## Examples\n\n{exs}\n\n"
            + (f"## Note\n\n{note}\n" if note and note != "None" else ""))  # fmt: skip
    return text, pairs


# ---------------------------------------------------------------------------------------------------
# Hidden-test grading (ground truth)
# ---------------------------------------------------------------------------------------------------


def _box(d: Path, args: list[str], stdin=None, timeout: float = 60):
    try:
        p = subprocess.run(
            [*IO.BOX, "--bind", str(d), "/w", "--chdir", "/w", *args],
            stdin=stdin,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired:
        return None, b""


def grade(problem_dir: Path, program: str, limit: float = 20) -> dict:
    verdicts: dict[str, int] = {}
    for case in sorted((problem_dir / "tests").glob("*.in")):
        with tempfile.TemporaryDirectory(prefix="cfg-") as tmp:
            d = Path(tmp)
            (d / "sol.py").write_text(program)
            shutil.copy(problem_dir / "checker.py", d / "checker.py")
            shutil.copy(case, d / "in.txt")
            shutil.copy(case.with_suffix(".out"), d / "ans.txt")
            with open(case) as f:
                rc, out = _box(d, ["/usr/bin/python3", "-I", "sol.py"], stdin=f, timeout=limit)
            if rc is None or rc != 0:
                v = "TLE" if rc is None else "RE"
            else:
                (d / "sub.txt").write_bytes(out)
                _, res = _box(d, ["/usr/bin/python3", "-I", "checker.py", "in.txt", "ans.txt", "sub.txt"], timeout=300)
                try:
                    v = "OK" if float(res.decode().split()[-1]) in (1, 100) else "WA"
                except (ValueError, IndexError):
                    v = "CHECKER_ERR"
        verdicts[v] = verdicts.get(v, 0) + 1
        if v in ("WA", "RE"):
            break  # one logic failure decides
    return {
        "correct": set(verdicts) == {"OK"},
        "logic_correct": not ({"WA", "RE", "CHECKER_ERR"} & set(verdicts)),
        "verdicts": verdicts,
    }


# ---------------------------------------------------------------------------------------------------
# One problem
# ---------------------------------------------------------------------------------------------------


def run_problem(pid: str, a, s: IO.Settings) -> None:
    out = Path(a.out) / pid
    out.mkdir(parents=True, exist_ok=True)
    pdir = Path(a.data) / "problems" / pid
    req, examples = requirement(json.loads((pdir / "problem.json").read_text()))
    spec_file = out / "spec.json"
    if spec_file.exists():
        d = json.loads(spec_file.read_text())
        spec = IO.Spec(d["text"], d["conditions"], d["inputs"]) if d["text"] else None
    else:
        spec, log = IO.write_spec(asker(a.model, 64000), req, examples, s)
        spec_file.write_text(json.dumps({"text": spec.text if spec else None, "conditions": spec.conditions if spec else {},
                                         "inputs": spec.inputs if spec else [], "log": log}, ensure_ascii=False, indent=1))  # fmt: skip
    judge = asker(a.model, 32000) if not a.no_judge else None
    fixer = asker(a.model, 64000) if a.repair else None
    for i in a.samples:
        f = Path(a.gen) / f"{pid}-{a.model}-{i}.py"
        res_file = out / f"s{i}.json"
        if not f.exists() or res_file.exists():
            continue
        program = f.read_text()
        if spec is None:
            rounds = [{"round": 0, "program": program, "conclusion": IO.NONE, "reason": "检查模块未通过自检"}]
        else:
            try:
                rounds = IO.validate(program, spec, req, s, judge, fixer)
            except RuntimeError as e:
                rounds = [{"round": 0, "program": program, "conclusion": IO.NONE, "reason": str(e)}]
        for r in rounds:
            r["truth"] = grade(pdir, r["program"])
            r.pop("program_text", None)
        res_file.write_text(json.dumps({"pid": pid, "sample": i, "rounds": rounds}, ensure_ascii=False, indent=1))
        print(pid, i, [(r["conclusion"], r["truth"]["correct"]) for r in rounds], flush=True)


# ---------------------------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------------------------


def summary(out: Path, problems: list[str] | None) -> dict:
    rows = []
    for f in sorted(out.glob("*/s*.json")):
        d = json.loads(f.read_text())
        if problems and d["pid"] not in problems:
            continue
        rows.append(d)
    n = len(rows)
    if not n:
        return {"samples": 0}
    first = [d["rounds"][0] for d in rows]
    last = [d["rounds"][-1] for d in rows]

    def block(rs):
        correct = [r["truth"]["correct"] for r in rs]
        cert = [r for r in rs if r["conclusion"] == IO.PASS]
        cc = sum(r["truth"]["correct"] for r in cert)
        wrong = [r for r in rs if not r["truth"]["correct"]]
        right = [r for r in rs if r["truth"]["correct"]]
        return {"n": len(rs), "correct": sum(correct), "p_correct": round(sum(correct) / len(rs), 3),
                "certified": len(cert), "certified_correct": cc,
                "precision": round(cc / len(cert), 3) if cert else None,
                "false_alarm": round(sum(r["conclusion"] == IO.FAIL for r in right) / len(right), 3) if right else None,
                "caught_wrong_k": round(sum(r["conclusion"] == IO.FAIL for r in wrong) / len(wrong), 3) if wrong else None,
                "no_conclusion": sum(r["conclusion"] == IO.NONE for r in rs)}  # fmt: skip

    res = {"samples": n, "first_round": block(first), "after_repair": block(last),
           "repaired": sum(len(d["rounds"]) > 1 for d in rows)}  # fmt: skip
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", required=True)
    r.add_argument("--gen", required=True)
    r.add_argument("--problems", required=True)
    r.add_argument("--samples", type=int, nargs="+", default=[0])
    r.add_argument("--out", required=True)
    r.add_argument("--model", default=MODEL)
    r.add_argument("--repair", action="store_true")
    r.add_argument("--no-judge", action="store_true")
    r.add_argument("--workers", type=int, default=4)
    sm = sub.add_parser("summary")
    sm.add_argument("--out", required=True)
    sm.add_argument("--problems")
    a = ap.parse_args()
    if a.cmd == "summary":
        probs = [x.strip() for x in Path(a.problems).read_text().splitlines() if x.strip()] if a.problems else None
        print(json.dumps(summary(Path(a.out), probs), ensure_ascii=False, indent=1))
        return
    config = json.loads((Path(__file__).resolve().parents[1] / "cross-validate" / "config.json").read_text())
    s = IO.Settings.load(config)
    if not a.repair:
        s.repair_rounds = 0
    pids = [x.strip() for x in Path(a.problems).read_text().splitlines() if x.strip()]
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(lambda p: run_problem(p, a, s), pids))
    print(json.dumps(USAGE))


if __name__ == "__main__":
    main()
