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


def verdict(problem_dir: Path, program: str, inp: str, ans: str, limit: float = 20) -> str:
    """OK, WA, RE, TLE or CHECKER_ERR for one input, judged by the dataset's checker."""
    with tempfile.TemporaryDirectory(prefix="cfg-") as tmp:
        d = Path(tmp)
        (d / "sol.py").write_text(program)
        shutil.copy(problem_dir / "checker.py", d / "checker.py")
        (d / "in.txt").write_text(inp)
        (d / "ans.txt").write_text(ans)
        with open(d / "in.txt") as f:
            rc, out = _box(d, ["/usr/bin/python3", "-I", "sol.py"], stdin=f, timeout=limit)
        if rc is None or rc != 0:
            return "TLE" if rc is None else "RE"
        (d / "sub.txt").write_bytes(out)
        _, res = _box(d, ["/usr/bin/python3", "-I", "checker.py", "in.txt", "ans.txt", "sub.txt"], timeout=300)
        try:
            return "OK" if float(res.decode().split()[-1]) in (1, 100) else "WA"
        except (ValueError, IndexError):
            return "CHECKER_ERR"


def grade(problem_dir: Path, program: str, limit: float = 20) -> dict:
    verdicts: dict[str, int] = {}
    first_fail = None
    for case in sorted((problem_dir / "tests").glob("*.in")):
        v = verdict(problem_dir, program, case.read_text(), case.with_suffix(".out").read_text(), limit)
        verdicts[v] = verdicts.get(v, 0) + 1
        if v != "OK" and first_fail is None:
            first_fail = case.name
        if v in ("WA", "RE") or verdicts.get("TLE", 0) >= 2:
            break  # one logic failure decides; two timeouts settle the speed question
    return {
        "correct": set(verdicts) == {"OK"},
        "logic_correct": not ({"WA", "RE", "CHECKER_ERR"} & set(verdicts)),
        "verdicts": verdicts,
        "first_fail": first_fail,
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
    if spec is not None and not a.no_large:
        large_file = out / "large.json"
        if large_file.exists():
            spec.large = json.loads(large_file.read_text())["inputs"]
        else:
            log = IO.write_large(asker(a.model, 64000), req, spec, s)
            large_file.write_text(json.dumps({"inputs": spec.large, "log": log}, ensure_ascii=False))
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


def _block(rs: list[dict], key: str) -> dict:
    correct = [r["truth"][key] for r in rs]
    cert = [r for r in rs if r["conclusion"] == IO.PASS]
    cc = sum(r["truth"][key] for r in cert)
    wrong = [r for r in rs if not r["truth"][key]]
    right = [r for r in rs if r["truth"][key]]
    return {
        "n": len(rs),
        "correct": sum(correct),
        "p_correct": round(sum(correct) / len(rs), 3) if rs else None,
        "certified": len(cert),
        "certified_correct": cc,
        "precision": round(cc / len(cert), 3) if cert else None,
        "false_alarm": round(sum(r["conclusion"] == IO.FAIL for r in right) / len(right), 3) if right else None,
        "k_caught_wrong": round(sum(r["conclusion"] == IO.FAIL for r in wrong) / len(wrong), 3) if wrong else None,
        "no_conclusion": sum(r["conclusion"] == IO.NONE for r in rs),
    }


def _bootstrap(rows: list[dict], key: str, n: int = 2000) -> dict:
    """95% intervals, resampling problems (samples of one problem share a check module)."""
    import random

    by: dict[str, list[dict]] = {}
    for d in rows:
        by.setdefault(d["pid"], []).append(d)
    pids, rng, lift1, lift2, final = sorted(by), random.Random(0), [], [], []  # noqa: S311 (resampling, not secrets)
    for _ in range(n):
        pick = [d for p in (rng.choice(pids) for _ in pids) for d in by[p]]
        first = [d["rounds"][0] for d in pick]
        last = [d["rounds"][-1] for d in pick]
        pm = sum(r["truth"][key] for r in first) / len(first)
        for rs, out in ((first, lift1), (last, lift2)):
            cert = [r for r in rs if r["conclusion"] == IO.PASS]
            if cert:
                out.append(sum(r["truth"][key] for r in cert) / len(cert) - pm)
        final.append(sum(r["truth"][key] for r in last) / len(last) - pm)

    def ci(xs):
        xs = sorted(xs)
        return [round(xs[int(0.025 * len(xs))], 3), round(xs[int(0.975 * len(xs)) - 1], 3)] if xs else None

    return {
        "precision_minus_pm_first": ci(lift1),
        "precision_minus_pm_after_repair": ci(lift2),
        "final_minus_pm": ci(final),
    }


def summary(out: Path, problems: list[str] | None) -> dict:
    rows, empty = [], 0
    for f in sorted(out.glob("*/s[0-9]*.json")):
        d = json.loads(f.read_text())
        if problems and d["pid"] not in problems:
            continue
        if not d["rounds"][0]["program"].strip():
            empty += 1  # the main task produced no code (C3): not a certificate question
            continue
        rows.append(d)
    if not rows:
        return {"samples": 0, "no_code": empty}
    res: dict = {"samples": len(rows), "problems": len({d["pid"] for d in rows}), "no_code": empty,
                 "repaired": sum(len(d["rounds"]) > 1 for d in rows)}  # fmt: skip
    for key in ("correct", "logic_correct"):
        res[key] = {
            "first_round": _block([d["rounds"][0] for d in rows], key),
            "after_repair": _block([d["rounds"][-1] for d in rows], key),
            "ci": _bootstrap(rows, key),
        }
    audited = []
    for d in rows:
        f = out / d["pid"] / f"audit-s{d['sample']}.json"
        if f.exists():
            v = json.loads(f.read_text())
            audited.append({"sample": f"{d['pid']}#{d['sample']}", "kind": v["kind"], "verdict": v["verdict"]})
            r0 = d["rounds"][0]
            if v["kind"] == "alarm" and v["verdict"] == "INVALID":
                r0["truth"] = {**r0["truth"], "audited": False}
            elif v["kind"] == "escape" and v["verdict"] in ("VALID", "ILLEGAL_TEST"):
                r0["truth"] = {**r0["truth"], "audited": True}
    for d in rows:
        for r in d["rounds"]:
            r["truth"].setdefault("audited", r["truth"]["correct"])
    res["audited"] = {
        "cases": audited,
        "first_round": _block([d["rounds"][0] for d in rows], "audited"),
        "ci": _bootstrap(rows, "audited"),
    }
    res["escaped"] = [f"{d['pid']}#{d['sample']}" for d in rows
                      if d["rounds"][0]["conclusion"] == IO.PASS and not d["rounds"][0]["truth"]["correct"]]  # fmt: skip
    res["false_alarms"] = [f"{d['pid']}#{d['sample']}" for d in rows
                           if d["rounds"][0]["conclusion"] == IO.FAIL and d["rounds"][0]["truth"]["correct"]]  # fmt: skip
    return res


AUDIT = """You are refereeing a disagreement about a competitive programming problem. Below are the problem
statement, one input and the output a program printed for it. Decide, from the statement alone, whether this
output is an acceptable answer for this input. Check every stated condition by explicit computation on this
concrete case; when the statement allows several answers, any output meeting all stated conditions is
acceptable. The first line of your answer must be exactly VALID or INVALID; then give your verification.

# Statement

{req}

# Input

```
{inp}
```

# Output

```
{out}
```
"""

AUDIT_INPUT = """You are refereeing a hidden test of a competitive programming problem. A program failed on the test
input below (it crashed, or the input was too long to re-judge its output). Decide, from the input section of the
statement alone, whether this input satisfies every stated constraint and guarantee (types, ranges, counts,
formats). Only the beginning and the end of the input are shown; judge what is shown. The first line of your answer
must be exactly LEGAL or ILLEGAL; then name the constraint you checked or the one that is broken.

# Statement

{req}

# Input ({size} characters, {lines} lines)

Beginning:
```
{head}
```

End:
```
{tail}
```

# What the program reported

```
{err}
```
"""


def _audit_input(ref, req: str, inp: str, err: str) -> tuple[str, str]:
    answer = ref(AUDIT_INPUT.format(req=req, size=len(inp), lines=inp.count("\n"), head=inp[:3000],
                                    tail=inp[-500:], err=err[-1500:]))  # fmt: skip
    first = next((ln.strip() for ln in answer.splitlines() if ln.strip()), "")
    return (
        "ILLEGAL_TEST" if first.startswith("ILLEGAL") else "LEGAL_TEST" if first.startswith("LEGAL") else "UNCLEAR"
    ), answer


JUDGE_PROGRAM = """Below are a competitive programming problem and a Python 3 program written for it. Decide whether
the program is correct: it must print an accepted answer on every valid input, within the limits. The first line
of your answer must be exactly YES (the program is correct) or NO; then explain.

# Problem

{req}

# Program

```python
{code}
```
"""


def baseline(a) -> None:
    """C9: the examples-only check and the same model judging the program, for every sample with code."""
    ask = asker(a.model, 32000)

    def one(f: Path) -> None:
        out = f.parent / f"base-{f.name}"
        d = json.loads(f.read_text())
        program = d["rounds"][0]["program"]
        if out.exists() or not program.strip():
            return
        pdir = Path(a.data) / "problems" / d["pid"]
        req, examples = requirement(json.loads((pdir / "problem.json").read_text()))
        ex = [verdict(pdir, program, i, o) for i, o in examples]
        answer = ask(JUDGE_PROGRAM.format(req=req, code=program))
        first = next((ln.strip() for ln in answer.splitlines() if ln.strip()), "")
        rec = {"examples": bool(ex) and all(v == "OK" for v in ex), "example_verdicts": ex,
               "judge": first.upper().startswith("YES"), "judge_answer": answer[-2000:]}  # fmt: skip
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        print(d["pid"], d["sample"], rec["examples"], rec["judge"], flush=True)

    files = sorted(Path(a.out).glob("*/s[0-9]*.json"))
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(one, files))
    print(json.dumps(USAGE), flush=True)


