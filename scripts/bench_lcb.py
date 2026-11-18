"""LiveCodeBench evaluation of cross-validation (BENCHMARK_LCB.md). Not part of the plugin.

The plugin's own workflow functions do every step (README 算法 1, 细则 11.12); this script only supplies the
model calls, the problems and the samples. Data and results stay outside the repository.

    bench_lcb.py prep <qid>            split, propositions, review and io alignment from the statement alone
    bench_lcb.py check <qid>           run the frozen checks on every graded sample of the problem
    bench_lcb.py judge <qid>           baseline: the same model asked whether each sample is correct
    bench_lcb.py summary <ids-file>    the numbers of L1 and L7 over the problems listed in the file
    bench_lcb.py split                 write dev.txt and heldout.txt (L2)

Environment: CV_LCB_DATA (problems/, gen/, grades/), CV_LCB_OUT (results), CV_BENCH_KEY_FILE.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CV_LCB_DATA", Path.home() / ".claude/jobs/5573734e/tmp/lcb"))
OUT = Path(os.environ.get("CV_LCB_OUT", DATA / "cv"))
MODEL = "claude-haiku-5-5"
MAX_TOKENS = 32000
MULTI_ANSWER = ("abc396_e", "abc397_d", "abc398_f", "arc185_c", "arc190_a", "arc191_c")  # L2
SITE = Path.home() / ".local/lib/python3.12/site-packages"
DIST = "/usr/local/lib/python3.12/dist-packages"

os.environ.setdefault("CV_CHRYS_HOME", str(OUT / "home"))
sys.path.insert(0, str(ROOT / "cross-validate"))
import cv_flow as FLOW  # noqa: E402
import cv_state as S  # noqa: E402

LOG_LOCK = threading.Lock()

# The wrapper for LeetCode problems: the same imports LiveCodeBench's own harness puts before a solution.
CALL = """import json, sys
PRE = ("from string import *\\nfrom re import *\\nfrom datetime import *\\nfrom collections import *\\nfrom heapq import *\\n"
       "from bisect import *\\nfrom copy import *\\nfrom math import *\\nfrom random import *\\nfrom statistics import *\\n"
       "from itertools import *\\nfrom functools import *\\nfrom operator import *\\nfrom io import *\\nfrom sys import *\\n"
       "from json import *\\nfrom builtins import *\\nfrom typing import *\\n"
       "import string, re, datetime, collections, heapq, bisect, copy, math, random, statistics, itertools, functools, operator, io, sys, json\\n"
       "sys.setrecursionlimit(50000)\\n")
