"""io mode: cross-validation of programs that read standard input and write standard output (README 第 20 节).

The subtask, from the requirement alone, writes an executable specification: the input rules, a small-input
generator and an output checker, each condition quoted from the requirement. A program checks the
specification against the requirement's own examples before freezing it (20.3), runs the main task's program
on the frozen inputs (20.5), and asks fresh judges to confirm each violation on its concrete smallest case
(20.6). The conclusion (20.7) and an optional repair round (20.8) use only frozen artifacts.

Model calls are made through a caller-supplied function `ask(prompt) -> str`; this module has no network code.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"
REQUIRED = ("CONDITIONS", "INPUT_RULES", "valid_input", "generate", "check")
CHECK_ERROR = "检查出错"
PASS, FAIL, NONE = "比对通过", "比对未通过", "无结论"

# The sandbox: no network, no home, read-only /usr; one writable work directory (20.9).
BOX = ["bwrap", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
       "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
       "--unshare-all", "--die-with-parent", "--new-session"]  # fmt: skip


@dataclass
class Settings:
    """Numbers fixed by 20.3, 20.5, 20.6 and 20.8; config.json["io"] may override them."""

    fix_rounds: int = 2
    selftest_inputs: int = 60
    min_distinct: int = 10
    run_inputs: int = 150
    max_size: int = 10
    run_timeout: float = 5.0
    memory_mb: int = 1024
    judges: int = 2
    max_judged: int = 3
    repair_rounds: int = 2
    max_input_chars: int = 20000
    large_inputs: int = 3
    large_timeout: float = 20.0
    large_max_chars: int = 4_000_000
    slow_fails: bool = False
    pass_with_dismissed: bool = False

    @classmethod
    def load(cls, config: dict | None = None) -> Settings:
        cfg = (config or {}).get("io", {})
        return cls(**{k: v for k, v in cfg.items() if k in cls.__dataclass_fields__})


def prompt(name: str, **fields: str) -> str:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for key, value in fields.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def code_block(answer: str) -> str | None:
    """The last ```python block of an answer, or None."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", answer, re.DOTALL)
    return blocks[-1] if blocks else None


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("$$$", "")


# ---------------------------------------------------------------------------------------------------
# Running code in the box
# ---------------------------------------------------------------------------------------------------

DRIVER = r"""
import json, random, sys, traceback, resource, signal, subprocess, io, contextlib
req = json.load(open("req.json"))
def limit():
    mb = req.get("memory_mb", 1024)
    resource.setrlimit(resource.RLIMIT_AS, (mb << 20, mb << 20))
def tail(s, n=600): return s[-n:]
out = {}
if req["op"] == "run":                      # run the program on each input (20.5)
    res = []
    for text in req["inputs"]:
        try:
            p = subprocess.run([sys.executable, "-I", "prog.py"], input=text, capture_output=True, text=True,
                               timeout=req["timeout"], preexec_fn=limit)
            res.append({"rc": p.returncode, "out": p.stdout if req.get("keep_output", True) else "",
                        "err": tail(p.stderr)})
        except subprocess.TimeoutExpired:
            res.append({"rc": None, "out": "", "err": "timeout"})
    out["runs"] = res
else:
    limit()
    try:
        sys.path.insert(0, "."); import spec
    except BaseException:
        print(json.dumps({"load_error": tail(traceback.format_exc())})); sys.exit()
    def call(f, *a):
        signal.alarm(req.get("alarm", 10))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return True, f(*a)
        except BaseException:
            return False, tail(traceback.format_exc())
        finally:
            signal.alarm(0)
    if req["op"] == "generate":               # fixed seeds and sizes (20.5)
        res = []
        for seed, size in req["seeds"]:
            ok, v = call(spec.generate, random.Random(seed), size)
            res.append({"ok": ok and isinstance(v, str), "text": v if ok and isinstance(v, str) else None,
                        "error": None if ok and isinstance(v, str) else (v if not ok else f"generate returned {type(v).__name__}")})
        out["inputs"] = res
    elif req["op"] == "generate_large":        # large inputs (20.11)
        res = []
        for seed in req["seeds"]:
            ok, v = call(spec.generate_large, random.Random(seed))
            res.append({"ok": ok and isinstance(v, str), "text": v if ok and isinstance(v, str) else None,
                        "error": None if ok and isinstance(v, str) else (v if not ok else f"generate_large returned {type(v).__name__}")})
        out["inputs"] = res
    elif req["op"] == "valid":
        out["valid"] = [call(spec.valid_input, t) for t in req["inputs"]]
    elif req["op"] == "check":
        out["checks"] = [call(spec.check, i, o) for i, o in req["pairs"]]
    elif req["op"] == "meta":
        conds = getattr(spec, "CONDITIONS", None); rules = getattr(spec, "INPUT_RULES", None)
        out["meta"] = {"CONDITIONS": conds, "INPUT_RULES": rules,
                       "functions": [n for n in ("valid_input", "generate", "check") if callable(getattr(spec, n, None))]}
print(json.dumps(out, default=str))
"""


def box(files: dict[str, str], req: dict, timeout: float) -> dict:
    """Run DRIVER in a fresh box with the given files; returns its JSON or {"box_error": ...}."""
    if shutil.which("bwrap") is None:
        return {"box_error": "bwrap not found"}
    with tempfile.TemporaryDirectory(prefix="cv-io-") as tmp:
        d = Path(tmp)
        for name, text in files.items():
            (d / name).write_text(text, encoding="utf-8")
        (d / "driver.py").write_text(DRIVER, encoding="utf-8")
        (d / "req.json").write_text(json.dumps(req), encoding="utf-8")
        cmd = [*BOX, "--bind", str(d), "/w", "--chdir", "/w", "/usr/bin/python3", "-I", "driver.py"]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
        except subprocess.TimeoutExpired:
            return {"box_error": f"timeout after {timeout:.0f} s"}
    lines = p.stdout.strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        return {"box_error": (p.stderr or p.stdout)[-600:]}


# ---------------------------------------------------------------------------------------------------
# The specification and its self-test (20.2, 20.3)
# ---------------------------------------------------------------------------------------------------


def _items(value, kind: str, requirement: str, problems: list[str]) -> dict[str, dict]:
    if not isinstance(value, list) or not value:
        problems.append(f"{kind} 必须是非空列表")
        return {}
    out, req = {}, _norm(requirement)
    for i, item in enumerate(value):
        if not (isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str)
                and isinstance(item.get("quote"), str)):  # fmt: skip
            problems.append(f"{kind}[{i}] 要有字符串字段 id、text、quote")
            continue
        if item["id"] in out:
            problems.append(f"{kind} 中编号 {item['id']} 重复")
        if not _norm(item["quote"]) or _norm(item["quote"]) not in req:
            problems.append(f"{kind} {item['id']} 的 quote 不是需求中的原文：{item['quote'][:80]}")
        out[item["id"]] = item
    return out


def _violations(value) -> list[tuple[str, str]] | None:
    """check() result as [(condition id, message)], or None when malformed."""
    if not isinstance(value, list):
        return None
    out = []
    for v in value:
        if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str):
            out.append((v[0], str(v[1])))
        elif isinstance(v, str):
            out.append((v, ""))
        else:
            return None
    return out


def seeds(n: int, max_size: int, salt: int = 0) -> list[tuple[int, int]]:
    """Fixed (seed, size) pairs; sizes ramp from 1 to max_size (20.5)."""
    return [(salt * 100003 + i, 1 + (i * max_size) // max(n, 1)) for i in range(n)]


def selftest(spec: str, requirement: str, examples: list[tuple[str, str]], s: Settings) -> dict:
    """20.3: problems found without any code of the main task; empty "problems" means the spec may be frozen."""
    problems: list[str] = []
    meta = box({"spec.py": spec}, {"op": "meta", "memory_mb": s.memory_mb}, 60)
    if "load_error" in meta or "box_error" in meta:
        return {"problems": [f"模块无法加载：{meta.get('load_error') or meta.get('box_error')}"]}
    m = meta["meta"]
    missing = [n for n in ("valid_input", "generate", "check") if n not in m["functions"]]
    if missing:
        problems.append("缺少函数：" + "、".join(missing))
    conds = _items(m["CONDITIONS"], "CONDITIONS", requirement, problems)
    _items(m["INPUT_RULES"], "INPUT_RULES", requirement, problems)
    if missing:
        return {"problems": problems}
    ex_in = [i for i, _ in examples]
    valid = box({"spec.py": spec}, {"op": "valid", "inputs": ex_in, "memory_mb": s.memory_mb}, 120)
    pairs = [[i, o] for i, o in examples] + [[i, ""] for i in ex_in]
    checks = box({"spec.py": spec}, {"op": "check", "pairs": pairs, "memory_mb": s.memory_mb}, 120)
    gen = box({"spec.py": spec}, {"op": "generate", "seeds": seeds(s.selftest_inputs, s.max_size, salt=7),
                                  "memory_mb": s.memory_mb}, 300)  # fmt: skip
    for res in (valid, checks, gen):
        if "box_error" in res or "load_error" in res:
            return {"problems": [*problems, f"运行出错：{res.get('box_error') or res.get('load_error')}"]}
    for k, (ok, v) in enumerate(valid["valid"]):
        if not ok:
            problems.append(f"示例 {k + 1} 的输入上 valid_input 出错：{v}")
        elif v:
            problems.append(f"示例 {k + 1} 的输入被 valid_input 判为不合法：{v}")
    n = len(examples)
    for k, (ok, v) in enumerate(checks["checks"]):
        which = f"示例 {k % n + 1}" if n else "示例"
        if not ok:
            problems.append(f"{which}{'的空输出' if k >= n else ''}上 check 出错：{v}")
            continue
        viol = _violations(v)
        if viol is None:
            problems.append(f"{which} 上 check 的返回值格式不对：{str(v)[:200]}")
        elif k < n and viol:
            problems.append(f"{which} 给出的输出被 check 判为违反：{viol[:3]}")
        elif k >= n and not viol:
            problems.append(f"{which} 的输入配空输出，check 没有判为违反")
        elif viol and any(c not in conds and c != CHECK_ERROR for c, _ in viol):
            problems.append(f"{which} 上 check 返回了 CONDITIONS 中没有的编号：{[c for c, _ in viol]}")
    texts = []
    for k, r in enumerate(gen["inputs"]):
        if not r["ok"]:
            problems.append(f"generate 第 {k + 1} 次出错：{r['error']}")
        elif len(r["text"]) > s.max_input_chars:
            problems.append(f"generate 第 {k + 1} 次生成的输入过长：{len(r['text'])} 个字符")
        else:
            texts.append(r["text"])
        if len(problems) > 12:
            break
    if texts:
        gv = box({"spec.py": spec}, {"op": "valid", "inputs": texts, "memory_mb": s.memory_mb}, 300)
        bad = [(t, v) for t, (ok, v) in zip(texts, gv.get("valid", []), strict=False) if not ok or v]
        if bad:
            t, v = min(bad, key=lambda b: len(b[0]))
            problems.append(
                f"generate 生成的 {len(bad)} 个输入被 valid_input 判为不合法，最短的一个：{t[:300]!r}，原因：{str(v)[:300]}"
            )
        if len(set(texts)) < min(s.min_distinct, len(texts)):
            problems.append(f"generate 只生成了 {len(set(texts))} 种不同的输入")
    return {"problems": problems, "conditions": list(conds)}


@dataclass
class Spec:
    text: str
    conditions: dict[str, dict]
    inputs: list[str]
    log: list[dict] = field(default_factory=list)
    large: list[str] = field(default_factory=list)

    @property
    def digest(self) -> str:
        return digest(self.text)


def write_spec(
    ask: Callable[[str], str], requirement: str, examples: list[tuple[str, str]], s: Settings
) -> tuple[Spec | None, list[dict]]:
    """20.2–20.4: ask, self-test, fix at most s.fix_rounds times, then freeze the spec and its inputs."""
    log: list[dict] = []
    answer = ask(prompt("io_spec", REQUIREMENT=requirement))
    for attempt in range(s.fix_rounds + 1):
        spec = code_block(answer)
        result = (
            {"problems": ["回答中没有 ```python 代码块"]} if spec is None else selftest(spec, requirement, examples, s)
        )
        log.append({"attempt": attempt, "answer": answer, "problems": result["problems"]})
        if not result["problems"]:
            break
        if attempt == s.fix_rounds:
            return None, log
        answer = ask(prompt("io_fix", REQUIREMENT=requirement, SPEC=spec or answer,
                            PROBLEMS="\n".join(f"- {p}" for p in result["problems"])))  # fmt: skip
    if spec is None:
        return None, log
    gen = box(
        {"spec.py": spec}, {"op": "generate", "seeds": seeds(s.run_inputs, s.max_size), "memory_mb": s.memory_mb}, 600
    )
    inputs = [i for i, _ in examples] + [
        r["text"] for r in gen.get("inputs", []) if r["ok"] and len(r["text"]) <= s.max_input_chars
    ]
    meta = box({"spec.py": spec}, {"op": "meta", "memory_mb": s.memory_mb}, 60)["meta"]
    conds = {c["id"]: c for c in meta["CONDITIONS"]}
    return Spec(spec, conds, list(dict.fromkeys(inputs)), log), log


