#!/usr/bin/env python3
"""Measure the checks on real runs: full workflow in a box, then the frozen checks on broken stacks.

    python3 scripts/live_eval.py --example examples/stack --model-yaml model.yaml --out eval-out --rounds 3

An example directory holds goal.txt, mutants.py and, optionally, seed/: files put in the project before
the workflow starts. mutants.py gives TARGET, the file to replace, and prepare(code), which returns the
source of every mutant for this round's code, or None when they cannot be made from it. MODE = "积累"
runs accumulation mode: the goal becomes one recorded message and the code is never rewritten.

A round that finished is not run again, so a stopped evaluation continues where it was. Each round has
its own home and project, and bwrap shows it nothing else: no other round, no real home, no environment.
The workflow runs for real. The mutants replay the frozen checks directly and skip the integrity checks,
which would rightly refuse a changed W.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_IN_BOX = "/cv-repo"
ROUND_SECONDS = 9000
REPLAY_SECONDS = 3600
FOUND = {"失败（检查）", "确定违反"}


def load_mutants(example: Path):
    spec = importlib.util.spec_from_file_location(f"mutants_{example.name}", example / "mutants.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def box(cwd: Path, bound: Path, home: Path, *command: str) -> list[str]:
    """Only `bound`, read-only system directories and the iCode install are visible."""
    real = Path(os.environ["HOME"])
    return [
        "bwrap", "--unshare-pid", "--unshare-ipc", "--die-with-parent", "--new-session",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--tmpfs", "/home", "--tmpfs", "/mnt", "--tmpfs", "/root", "--tmpfs", "/run",
        "--ro-bind-try", str(real / ".local/share/uv"), str(real / ".local/share/uv"),
        "--ro-bind-try", str(real / ".local/bin"), str(real / ".local/bin"),
        "--ro-bind", str(ROOT), REPO_IN_BOX,
        "--bind", str(bound), str(bound), "--chdir", str(cwd),
        "--clearenv", "--setenv", "HOME", str(home), "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PATH", f"{real / '.local/bin'}:/usr/bin:/bin",
        "--setenv", "TMPDIR", str(bound / "tmp"), "--setenv", "NO_PROXY", "127.0.0.1,localhost",
        "--setenv", "TERM", "dumb",
        *command,
    ]  # fmt: skip


def prepare(round_dir: Path, model_yaml: Path, seed: Path | None = None) -> Path:
    home = round_dir / "home"
    models = home / ".chrys" / "models"
    models.mkdir(parents=True)
    if seed and seed.is_dir():
        shutil.copytree(seed, round_dir / "proj")
    else:
        (round_dir / "proj").mkdir()
    (round_dir / "tmp").mkdir()
    text = model_yaml.read_text(encoding="utf-8")
    profile_id = re.search(r"^id:\s*(\S+)", text, re.MULTILINE).group(1)
    name = re.search(r"^name:\s*(\S+)", text, re.MULTILINE).group(1)
    (models / f"{profile_id}.yaml").write_text(text, encoding="utf-8")
    (home / ".chrys" / "settings.yaml").write_text(
        f"schema_version: 1\nmodel:\n  profile:\n    active: {name}\nsession:\n  title:\n    auto: false\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(home), "CV_CHRYS_HOME": str(home / ".chrys")}
    subprocess.run([str(ROOT / "cv"), "install"], cwd=ROOT, env=env, check=True, capture_output=True)
    subprocess.run(["git", "init", "-q"], cwd=round_dir / "proj", check=True)
    return home


def run_workflow(round_dir: Path, home: Path, goal: str) -> None:
    command = ["icode", "workflow", "run", "cross-validate", "--trust", "--json", "--input", goal]
    with open(round_dir / "run.json", "w") as out, open(round_dir / "run.err", "w") as err:
        subprocess.run(
            box(round_dir / "proj", round_dir, home, *command),
            stdout=out,
            stderr=err,
            timeout=ROUND_SECONDS,
            check=False,
        )


def run_accumulation(round_dir: Path, home: Path, goal: str) -> None:
    """The seed is the project; the requirement arrives as one recorded message; cv check runs it."""
    proj = round_dir / "proj"
    identity = ["-c", "user.name=eval", "-c", "user.email=eval@example.invalid"]
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)
    subprocess.run(["git", *identity, "commit", "-qm", "seed"], cwd=proj, check=True)
    (round_dir / "goal.txt").write_text(goal, encoding="utf-8")
    chrys = home / ".chrys"
    script = (
        f"{chrys}/cross-validate/bin/cv init . && "
        f"python3 -c \"import sys; sys.path.insert(0, '{chrys}/workflows/cross-validate'); import cv_ledger as L; "
        f"L.append_message('{proj}', 'eval', open('{round_dir}/goal.txt', encoding='utf-8').read(), False)\" && "
        f"{chrys}/cross-validate/bin/cv check ."
    )
    with open(round_dir / "run.json", "w") as out, open(round_dir / "run.err", "w") as err:
        try:
            subprocess.run(
                box(proj, round_dir, home, "sh", "-c", script),
                stdout=out,
                stderr=err,
                timeout=ROUND_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            err.write("超时\n")


def run_dir_of(home: Path) -> Path | None:
    found = sorted((home / ".chrys" / "cross-validate" / "projects").glob("*-proj/runs/*"))
    return found[-1] if found else None


def replay(mutant_dir: Path) -> None:
    """Inside the box: the frozen checks on one W, with the checker of this repository."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, f"{REPO_IN_BOX}/cross-validate")
    import cv_run as RUN
    import cv_state as S
    from cv_formula import Decls

    run = mutant_dir / "run"
    data = S.read_json(run / "propositions.json")
    props = {p["id"]: p for p in data["propositions"] if not p["problems"]}
    frozen = S.read_json(run / "align" / "checks.frozen.json")
    config = json.loads(Path(f"{REPO_IN_BOX}/cross-validate/config.json").read_text(encoding="utf-8"))
    entities = Decls.from_json(data["decls"]).of("实体")
    results = RUN.run_checks(run, mutant_dir / "w", props, frozen, entities, config)
    (mutant_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, default=str), encoding="utf-8")


