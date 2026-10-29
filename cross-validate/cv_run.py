"""The checker: run the frozen checks and judge every proposition (README 第 11–13 节; 算法1 第7行)."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import cv_monitor as M
import cv_state as S

TAIL = 2000


def _tail(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", "replace")[-TAIL:]


def run_command(command: str, cwd: Path, env: dict[str, str], timeout: float) -> dict:
    """`sh -c` in W with a new process group, killed as a whole on timeout."""
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            ["sh", "-c", command],
            cwd=cwd,
            env={**os.environ, **env},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as e:
        return {"exit": None, "timeout": False, "error": f"无法启动：{e}", "stdout": "", "stderr": "", "seconds": 0.0}
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        timed_out = True
    return {
        "exit": None if timed_out else proc.returncode,
        "timeout": timed_out,
        "error": "超时" if timed_out else None,
        "stdout": _tail(out),
        "stderr": _tail(err),
        "seconds": round(time.monotonic() - start, 3),
    }


def read_trace(path: Path, max_states: int) -> tuple[list[dict], bool]:
    """细则 12.1: JSON Lines, one state per line. Invalid lines are an alignment failure."""
    states: list[dict] = []
    if not path.exists():
        return states, False
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            if len(states) >= max_states:
                return states, True
            try:
                state = json.loads(line)
            except json.JSONDecodeError as e:
                raise M.AlignError(f"轨迹第 {n} 行不是 JSON：{e}") from e
            if not isinstance(state, dict):
                raise M.AlignError(f"轨迹第 {n} 行应是对象")
            states.append(state)
    return states, False


def _counterexample(states: list[dict], at: int) -> dict:
    """细则 13.4: the last 5 states up to the decisive one, and the whole action sequence."""
    upto = states[: at + 1]
    out = {"state": at, "last_states": upto[-5:]}
    actions = [s.get("动作") for s in upto if "动作" in s]
    if actions:
        out["actions"] = actions
    return out


def run_checks(run: Path, w: Path, props: dict[str, dict], frozen: dict, entities: set[str], limits: dict) -> dict:
    """Run every frozen check (细则 11.3, 11.4) and return results.json (细则 13.6)."""
    run, w = Path(run), Path(w)
    traces = S.ensure_private(run / "traces")
    align_dir = run / "align"
    deadline = time.monotonic() + limits["budget"]
    results: list[dict] = []
    for check in frozen["checks"]:
        prop = props[check["proposition"]]
        rec = {**check, "runs_detail": [], "result": None, "reason": None, "counterexample": None}
        results.append(rec)
        if time.monotonic() >= deadline:
            rec.update(result=M.ALIGN_FAILED, reason="预算用尽，未运行")
            continue
        base_env = {"CV_ALIGN": str(align_dir), "CV_W": str(w)}
        if check["kind"] == "command":
            out = run_command(
                check["command"], w, base_env, min(check["timeout"], max(1.0, deadline - time.monotonic()))
            )
            rec["runs_detail"].append(out)
            if out["exit"] == 0:
                rec["result"] = M.PASSED
            elif out["exit"] == 1:
                rec.update(result=M.CHECK_FAILED, counterexample={"stdout": out["stdout"]})
            else:
                why = out["error"] or f"退出码 {out['exit']}"
                rec.update(
                    result=M.ALIGN_FAILED,
                    reason=f"检查命令{why}（细则 11.3）",
                    counterexample={"stderr": out["stderr"]},
                )
            continue
        verdicts: list[str] = []
        matched = 0
        for i in range(check["runs"]):
            if time.monotonic() >= deadline:
                rec["reason"] = f"预算用尽，只运行了 {i} 次"
                break
            path = traces / f"c{check['index']}-r{i}.jsonl"
            env = {**base_env, "CV_RUN": str(i), "CV_TRACE": str(path)}
            out = run_command(check["command"], w, env, min(check["timeout"], max(1.0, deadline - time.monotonic())))
            detail = {**out, "run": i, "trace": path.name}
            rec["runs_detail"].append(detail)
            if path.exists():
                detail["trace_sha256"] = S.sha256_file(path)
            if out["exit"] is None and not out["timeout"]:
                detail.update(result=M.ALIGN_FAILED, reason=f"检查命令{out['error']}")
                verdicts.append(M.ALIGN_FAILED)
                continue
            if out["exit"] not in (0, None):
                # 细则 12.6: a crash of the program or of the check script fails the check.
                detail["result"] = M.CHECK_FAILED
                verdicts.append(M.CHECK_FAILED)
                if rec["counterexample"] is None:
                    rec["counterexample"] = {"run": i, "exit": out["exit"], "stderr": out["stderr"]}
                continue
            try:
                states, truncated = read_trace(path, limits["max_states"])
                if prop.get("pattern") == "从出发":
                    if not M.scenario_matches(prop["tree"], states, entities):
                        detail["result"] = "不相符"
                        continue
                    matched += 1
                judged = M.judge(prop["tree"], states, entities)
            except M.AlignError as e:
                detail["result"] = M.ALIGN_FAILED
                detail["reason"] = str(e)
                verdicts.append(M.ALIGN_FAILED)
                continue
            detail.update(result=judged["result"], state=judged["state"], truncated=truncated or out["timeout"])
            verdicts.append(judged["result"])
            if judged["result"] in (M.VIOLATED, M.CHECK_FAILED, M.PRESUMED_FALSE):
                cex = rec["counterexample"]
                worse = cex is None or M.ORDER[judged["result"]] < M.ORDER[cex.get("result", M.PASSED)]
                if worse:
                    rec["counterexample"] = {
                        "run": i,
                        "result": judged["result"],
                        **_counterexample(states, judged["state"]),
                    }
        if prop.get("pattern") == "从出发" and matched == 0 and not any(v == M.CHECK_FAILED for v in verdicts):
            rec.update(result=M.ALIGN_FAILED, reason="没有一条轨迹的动作序列与句子相符（细则 12.5）")
            continue
        if not verdicts:
            rec.update(result=M.ALIGN_FAILED, reason=rec["reason"] or "没有可判定的运行")
            continue
        rec["result"] = M.worst(verdicts)
        if rec["result"] == M.ALIGN_FAILED and not rec["reason"]:
            rec["reason"] = next(d.get("reason") for d in rec["runs_detail"] if d.get("result") == M.ALIGN_FAILED)
    by_prop: dict[str, dict] = {}
    for pid in props:
        mine = [r for r in results if r["proposition"] == pid]
        if pid in frozen["align_failures"]:
            by_prop[pid] = {"result": M.ALIGN_FAILED, "reason": frozen["align_failures"][pid]}
            continue
        worst = M.worst([r["result"] for r in mine])
        reasons = [r["reason"] for r in mine if r["result"] == worst and r["reason"]]
        cexs = [r["counterexample"] for r in mine if r["result"] == worst and r["counterexample"]]
        by_prop[pid] = {
            "result": worst,
            "reason": reasons[0] if reasons else None,
            "counterexample": cexs[0] if cexs else None,
        }
    return {"checks": results, "propositions": by_prop}