sys.set_int_max_str_digits(0)
g = {"__name__": "sol"}
exec(compile(PRE + open("main.py").read(), "main.py", "exec"), g)
args = [json.loads(line) for line in sys.stdin.read().split("\\n") if line.strip()]
sys.stdout.write(json.dumps(getattr(g["Solution"](), sys.argv[1])(*args)))
"""


def log(event: dict) -> None:
    with LOG_LOCK, open(OUT / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%H:%M:%S"), **event}, ensure_ascii=False) + "\n")


def ask(prompt: str, tag: str) -> str:
    """One fresh session without tools. Only API and network errors are retried, and each retry is logged (L5)."""
    key = Path(os.environ.get("CV_BENCH_KEY_FILE", Path.home() / ".config/cv-bench/anthropic.key")).read_text().strip()
    body = json.dumps(
        {"model": MODEL, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": prompt}]}
    ).encode()
    for attempt in range(8):
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
            "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})  # fmt: skip
        try:
            with urllib.request.urlopen(req, timeout=900) as r:  # noqa: S310
                data = json.loads(r.read())
            u = data.get("usage", {})
            log(
                {
                    "call": tag,
                    "in": u.get("input_tokens"),
                    "out": u.get("output_tokens"),
                    "stop": data.get("stop_reason"),
                }
            )
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            code = getattr(e, "code", None)
            log({"retry": tag, "attempt": attempt, "error": f"{type(e).__name__} {code}"})
            if code is not None and code < 500 and code not in (429, 408):
                raise
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("API unavailable")


def problem(qid: str) -> dict:
    return json.loads((DATA / "problems" / qid / "problem.json").read_text(encoding="utf-8"))


def requirement(p: dict) -> str:
    """L5: the statement; for LeetCode also the class skeleton the main task was given."""
    if p["starter_code"]:
        return f"{p['question_content']}\n\n```python\n{p['starter_code']}\n```\n"
    return p["question_content"]


def io_settings(p: dict) -> dict:
    binds = ["--tmpfs", DIST]
    for name in ("numpy", "numpy.libs", "sortedcontainers"):
        if (SITE / name).is_dir():
            binds += ["--ro-bind", str(SITE / name), f"{DIST}/{name}"]
    if p["testtype"] == "functional":
        fn = p["metadata"]["func_name"]
        return {
            "command": f"python3 -I cv_call.py {fn}",
            "protocol": f"输入文本每行一个 JSON 值，依次是方法 `{fn}` 除 self 以外的各个参数；"
            "输出文本是这个方法的返回值，写成一行 JSON（元组写成列表）。",
            "box_args": binds,
        }
    return {"command": "python3 -I main.py", "box_args": binds}


def setup_home() -> None:
    home = Path(os.environ["CV_CHRYS_HOME"])
    wf = home / "workflows" / "cross-validate"
    lock = home / ".setup.lock"
    home.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as f:
        import fcntl

        fcntl.flock(f, fcntl.LOCK_EX)
        if not wf.exists():
            shutil.copytree(ROOT / "cross-validate", wf, ignore=shutil.ignore_patterns("__pycache__"))
            digest = S.folder_digest(wf)
            S.write_json(S.ensure_private(S.cv_home()) / "install.json", {"workflow_digest": digest})


def stage(state: dict, name: str, make, check) -> dict:
    """The workflow's stage pattern: prompt, answer, check; at most one redo (cross-validate.py)."""
    p = make(state)
    if p is None:
        return state
    state, redo = check(state, ask(p, f"{name}:1"), 1)
    if redo:
        state, _ = check(state, ask(redo, f"{name}:2"), 2)
    return state


def prep(qid: str) -> None:
    """Everything before any sample is seen (L5 一次准备，多份共用)."""
    done = OUT / "prep" / f"{qid}.json"
    if done.exists():
        return
    setup_home()
    p = problem(qid)
    w = OUT / "w" / qid
    shutil.rmtree(w, ignore_errors=True)
    w.mkdir(parents=True)
    if p["testtype"] == "functional":
        (w / "cv_call.py").write_text(CALL, encoding="utf-8")
    pdir = S.ensure_private(S.project_dir(w))
    S.write_json(pdir / "project.json", {"project": str(w), "settings": {"io": io_settings(p)}})
    config = FLOW.load_config()
    t = time.time()
    state = FLOW.intake(requirement(p), str(w), S.global_workflow_dir())
    state = FLOW.after_implement(state)
    for name in ("split", "propose", "review"):
        state = stage(state, name, getattr(FLOW, f"{name}_prompt"), getattr(FLOW, f"{name}_check"))
        if state.get("stopped"):
            break
    if not state.get("stopped"):
        state = stage(state, "io_align", FLOW.io_align_prompt, lambda s, a, n: FLOW.io_align_check(s, a, n, config))
    S.ensure_private(OUT / "prep")
    S.write_json(done, {"qid": qid, "state": state, "seconds": round(time.time() - t)})
    log({"prep": qid, "seconds": round(time.time() - t), "stopped": state.get("stopped")})


def samples(qid: str) -> list[Path]:
    return sorted(
        f for f in (DATA / "gen").glob(f"{qid}-{MODEL}-*.py") if (DATA / "grades" / f"{f.stem}.json").exists()
    )