def how_caught(results: dict, control: dict | None = None) -> str:
    """How a mutant was found. Only failures the correct code does not also have count.

    Formula: the checker judged a formula. Script: a command check failed. Crash: a trace run exited
    non-zero, so no formula was judged.
    """
    known = {k for k, v in (control or {"propositions": {}})["propositions"].items() if v["result"] in FOUND}
    # 细则 13.8: what is only for a person to confirm does not decide the conclusion, so it catches nothing.
    new = {k for k, v in results["propositions"].items() if v["result"] in FOUND and not v.get("unstated")} - known
    if not new:
        return "对齐失败" if any(p["result"] == "失败（对齐）" for p in results["propositions"].values()) else "漏掉"
    outcomes = {(c["kind"], c["result"]) for c in results["checks"] if c["proposition"] in new}
    if ("trace", "确定违反") in outcomes:
        return "公式"
    return "脚本" if ("command", "失败（检查）") in outcomes else "崩溃"


def replay_all(round_dir: Path, home: Path, run: Path, code: Path, mutants: dict[str, str], workers: int = 5) -> dict:
    base = round_dir / "mutants"
    sources = {"correct": code.read_text(encoding="utf-8"), **mutants}

    def one(item: tuple[str, str]) -> tuple[str, dict | None]:
        name, source = item
        target = base / name
        (target / "w").mkdir(parents=True)
        shutil.copytree(
            code.parent, target / "w", dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "__pycache__")
        )
        (target / "w" / code.name).write_text(source, encoding="utf-8")
        shutil.copytree(
            run, target / "run", ignore=shutil.ignore_patterns("traces", "results.json", "report.*", "__pycache__")
        )
        command = ["/usr/bin/python3", f"{REPO_IN_BOX}/scripts/live_eval.py", "--replay", str(target)]
        with open(target / "out.txt", "w") as log:
            subprocess.run(
                box(target / "w", round_dir, home, *command),
                stdout=log,
                stderr=log,
                timeout=REPLAY_SECONDS,
                check=False,
            )
        path = target / "results.json"
        return name, json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    with ThreadPoolExecutor(workers) as pool:
        return dict(pool.map(one, sources.items()))


