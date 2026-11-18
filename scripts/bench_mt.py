"""BENCHMARK_MT.md: the multi-turn experiment, checked in accumulation mode.

The conversations are generated outside the repository (converse.py). This script lays them out so that
bench_lcb.py's `check` and `summary` work unchanged: a sample `<pair>-t<turn>-claude-haiku-5-5-<session>.py` is
the program after that turn, its problem is the easy version for turn 1 and the hard one for turn 3 (M4).

usage (CV_LCB_DATA is the layout directory, CV_LCB_OUT its cv directory, CV_BENCH_KIND=cf):
  bench_mt.py layout <mt-dir>      programs, problem links, the scripts' messages
  bench_mt.py prep <pair>-t<1|3>   ledger through the hook's function, consolidation, then the usual stages (M5)
  bench_mt.py follow               M6: did consolidation and the main task follow the correction
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("CV_BENCH_KIND", "cf")
import bench_lcb as B  # noqa: E402  (its globals read the environment)

FLOW, S = B.FLOW, B.S
import cv_ledger as L  # noqa: E402

TURNS = {1: ("m1",), 3: ("m1", "m2", "m3")}  # M5: checks after turn 1 and turn 3


def layout(mt: str) -> None:
    src = Path(mt)
    data = B.DATA
    for d in ("gen", "problems", "scripts"):
        (data / d).mkdir(parents=True, exist_ok=True)
    for f in sorted((src / "gen").glob("*-mt-*.json")):
        conv = json.loads(f.read_text(encoding="utf-8"))
        pair, session = f.name.split("-mt-")[0], f.stem.rsplit("-", 1)[1]
        for t, version in ((1, "easy"), (3, "hard")):
            qid = f"{pair}-t{t}"
            turn = conv["turns"][t - 1] if len(conv["turns"]) >= t else None
            code = (turn or {}).get("code") or ""
            (data / "gen" / f"{qid}-{B.MODEL}-{session}.py").write_text(code, encoding="utf-8")
            link = data / "problems" / qid
            if not link.exists():
                link.symlink_to(src / "problems" / conv["pair"][version])
    for f in sorted((src / "gen").glob("*-full-*.py")) + sorted((src / "gen").glob("*-merged-*.py")):
        pair, arm, session = f.stem.split("-")
        qid = f"{pair}-{arm}"  # M4 single-turn controls, graded on the hard version
        shutil.copy(f, data / "gen" / f"{qid}-{B.MODEL}-{session}.py")
        link = data / "problems" / qid
        if not link.exists():
            hard = json.loads((src / "scripts" / f"{pair}.json").read_text(encoding="utf-8"))["pair"]["hard"]
            link.symlink_to(src / "problems" / hard)
    for f in sorted((src / "scripts").glob("*.json")):
        shutil.copy(f, data / "scripts" / f.name)
    print(len(list((data / "gen").glob("*.py"))), "programs")


def prep(qid: str) -> None:
    """M5: the three messages go in through cv_ledger.append_message, as the record hook does; then the launcher's
    preprocessing, consolidation, and the stages of bench_lcb.prep. One preparation per pair and turn."""
    done = B.OUT / "prep" / f"{qid}.json"
    if done.exists():
        return
    B.setup_home()
    pair, t = re.fullmatch(r"(.+)-t(\d)", qid).groups()
    script = json.loads((B.DATA / "scripts" / f"{pair}.json").read_text(encoding="utf-8"))
    w = B.OUT / "w" / qid
    shutil.rmtree(w, ignore_errors=True)
    w.mkdir(parents=True)
    shutil.rmtree(S.project_dir(w), ignore_errors=True)  # a fresh ledger: an earlier attempt must not add messages
    pdir = S.ensure_private(S.project_dir(w))
    io = {"command": "python3 -I main.py"}
    S.write_json(pdir / "project.json", {"project": str(w), "settings": {"io": io}})
    for key in TURNS[int(t)]:
        L.append_message(w, f"mt-{qid}", script["messages"][key], False)
    messages = L.read_messages(L.ledger_file(w))
    run = S.create_run(w)
    info = {
        "mode": FLOW.MODE_ACCUM,
        "trigger": "评测（BENCHMARK_MT.md M5）",
        "project": str(w),
        "snapshot": str(w),
        "ledger": L.current_id(w),
        "first_message": messages[0]["id"],
        "last_message": messages[-1]["id"],
        "started": B._now() if hasattr(B, "_now") else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "io": io,
        "notes": [],
    }
    S.write_json(run / "run.json", info)
    processed = L.preprocess(messages)
    with open(run / "ledger.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(m, ensure_ascii=False) + "\n" for m in processed)
    config = FLOW.load_config()
    t0 = time.time()
    state = FLOW.intake(FLOW.RUN_PREFIX + str(run), str(w), S.global_workflow_dir())
    state = B.stage(state, "consolidate", FLOW.consolidate_prompt, FLOW.consolidate_check)
    if not state.get("stopped"):
        for name in ("split", "propose", "review"):
            state = B.stage(state, name, getattr(FLOW, f"{name}_prompt"), getattr(FLOW, f"{name}_check"))
            if state.get("stopped"):
                break
    if not state.get("stopped"):
        state = B.stage(state, "io_align", FLOW.io_align_prompt, lambda s, a, n: FLOW.io_align_check(s, a, n, config))
    S.ensure_private(B.OUT / "prep")
    S.write_json(done, {"qid": qid, "state": state, "seconds": round(time.time() - t0)})
    B.log({"prep": qid, "seconds": round(time.time() - t0), "stopped": state.get("stopped")})


def _norm(text: str) -> str:
    """M6: spaces and thousands separators inside numbers do not count."""
    return re.sub(r"(?<=\d)[ ,  ](?=\d{3}\b)", "", text)


def _has(text: str, forms: list[str]) -> bool:
    t = _norm(text)
    for f in forms:
        f = _norm(f)
        pat = (r"(?<![\w.])" + re.escape(f) + r"(?![\w.])") if re.fullmatch(r"[\w.]+", f) else re.escape(f)
        if re.search(pat, t):
            return True
    return False


def follow() -> None:
    """M6: the third turn's requirement uses a correct form and no wrong one; and the main task's code."""
    rows = []
    for f in sorted((B.OUT / "prep").glob("*-t3.json")):
        info = S.read_json(f)
        pair = info["qid"][: -len("-t3")]
        script = json.loads((B.DATA / "scripts" / f"{pair}.json").read_text(encoding="utf-8"))
        err = script["messages"]["error"]
        run = Path(info["state"]["run"])
        req_file = run / "requirement.txt"
        req = req_file.read_text(encoding="utf-8") if req_file.exists() else None
        if req is not None:  # the sentences and the original words they cite
            entries = (S.read_json(run / "requirement.json") or {}).get("entries", [])
            req += "\n" + "\n".join(q["text"] for e in entries for q in e.get("quotes", []))
        hard = B.cf_statement(B.problem(info["qid"]))
        rows.append({
            "pair": pair,
            "excluded": _has(hard, err["wrong"]),  # M6: the wrong form already occurs in the hard statement
            "stopped": info["state"].get("stopped"),
            "correct": req is not None and _has(req, err["correct"]),
            "wrong": req is not None and _has(req, err["wrong"]),
        })  # fmt: skip
    counted = [r for r in rows if not r["excluded"] and not r["stopped"]]
    ok = [r for r in counted if r["correct"] and not r["wrong"]]
    print(
        f"consolidation followed the correction: {len(ok)}/{len(counted)}"
        f" (excluded {sum(r['excluded'] for r in rows)}, stopped {sum(bool(r['stopped']) for r in rows)})"
    )
    for r in counted:
        if r not in ok:
            print("  ", r)  # M6: each mismatch is looked at by hand


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"layout": layout, "prep": prep, "follow": follow}[cmd](*args)