def large_selftest(module: str, s: Settings) -> tuple[list[str], list[str]]:
    """20.11: (problems, inputs) for a module that defines generate_large."""
    gen = box({"spec.py": module}, {"op": "generate_large", "seeds": [9001 + i for i in range(s.large_inputs)],
                                    "memory_mb": s.memory_mb, "alarm": 20}, 120 + 30 * s.large_inputs)  # fmt: skip
    if "box_error" in gen or "load_error" in gen:
        return [f"模块无法运行：{gen.get('box_error') or gen.get('load_error')}"], []
    problems, texts = [], []
    for k, r in enumerate(gen["inputs"]):
        if not r["ok"]:
            problems.append(f"generate_large 第 {k + 1} 次出错：{r['error']}")
        elif len(r["text"]) > s.large_max_chars:
            problems.append(
                f"generate_large 第 {k + 1} 次生成的输入有 {len(r['text'])} 个字符，超过 {s.large_max_chars}"
            )
        else:
            texts.append(r["text"])
    if texts:
        gv = box({"spec.py": module}, {"op": "valid", "inputs": texts, "memory_mb": s.memory_mb, "alarm": 60},
                 120 + 60 * len(texts))  # fmt: skip
        for t, (ok, v) in zip(texts, gv.get("valid", [(False, gv.get("box_error"))] * len(texts)), strict=True):
            if not ok or v:
                problems.append(
                    f"generate_large 生成的输入（开头：{t[:200]!r}）被 valid_input 判为不合法：{str(v)[:300]}"
                )
    return problems, list(dict.fromkeys(texts))


