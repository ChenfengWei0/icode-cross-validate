"""The checker: run the frozen checks and judge every proposition (README 第 11–13 节; 算法1 第7行)."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv_monitor as M
import cv_state as S

EXEC = Path(__file__).resolve().parent / "cv_exec.py"

LINE_MAX = 1 << 20
GRACE = 10

VACUOUS = "前件从未成立，实际没有被检验"
PREFLIGHT_SECONDS = 300


def run_command(command: str, cwd: Path, env: dict[str, str], timeout: float) -> dict:
    """细则 5.5: `sh -c` in W under the wrapper, which leaves no process behind and keeps only tails."""
    start = time.monotonic()
    failed = {"exit": None, "timeout": False, "stdout": "", "stderr": ""}
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", str(EXEC), str(timeout), "--", "sh", "-c", command],
            cwd=cwd,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **env},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError) as e:
        return {**failed, "error": f"无法启动：{e}", "seconds": 0.0}
    try:
        out, _ = proc.communicate(timeout=timeout + GRACE)
        result = json.loads(out.decode("utf-8", "replace"))
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=GRACE)
        result = {**failed, "timeout": True, "error": "超时"}
    except (json.JSONDecodeError, ValueError):
        result = {**failed, "error": f"包装进程异常（退出码 {proc.returncode}）"}
    result["seconds"] = round(time.monotonic() - start, 3)
    return result


def _no_constant(name: str):
    raise ValueError(f"{name} 不是 JSON 值")


def read_trace(
    path: Path, max_states: int, max_bytes: int = 100 << 20, partial_ok: bool = False
) -> tuple[list[dict], bool]:
    """细则 12.1, 12.6: JSON Lines from a regular file, one state per line.

    Returns (states, truncated). A bad line is an alignment failure, except an unterminated last line
    of a run that timed out (`partial_ok`), which is dropped.
    """
    states: list[dict] = []
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return states, False
    if not stat.S_ISREG(st.st_mode):
        raise M.AlignError("轨迹文件不是普通文件（细则 12.6）")
    read = 0
    with open(path, "rb") as f:
        n = 0
        while True:
            raw = f.readline(LINE_MAX + 1)
            if not raw:
                return states, False
            n += 1
            read += len(raw)
            if read > max_bytes:
                return states, True
            if not raw.strip():
                continue
            if len(states) >= max_states:
                return states, True
            last = not raw.endswith(b"\n")
            try:
                if len(raw) > LINE_MAX:
                    raise M.AlignError(f"轨迹第 {n} 行超过 1 MB")
                state = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
            except (UnicodeDecodeError, ValueError, RecursionError, M.AlignError) as e:
                if last and partial_ok:
                    return states, True
                if isinstance(e, M.AlignError):
                    raise
                why = "嵌套过深" if isinstance(e, RecursionError) else str(e)
                raise M.AlignError(f"轨迹第 {n} 行不能解析：{why}") from e
            if not isinstance(state, dict):
                raise M.AlignError(f"轨迹第 {n} 行应是对象")
            states.append(state)


def _counterexample(states: list[dict], at: int) -> dict:
    """细则 13.4: the last 5 states up to the decisive one, and the whole action sequence."""
    upto = states[: at + 1]
    out = {"state": at, "last_states": upto[-5:]}
    actions = [s.get("动作") for s in upto if "动作" in s]
    if actions:
        out["actions"] = actions
    return out


def preflight(
    w: Path, align_dir: Path, props: dict[str, dict], checks: list[dict], entities: set[str], limits: dict
) -> list[str]:
    """细则 11.7: one run of each trace command before freezing; only the format of the trace is reported.

    Verdicts, exit codes and timeouts are thrown away: they may be defects of the code, and an alignment
    agent that knew about them could change the check until it passes.
    """
    deadline = time.monotonic() + PREFLIGHT_SECONDS
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="cv-preflight-") as tmp:
        by_command: dict[str, list[dict]] = {}
        for check in checks:
            if check["kind"] == "trace":
                by_command.setdefault(check["command"], []).append(check)
        for n, (command, users) in enumerate(by_command.items()):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            path = Path(tmp) / f"t{n}.jsonl"
            env = {
                "CV_ALIGN": str(align_dir),
                "CV_W": str(w),
                "PYTHONPYCACHEPREFIX": tmp,
                "CV_RUN": "0",
                "CV_TRACE": str(path),
            }
            out = run_command(command, w, env, min(users[0]["timeout"], max(1.0, left)))
            if out["exit"] != 0:
                continue
            try:
                states, _ = read_trace(path, limits["max_states"], limits.get("max_trace_bytes", 100 << 20), False)
            except M.AlignError as e:
                problems.append(f"{command}：{e}")
                continue
            for check in users:
                prop = props[check["proposition"]]
                try:
                    if prop.get("pattern") == "从出发" and not M.scenario_matches(prop["tree"], states, entities):
                        continue
                    M.judge(prop["tree"], states, entities, deadline=deadline)
                except M.AlignError as e:
                    problems.append(f"{prop['id']}（{command}）：{e}")
    return list(dict.fromkeys(problems))[:20]


def run_checks(run: Path, w: Path, props: dict[str, dict], frozen: dict, entities: set[str], limits: dict) -> dict:
    """Run every frozen check (细则 11.3, 11.4) and return results.json (细则 13.6)."""
    run, w = Path(run), Path(w)
    traces = S.ensure_private(run / "traces")
    align_dir = run / "align"
    deadline = time.monotonic() + limits["budget"]
    results: list[dict] = []
    # 细则 5.5: Python in a check reads bytecode only from a fresh empty prefix, never W's __pycache__.
    pycache = tempfile.mkdtemp(prefix="cv-pycache-")
    for check in frozen["checks"]:
        prop = props[check["proposition"]]
        rec = {**check, "runs_detail": [], "result": None, "reason": None, "counterexample": None}
        results.append(rec)
        if time.monotonic() >= deadline:
            rec.update(result=M.ALIGN_FAILED, reason="预算用尽，未运行")
            continue
        base_env = {"CV_ALIGN": str(align_dir), "CV_W": str(w), "PYTHONPYCACHEPREFIX": pycache}
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
            if path.is_file() and not path.is_symlink():
                detail["trace_sha256"] = S.sha256_file(path)
            if out["exit"] is None and not out["timeout"]:
                detail.update(result=M.ALIGN_FAILED, reason=f"检查命令{out['error']}")
                verdicts.append(M.ALIGN_FAILED)
                continue
            if out["exit"] not in (0, None):
                # 细则 12.6: a crash of the program or of the check script fails the check.
                detail["result"] = M.CHECK_FAILED
                verdicts.append(M.CHECK_FAILED)
                cex = rec["counterexample"]
                if cex is None or M.ORDER[M.CHECK_FAILED] < M.ORDER[cex.get("result", M.PASSED)]:
                    rec["counterexample"] = {
                        "run": i,
                        "result": M.CHECK_FAILED,
                        "exit": out["exit"],
                        "stderr": out["stderr"],
                        "stdout": out["stdout"],
                    }
                continue
            try:
                states, truncated = read_trace(
                    path, limits["max_states"], limits.get("max_trace_bytes", 100 << 20), partial_ok=out["timeout"]
                )
                if prop.get("pattern") == "从出发":
                    if not M.scenario_matches(prop["tree"], states, entities):
                        detail["result"] = "不相符"
                        continue
                    matched += 1
                judged = M.judge(
                    prop["tree"], states, entities, deadline=min(deadline, time.monotonic() + check["timeout"])
                )
            except Exception as e:  # 细则 4.8: untrusted input must not crash the node
                detail["result"] = M.ALIGN_FAILED
                detail["reason"] = str(e) if isinstance(e, M.AlignError) else f"无法在轨迹上判定：{type(e).__name__}"
                verdicts.append(M.ALIGN_FAILED)
                continue
            detail.update(result=judged["result"], state=judged["state"], truncated=truncated or out["timeout"])
            fired = M.triggered(prop["tree"], states, entities)
            if fired is not None:  # 细则 13.7
                rec["triggered"] = rec.get("triggered", False) or fired
            if detail["truncated"]:
                rec["truncated_runs"] = rec.get("truncated_runs", 0) + 1
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
        if rec["reason"]:  # 细则 11.8: a check cut short by the budget does not pass
            verdicts.append(M.ALIGN_FAILED)
        rec["result"] = M.worst(verdicts)
        if rec["result"] == M.ALIGN_FAILED and not rec["reason"]:
            rec["reason"] = next(d.get("reason") for d in rec["runs_detail"] if d.get("result") == M.ALIGN_FAILED)
        timeouts = sum(1 for d in rec["runs_detail"] if d.get("timeout"))
        if timeouts:
            rec["timeouts"] = timeouts
    by_prop: dict[str, dict] = {}
    for pid in props:
        # 细则 13.8: checks on inputs the requirement does not define are shown, not taken into the worst.
        aside = [r for r in results if r["proposition"] == pid and r.get("unstated_inputs")]
        mine = [r for r in results if r["proposition"] == pid and not r.get("unstated_inputs")]
        # 细则 13.3: a coverage failure takes part in the worst; it does not hide what a check found.
        if pid in frozen["align_failures"]:
            mine.append({"result": M.ALIGN_FAILED, "reason": frozen["align_failures"][pid], "counterexample": None})
        if not mine:
            mine.append({"result": M.ALIGN_FAILED, "reason": "没有任何检查", "counterexample": None})
        worst = M.worst([r["result"] for r in mine])
        reasons = [r["reason"] for r in mine if r["result"] == worst and r["reason"]]
        cexs = [r["counterexample"] for r in mine if r["result"] == worst and r["counterexample"]]
        notes = []
        for key, what in (("timeouts", "次运行超时"), ("truncated_runs", "条轨迹被截断")):
            count = sum(r.get(key, 0) for r in mine)
            if count:
                notes.append(f"{count} {what}")
        fired = [r["triggered"] for r in mine if "triggered" in r]
        vacuous = bool(fired) and not any(fired)  # 细则 13.7
        if vacuous:
            notes.append(VACUOUS)
        by_prop[pid] = {
            "result": worst,
            "reason": reasons[0] if reasons else None,
            "counterexample": cexs[0] if cexs else None,
            "notes": notes,
            **({"vacuous": True} if vacuous else {}),
            **({"unstated": True} if props[pid].get("unstated") else {}),
        }
        if aside:
            by_prop[pid]["unstated_inputs"] = [
                {k: r.get(k) for k in ("index", "form", "result", "reason", "counterexample")} for r in aside
            ]
    shutil.rmtree(pycache, ignore_errors=True)
    return {"checks": results, "propositions": by_prop}