def check(qid: str) -> None:
    """Each sample against the frozen checks (L5); the run directory is copied, never re-aligned."""
    info = S.read_json(OUT / "prep" / f"{qid}.json")
    if info is None:
        return
    config = FLOW.load_config()
    base = info["state"]
    S.ensure_private(OUT / "check")
    for f in samples(qid):
        out = OUT / "check" / f"{f.stem}.json"
        if out.exists():
            continue
        t = time.time()
        if base.get("stopped") or base.get("invalid"):
            S.write_json(out, {"sample": f.stem, "conclusion": "无结论", "reason": base.get("stopped") or "无效"})
            continue
        w = OUT / "w" / f"{qid}-{f.stem.rsplit('-', 1)[1]}"
        shutil.rmtree(w, ignore_errors=True)
        shutil.copytree(OUT / "w" / qid, w)
        shutil.copy(f, w / "main.py")
        run = Path(base["run"])
        mine = run.parent / f"{run.name}-{f.stem.rsplit('-', 1)[1]}"
        shutil.rmtree(mine, ignore_errors=True)
        shutil.copytree(run, mine)
        state = {**json.loads(json.dumps(base)), "run": str(mine), "w": str(w)}
        state = FLOW.guarded("run_checks", lambda s: FLOW.run_checks(s, config), state)
        FLOW.report(state, config)
        rep = S.read_json(mine / "report.json") or {}
        frozen = S.read_json(mine / "align" / "checks.frozen.json") or {}
        res = S.read_json(mine / "results.json") or {}
        S.write_json(out, {
            "sample": f.stem,
            "conclusion": rep.get("conclusion"),
            "reason": rep.get("conclusion_reason"),
            "without_review": rep.get("conclusion_without_review"),
            "usable": (frozen.get("io") or {}).get("usable", False),
            "results": {pid: {k: r.get(k) for k in ("result", "reason", "vacuous")} for pid, r in res.get("propositions", {}).items()},
            "derived_props": derived_props(mine, frozen),
            "run": str(mine),
            "seconds": round(time.time() - t),
        })  # fmt: skip
        log({"check": f.stem, "conclusion": rep.get("conclusion"), "seconds": round(time.time() - t)})


def derived_props(run: Path, frozen: dict) -> list[str]:
    """Propositions that read a value derive() computes (L7: the share of the gain from the solver part)."""
    import cv_io as IO

    names = set((frozen.get("io") or {}).get("derived", []))
    props = S.read_json(run / "propositions.json") or {}
    return [p["id"] for p in props.get("propositions", []) if not p.get("problems") and IO.names_of(p["tree"]) & names]


JUDGE = """下面是一道编程题和一份 Python 程序。请判断这份程序能否通过这道题的全部隐藏测试（包括时间限制）。
第一行只回答 YES 或 NO，之后可以简要说明理由。

# 题目

{statement}

# 程序

```python
{code}
```
"""


def judge(qid: str) -> None:
    """L6 直接判断: the same model, a fresh session without tools."""
    p = problem(qid)
    S.ensure_private(OUT / "judge")
    for f in samples(qid):
        out = OUT / "judge" / f"{f.stem}.json"
        if out.exists():
            continue
        answer = ask(JUDGE.format(statement=requirement(p), code=f.read_text(encoding="utf-8")), f"judge:{f.stem}")
        first = answer.strip().splitlines()[0].strip().upper() if answer.strip() else ""
        S.write_json(out, {"sample": f.stem, "yes": first.startswith("YES"), "answer": answer[:2000]})


def split() -> None:
    """L2: sha256("lcb-split-v1:" + id) order; the first 40 are dev, the rest held out."""
    ids = sorted(d.name for d in (DATA / "problems").iterdir() if d.name not in MULTI_ANSWER)
    order = sorted(ids, key=lambda q: hashlib.sha256(f"lcb-split-v1:{q}".encode()).hexdigest())
    (OUT / "dev.txt").write_text("\n".join(order[:40]) + "\n", encoding="utf-8")
    (OUT / "heldout.txt").write_text("\n".join(order[40:]) + "\n", encoding="utf-8")