def write_large(ask: Callable[[str], str], requirement: str, spec: Spec, s: Settings) -> list[dict]:
    """20.11: the subtask adds generate_large to its frozen module; the inputs it gives are kept in spec.large."""
    log: list[dict] = []
    problems = "无"
    for attempt in range(s.fix_rounds + 1):
        answer = ask(prompt("io_large", REQUIREMENT=requirement, SPEC=spec.text, PROBLEMS=problems))
        code = code_block(answer)
        if code is None:
            found, texts = ["回答中没有 ```python 代码块"], []
        else:
            found, texts = large_selftest(spec.text + "\n\n" + code, s)
        log.append({"attempt": attempt, "answer": answer, "problems": found})
        if not found:
            spec.large = texts
            break
        problems = "\n".join(f"- {p}" for p in found)
    return log


# ---------------------------------------------------------------------------------------------------
# Running the program, judging, concluding (20.5–20.7)
# ---------------------------------------------------------------------------------------------------


def cross_run(spec: Spec, program: str, s: Settings) -> list[dict]:
    """20.5: every frozen input -> {"input", "output", "kind": pass|crash|timeout|violation, "violations"}."""
    runs = box({"prog.py": program}, {"op": "run", "inputs": spec.inputs, "timeout": s.run_timeout,
                                      "memory_mb": s.memory_mb}, 60 + s.run_timeout * len(spec.inputs))  # fmt: skip
    if "box_error" in runs:
        raise RuntimeError(runs["box_error"])
    results, pairs = [], []
    for text, r in zip(spec.inputs, runs["runs"], strict=True):
        if r["rc"] is None:
            results.append({"input": text, "output": "", "kind": "timeout", "detail": "timeout"})
        elif r["rc"] != 0:
            results.append({"input": text, "output": r["out"], "kind": "crash", "detail": r["err"]})
        else:
            results.append({"input": text, "output": r["out"], "kind": "pass"})
            pairs.append((len(results) - 1, [text, r["out"]]))
    checks = box({"spec.py": spec.text}, {"op": "check", "pairs": [p for _, p in pairs], "memory_mb": s.memory_mb},
                 120 + 2 * len(pairs))  # fmt: skip
    if "box_error" in checks or "load_error" in checks:
        raise RuntimeError(checks.get("box_error") or checks.get("load_error"))
    for (k, _), (ok, v) in zip(pairs, checks["checks"], strict=True):
        viol = _violations(v) if ok else None
        if not ok or viol is None:
            results[k].update(kind="violation", violations=[(CHECK_ERROR, str(v)[-300:])])
        elif viol:
            results[k].update(kind="violation", violations=viol)
    if spec.large:  # 20.11: large inputs, only the exit status counts
        big = box({"prog.py": program}, {"op": "run", "inputs": spec.large, "timeout": s.large_timeout,
                                         "memory_mb": s.memory_mb, "keep_output": False},
                  60 + s.large_timeout * len(spec.large))  # fmt: skip
        if "box_error" in big:
            raise RuntimeError(big["box_error"])
        for text, r in zip(spec.large, big["runs"], strict=True):
            kind = ("timeout" if s.slow_fails else "slow") if r["rc"] is None else "crash" if r["rc"] != 0 else "pass"
            results.append({"input": text, "output": "", "kind": kind, "detail": r["err"], "large": True})
    return results