def summarize_round(report: dict | None, replays: dict, note: str | None = None) -> dict:
    out: dict = {"conclusion": (report or {}).get("conclusion"), "reason": (report or {}).get("conclusion_reason")}
    out["stats"] = (report or {}).get("stats")
    out["to_confirm"] = len((report or {}).get("to_confirm") or [])
    out["note"] = note
    if not replays:
        return out
    control = replays.get("correct")
    out["control_clean"] = bool(control) and not any(
        p["result"] in (*FOUND, "失败（对齐）") and not p.get("unstated") for p in control["propositions"].values()
    )
    out["mutants"] = {
        name: how_caught(r, control) if r else "没有结果" for name, r in replays.items() if name != "correct"
    }
    return out


def summarize(rounds: list[dict]) -> str:
    lines = [
        "| 轮 | 结论 | 需要人确认 | 正确代码无误报 | 抓到 | 其中公式判定 | 漏掉 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    rates, missed = [], collections.Counter()
    for k, r in enumerate(rounds, 1):
        mutants = r.get("mutants") or {}
        caught = [n for n, how in mutants.items() if how in ("公式", "脚本", "崩溃")]
        formula = sum(how == "公式" for how in mutants.values())
        escaped = [n for n in mutants if n not in caught]
        missed.update(escaped)
        if mutants:
            rates.append(len(caught) / len(mutants))
        count = f"{len(caught)}/{len(mutants)}" if mutants else "—"
        lines.append(
            f"| {k} | {r.get('conclusion') or '没有结论'} | {r.get('to_confirm', '—')} "
            f"| {r.get('control_clean', '—')} | {count} | {formula} "
            f"| {'、'.join(escaped) or r.get('note') or '—'} |"
        )
    lines.append("")
    if rates:
        lines.append(f"平均抓到率：{sum(rates) / len(rates):.0%}（{len(rates)} 轮）")
    if missed:
        lines.append("漏掉的次数：" + "、".join(f"{n} {c} 次" for n, c in missed.most_common()))
    return "\n".join(lines) + "\n"


def evaluate_round(round_dir: Path, model_yaml: Path, example: Path, workers: int = 5) -> dict:
    mut = load_mutants(example)
    goal = (example / "goal.txt").read_text(encoding="utf-8").strip()
    if round_dir.exists():
        shutil.rmtree(round_dir)  # no summary means it did not finish
    round_dir.mkdir(parents=True)
    home = prepare(round_dir, model_yaml, example / "seed")
    if getattr(mut, "MODE", "目标") == "积累":
        run_accumulation(round_dir, home, goal)
    else:
        run_workflow(round_dir, home, goal)
    run = run_dir_of(home)
    report_path = run / "report.json" if run else None
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path and report_path.exists() else None
    code = round_dir / "proj" / mut.TARGET
    frozen = run / "align" / "checks.frozen.json" if run else None
    if not (code.exists() and frozen and frozen.exists()):
        return summarize_round(report, {}, "没有代码或没有冻结的检查，不重放")
    mutants = mut.prepare(code.read_text(encoding="utf-8"))
    if mutants is None:
        return summarize_round(report, {}, "变异版本无法从这一轮的代码生成，不重放")
    return summarize_round(report, replay_all(round_dir, home, run, code, mutants, workers))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-yaml", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--example", type=Path, default=ROOT / "examples" / "stack")
    parser.add_argument("--workers", type=int, default=5, help="并行重放的变异版本数，内存紧时调小")
    parser.add_argument("--replay", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.replay:
        replay(args.replay)
        return 0
    if not (args.model_yaml and args.out):
        parser.error("需要 --model-yaml 和 --out")
    rounds = []
    for k in range(1, args.rounds + 1):
        done = args.out / f"round-{k}.json"
        if done.exists():
            rounds.append(json.loads(done.read_text(encoding="utf-8")))
            continue
        args.out.mkdir(parents=True, exist_ok=True)
        result = evaluate_round(
            (args.out / f"round-{k}").resolve(), args.model_yaml.resolve(), args.example.resolve(), args.workers
        )
        done.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        rounds.append(result)
        text = summarize(rounds)
        (args.out / "summary.md").write_text(text, encoding="utf-8")
        print(text, flush=True)
    print(summarize(rounds))
    return 0


if __name__ == "__main__":
    sys.exit(main())