def audit(a) -> None:
    """C4: an outside referee re-judges every sample where the io mode and the hidden tests disagree."""
    ref = asker(a.referee, 32000)
    for f in sorted(Path(a.out).glob("*/s[0-9]*.json")):
        d = json.loads(f.read_text())
        r0 = d["rounds"][0]
        if not r0["program"].strip() or (f.parent / f"audit-{f.name}").exists():
            continue
        alarm = r0["conclusion"] == IO.FAIL and r0["truth"]["correct"]
        escape = r0["conclusion"] == IO.PASS and not r0["truth"]["correct"]
        if not (alarm or escape):
            continue
        pdir = Path(a.data) / "problems" / d["pid"]
        req, _ = requirement(json.loads((pdir / "problem.json").read_text()))
        if alarm:
            inp, out = r0["counterexample"]["input"], r0["counterexample"]["output"]
        else:
            name = r0["truth"].get("first_fail")
            v = r0["truth"]["verdicts"]
            if not name or (v.get("WA", 0) == 0 and v.get("RE", 0) == 0):
                continue  # timeouts need no referee
            inp = (pdir / "tests" / name).read_text()
            with tempfile.TemporaryDirectory(prefix="cfa-") as tmp:
                t = Path(tmp)
                (t / "sol.py").write_text(r0["program"])
                (t / "in.txt").write_text(inp)
                with open(t / "in.txt") as fin:
                    _, raw = _box(t, ["/usr/bin/sh", "-c", "/usr/bin/python3 -I sol.py 2>&1 | tail -c 20000"], stdin=fin,
                                  timeout=20)  # fmt: skip
            out = raw.decode(errors="replace")
            if len(inp) > 3000 or v.get("WA", 0) == 0:  # a crash, or too long to re-judge: is the test itself legal?
                verdict, answer = _audit_input(ref, req, inp, out)
                rec = {"kind": "escape", "verdict": verdict, "input": inp[:3000], "answer": answer[-3000:]}
                (f.parent / f"audit-{f.name}").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
                print(d["pid"], d["sample"], "escape", verdict, flush=True)
                continue
        answer = ref(AUDIT.format(req=req, inp=inp.rstrip(), out=out[:6000].rstrip()))
        first = next((ln.strip() for ln in answer.splitlines() if ln.strip()), "")
        verdict = "INVALID" if first.startswith("INVALID") else "VALID" if first.startswith("VALID") else "UNCLEAR"
        rec = {"kind": "alarm" if alarm else "escape", "verdict": verdict, "input": inp, "output": out[:6000],
               "answer": answer[-3000:]}  # fmt: skip
        (f.parent / f"audit-{f.name}").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
        print(d["pid"], d["sample"], rec["kind"], verdict, flush=True)


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
    r.add_argument("--no-large", action="store_true")
    r.add_argument("--pass-with-dismissed", action="store_true")
    r.add_argument("--workers", type=int, default=4)
    au = sub.add_parser("audit")
    au.add_argument("--data", required=True)
    au.add_argument("--out", required=True)
    au.add_argument("--referee", default="claude-opus-5-5")
    bl = sub.add_parser("baseline")
    bl.add_argument("--data", required=True)
    bl.add_argument("--out", required=True)
    bl.add_argument("--model", default=MODEL)
    bl.add_argument("--workers", type=int, default=4)
    sm = sub.add_parser("summary")
    sm.add_argument("--out", required=True)
    sm.add_argument("--problems")
    a = ap.parse_args()
    if a.cmd == "audit":
        audit(a)
        return
    if a.cmd == "baseline":
        baseline(a)
        return
    if a.cmd == "summary":
        probs = [x.strip() for x in Path(a.problems).read_text().splitlines() if x.strip()] if a.problems else None
        print(json.dumps(summary(Path(a.out), probs), ensure_ascii=False, indent=1))
        return
    config = json.loads((Path(__file__).resolve().parents[1] / "cross-validate" / "config.json").read_text())
    s = IO.Settings.load(config)
    if not a.repair:
        s.repair_rounds = 0
    s.pass_with_dismissed = s.pass_with_dismissed or a.pass_with_dismissed
    pids = [x.strip() for x in Path(a.problems).read_text().splitlines() if x.strip()]
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(lambda p: run_problem(p, a, s), pids))
    print(json.dumps(USAGE))


if __name__ == "__main__":
    main()