def _condition(spec: Spec, cid: str) -> dict:
    if cid == CHECK_ERROR:
        return {"id": cid, "text": "检查程序无法处理这个输出（可能是输出格式不对）", "quote": ""}
    return spec.conditions.get(cid, {"id": cid, "text": "（检查返回了未声明的编号）", "quote": ""})


def judge(ask: Callable[[str], str], requirement: str, spec: Spec, case: dict, cid: str, message: str) -> dict:
    """20.6: one fresh judgement on one concrete case; no code is shown."""
    c = _condition(spec, cid)
    answer = ask(prompt("io_judge", REQUIREMENT=requirement, INPUT=case["input"], OUTPUT=case["output"][:4000],
                        CONDITION=c["text"], QUOTE=c["quote"], MESSAGE=message))  # fmt: skip
    first = next((ln.strip() for ln in answer.splitlines() if ln.strip()), "")
    verdict = "不违反" if first.startswith("不违反") else "违反" if first.startswith("违反") else "无法判断"
    return {"condition": cid, "verdict": verdict, "answer": answer[-1500:]}


def conclude(ask: Callable[[str], str] | None, requirement: str, spec: Spec, results: list[dict], s: Settings,
             dismissed: set[str] | None = None) -> dict:  # fmt: skip
    """20.6, 20.7: judge violations condition by condition, then decide."""
    dismissed = set(dismissed or ())
    judged: list[dict] = []
    hard = [r for r in results if r["kind"] in ("crash", "timeout")]
    while True:
        live = [
            (r, [(c, m) for c, m in r.get("violations", []) if c not in dismissed])
            for r in results
            if r["kind"] == "violation"
        ]
        live = [(r, v) for r, v in live if v]
        if hard or not live:
            break
        by_cond: dict[str, tuple[dict, str]] = {}
        for r, v in sorted(live, key=lambda x: len(x[0]["input"]) + len(x[0]["output"])):
            for c, m in v:
                by_cond.setdefault(c, (r, m))
        pending = [c for c in by_cond if c not in {j["condition"] for j in judged}]
        if ask is None or not pending or len({j["condition"] for j in judged}) >= s.max_judged:
            break
        cid = pending[0]
        case, message = by_cond[cid]
        votes = [judge(ask, requirement, spec, case, cid, message) for _ in range(s.judges)]
        judged.append({"condition": cid, "input": case["input"], "output": case["output"][:4000], "message": message,
                       "votes": votes})  # fmt: skip
        if all(v["verdict"] == "不违反" for v in votes):
            dismissed.add(cid)
            continue
        break  # one confirmed (or undecided) violation is enough to fail
    remaining = [r for r in results if r["kind"] in ("crash", "timeout")
                 or any(c not in dismissed for c, _ in r.get("violations", []))]  # fmt: skip
    n_cond = max(len(spec.conditions), 1)
    if len(dismissed) * 2 > n_cond:
        conclusion = NONE
    elif remaining:
        conclusion = FAIL
    elif dismissed and not s.pass_with_dismissed:
        conclusion = NONE
    else:
        conclusion = PASS
    counter = None
    if remaining:
        r = next((x for x in remaining if x["kind"] in ("crash", "timeout")), None)
        if r is None:
            j = next((j for j in judged if j["condition"] not in dismissed), None)
            ordered = sorted(remaining, key=lambda x: len(x["input"]) + len(x["output"]))
            r = next(x for x in ordered if j is None or x["input"] == j["input"])
        counter = r
    return {
        "conclusion": conclusion,
        "dismissed": sorted(dismissed),
        "judged": judged,
        "counterexample": counter,
        "counts": {k: sum(r["kind"] == k for r in results) for k in ("pass", "crash", "timeout", "violation", "slow")},
    }