def _bootstrap(rows: list[dict], stat, n: int = 2000, seed: int = 20261118) -> tuple[float, float] | None:
    by_q: dict[str, list[dict]] = {}
    for r in rows:
        by_q.setdefault(r["qid"], []).append(r)
    qs = sorted(by_q)
    rng = random.Random(seed)  # noqa: S311 — resampling, not security
    vals = []
    for _ in range(n):
        pick = [x for q in (rng.choice(qs) for _ in qs) for x in by_q[q]]
        v = stat(pick)
        if v is not None:
            vals.append(v)
    if len(vals) < n // 2:
        return None
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def _delta(rows: list[dict], key: str) -> float | None:
    passed = [r for r in rows if r[key]]
    if not passed or not rows:
        return None
    return sum(r["ok"] for r in passed) / len(passed) - sum(r["ok"] for r in rows) / len(rows)


def summary(ids_file: str) -> None:
    ids = [q for q in Path(ids_file).read_text(encoding="utf-8").split() if q]
    rows = []
    for q in ids:
        for f in samples(q):
            g = S.read_json(DATA / "grades" / f"{f.stem}.json")
            c = S.read_json(OUT / "check" / f"{f.stem}.json")
            if g is None or c is None or "NO_CODE" in g["verdicts"]:
                continue
            j = S.read_json(OUT / "judge" / f"{f.stem}.json") or {}
            rows.append({
                "qid": q, "sample": f.stem, "ok": g["ok"], "logic_ok": g["logic_ok"],
                "cv": c["conclusion"] == "比对通过", "cv_failed": c["conclusion"] == "比对未通过",
                "usable": c.get("usable", False), "judge": j.get("yes"), "examples": examples_pass(q, f, g),
            })  # fmt: skip
    if not rows:
        print("no rows")
        return
    usable = [r for r in rows if r["usable"]]

    def block(name: str, rs: list[dict]) -> None:
        if not rs:
            print(f"{name}: none")
            return
        n, ok = len(rs), sum(r["ok"] for r in rs)
        cv = [r for r in rs if r["cv"]]
        wrong = [r for r in rs if not r["ok"]]
        right = [r for r in rs if r["ok"]]
        print(f"\n== {name}: {n} samples, {len({r['qid'] for r in rs})} problems, p_m = {ok / n:.3f}")
        if cv:
            prec = sum(r["ok"] for r in cv) / len(cv)
            ci = _bootstrap(rs, lambda x: _delta(x, "cv"))
            print(f"  cv passed {len(cv)}  precision {prec:.3f}  delta {prec - ok / n:+.3f}  95% CI {ci}")
        print(f"  k (wrong caught by 比对未通过) {sum(r['cv_failed'] for r in wrong)}/{len(wrong)}")
        print(f"  false alarms (right but not passed) {sum(not r['cv'] for r in right)}/{len(right)}")
        print(f"  no conclusion {sum(not r['cv'] and not r['cv_failed'] for r in rs)}/{n}")
        print(f"  false approvals (passed but wrong) {sum(r['cv'] and not r['ok'] for r in rs)}/{n}")
        for key in ("examples", "judge"):
            got = [r for r in rs if r[key]]
            if got:
                print(f"  baseline {key}: passed {len(got)} precision {sum(r['ok'] for r in got) / len(got):.3f}")
        ex = [r for r in rs if r["examples"]]
        exc = [r for r in ex if r["cv"]]
        if ex and exc:
            d = sum(r["ok"] for r in exc) / len(exc) - sum(r["ok"] for r in ex) / len(ex)
            print(f"  among samples passing the examples: cv precision - examples precision = {d:+.3f}")

    block("usable problems (main, L7)", usable)
    block("all problems", rows)
    print(f"\nusable problems {len({r['qid'] for r in usable})}/{len({r['qid'] for r in rows})}")


def examples_pass(qid: str, f: Path, g: dict) -> bool:
    """L6 示例: the public tests are the statement's examples and come first in the graded list, so a failed
    example would be the first failure recorded."""
    n = len(problem(qid)["public"])
    return not any(int(b[0].split(".")[0]) < n for b in g.get("first_bad", []))


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    OUT.mkdir(parents=True, exist_ok=True)
    {"prep": prep, "check": check, "judge": judge, "summary": summary, "split": split}[cmd](*args)