def counterexample_text(spec: Spec, case: dict) -> str:
    """What the main task sees in a repair round (20.8): the input, its own output and the broken condition only."""
    if case.get("large"):
        lines = [f"输入（共 {len(case['input'])} 个字符的大输入，这里只给开头）：", "```", case["input"][:300], "```"]
    else:
        lines = ["输入：", "```", case["input"].rstrip("\n"), "```"]
    if case["kind"] == "timeout":
        lines.append("你的程序在这个输入上超时。")
    elif case["kind"] == "crash":
        lines += ["你的程序在这个输入上异常退出，错误输出的末尾：", "```", case["detail"][-600:], "```"]
    else:
        lines += ["你的程序的输出：", "```", case["output"][:2000].rstrip("\n"), "```"]
        for cid, msg in case.get("violations", [])[:1]:
            c = _condition(spec, cid)
            lines.append(f"这个输出不满足需求中的要求：{c['text']}" + (f"（原文：{c['quote']}）" if c["quote"] else ""))
            if msg:
                lines.append(f"具体：{msg[:400]}")
    return "\n".join(lines)


def repair(ask: Callable[[str], str], requirement: str, program: str, spec: Spec, case: dict) -> str | None:
    answer = ask(
        prompt("io_repair", REQUIREMENT=requirement, CODE=program, COUNTEREXAMPLE=counterexample_text(spec, case))
    )
    return code_block(answer)


def validate(program: str, spec: Spec, requirement: str, s: Settings, judge_ask: Callable[[str], str] | None,
             repair_ask: Callable[[str], str] | None = None) -> list[dict]:  # fmt: skip
    """20.5–20.8 for one program: a list of rounds, each {"program", "conclusion", ...}; round 0 is the original."""
    rounds = []
    for k in range(s.repair_rounds + 1):
        res = cross_run(spec, program, s)
        c = conclude(judge_ask, requirement, spec, res, s)
        rounds.append({"round": k, "program": program, **c})
        if c["conclusion"] != FAIL or repair_ask is None or k == s.repair_rounds or c["counterexample"] is None:
            break
        new = repair(repair_ask, requirement, program, spec, c["counterexample"])
        if not new:
            break
        program = new
    return rounds
