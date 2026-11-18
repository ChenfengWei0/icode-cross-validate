"""io alignment (细则 11.12): line 6 of Algorithm 1 for programs that only read input and write output.

The subtask's propositions and the program's formulas are unchanged. A tool-less alignment agent reads the
requirement (not the code) and writes a module that turns one run's input and output into a trace; the
checker judges every formula on those traces (第 13 节). This file holds the program side: the sandbox,
the trial on the requirement's own examples, the freeze, the mutation test and the runs.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import cv_check as C
import cv_formula as F
import cv_monitor as M
import cv_state as S

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "cv_io_harness.py"
MODULE = "io_module.py"
INPUTS = "io_inputs.json"
DEFAULTS = {"inputs": 150, "timeout": 6, "memory_mb": 4096, "tolerance": 1e-6, "mutants": 20}
TRIAL_INPUTS = 60
MIN_DISTINCT = 10
UNCHECKABLE = ("calls", "only")  # 细则 11.12: no one reads the code
STEP_OPS = ("X", "next")  # with 动作: meaningless on a one-state trace
FORM = "io 对齐"
BOX = [
    "bwrap", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
    "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
    "--unshare-all", "--die-with-parent", "--new-session",
]  # fmt: skip


def settings(config: dict, io: dict) -> dict:
    """config.json's defaults, then the project's io settings frozen in run.json."""
    return {**DEFAULTS, **(config.get("io") or {}), **(io or {})}


def python() -> str:
    return "/usr/bin/python3" if Path("/usr/bin/python3").exists() else (shutil.which("python3") or "python3")


def box(binds: list[tuple[Path, str]], argv: list[str], stdin: bytes, timeout: float, cfg: dict) -> dict:
    """Run argv in bwrap: no network, no home, only the given directories (read-only)."""
    cmd = list(BOX)
    for src, dst in binds:
        cmd += ["--ro-bind", str(src), dst]
    cmd += [*(cfg.get("box_args") or []), "--chdir", binds[0][1], *argv]
    if shutil.which("prlimit"):
        cmd = ["prlimit", f"--as={int(cfg['memory_mb']) << 20}", "--", *cmd]
    t = time.monotonic()
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout, check=False)
        return {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "timeout": False,
                "seconds": round(time.monotonic() - t, 2)}  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"exit": None, "stdout": b"", "stderr": b"", "timeout": True, "seconds": round(timeout, 2)}


class Module:
    """The alignment agent's module, called through the harness in a sandbox of its own."""

    def __init__(self, source: Path, cfg: dict):
        self.dir = Path(tempfile.mkdtemp(prefix="cv-io-mod-"))
        shutil.copy(source, self.dir / MODULE)
        shutil.copy(HARNESS, self.dir / HARNESS.name)
        self.cfg = cfg

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, op: str, items=None, timeout: float | None = None) -> dict:
        n = max(1, len(items or []))
        limit = timeout if timeout is not None else min(600.0, float(self.cfg["timeout"]) * n + 5)
        req = json.dumps({"op": op, "items": items or []}, ensure_ascii=False).encode()
        out = box([(self.dir, "/m")], [python(), "-I", f"/m/{HARNESS.name}", f"/m/{MODULE}"], req, limit, self.cfg)
        if out["timeout"]:
            return {"load_error": f"模块超时（{limit:.0f} 秒）"}
        try:
            return json.loads(out["stdout"].decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            tail = out["stderr"].decode("utf-8", "replace")[-300:]
            return {"load_error": f"模块异常退出（退出码 {out['exit']}）：{tail}"}


# ---------------------------------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------------------------------


def norm_input(text: str) -> str:
    """细则 11.12: trailing blanks of each line and trailing empty lines do not count."""
    return "\n".join(line.rstrip() for line in text.rstrip().splitlines())


def build_trace(item: dict) -> tuple[list[dict] | None, str | None, str | None]:
    """(states, unreadable reason, alignment error) from one observed item; derive merged in."""
    for part in ("derive", "trace"):
        if "error" in item.get(part, {}):
            return None, None, f"{part} 出错：{item[part]['error']}"
    tr, der = item["trace"]["ok"], item["derive"]["ok"]
    if isinstance(tr, dict) and set(tr) == {"无法读出"}:
        return None, str(tr["无法读出"])[:300], None
    if not isinstance(tr, list) or not tr or not all(isinstance(s, dict) for s in tr):
        return None, None, 'trace 应返回非空的状态列表，或 {"无法读出": 原因}'
    if not isinstance(der, dict) or not set(der) <= {"实体", "值"}:
        return None, None, 'derive 应返回 {"实体": {…}, "值": {…}}'
    dv, de = der.get("值", {}), der.get("实体", {})
    if not isinstance(dv, dict) or not isinstance(de, dict):
        return None, None, "derive 的 实体、值 应是对象"
    states = []
    for k, s in enumerate(tr):
        sv, se = s.get("值", {}), s.get("实体", {})
        if not isinstance(sv, dict) or not isinstance(se, dict):
            return None, None, f"状态 {k} 的 实体、值 应是对象"
        both = (set(sv) & set(dv)) | (set(se) & set(de))
        if both:
            return None, None, f"derive 和 trace 给出了同一个键：{'、'.join(sorted(both)[:5])}"
        state = {**s, "值": {**dv, **sv}}
        if de or se:
            state["实体"] = {**de, **se}
        states.append(state)
    return states, None, None


def names_of(tree: dict) -> set[str]:
    """Declared names a formula reads: predicates, terms, quantifier types and entity constants."""
    out = set()
    for n in F.walk(tree):
        if n["op"] in ("pred", "term", "const") and "name" in n:
            out.add(n["name"])
        if n["op"] in ("forall", "exists", "exists1") and n.get("type"):
            out.add(n["type"])
    return out


def uses_steps(tree: dict) -> bool:
    return any(n["op"] in STEP_OPS or (n["op"] == "term" and n.get("name") == "动作") for n in F.walk(tree))


def judge(prop: dict, states: list[dict], entities: set[str], tolerance: float) -> dict:
    """细则 11.12: the end of the trace is the end of the run, so presumed results are final."""
    r = M.judge(prop["tree"], states, entities, tolerance=tolerance)
    final = {M.PRESUMED_FALSE: M.VIOLATED, M.PRESUMED_TRUE: M.SATISFIED}
    return {**r, "result": final.get(r["result"], r["result"])}


def represented(tree: dict, states: list[dict]) -> bool:
    """表示(C) under io alignment: C has a value (or is a listed entity) in every state."""
    c = tree["args"][0]
    name = c.get("name") or str(c.get("value"))
    for s in states:
        keys = {k.split("(")[0] for k in s.get("值", {})}
        if name not in keys and name not in (s.get("实体") or {}):
            return False
    return True


# ---------------------------------------------------------------------------------------------------
# Structural propositions: the program reads W's Python files (细则 11.12)
# ---------------------------------------------------------------------------------------------------


def _defs(w: Path) -> tuple[dict[str, ast.AST], dict[str, set[str]]] | None:
    funcs: dict[str, ast.AST] = {}
    members: dict[str, set[str]] = {}
    files = [p for p in sorted(Path(w).rglob("*.py")) if ".git" not in p.parts]
    if not files:
        return None
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError, OSError):
            continue

        def visit(node: ast.AST, prefix: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    for name in (child.name, prefix + child.name):
                        funcs.setdefault(name, child)
                    if isinstance(child, ast.ClassDef):
                        mine = members.setdefault(child.name, set())
                        for item in ast.walk(child):
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                mine.add(item.name)
                            elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                                for t in targets:
                                    if isinstance(t, ast.Name):
                                        mine.add(t.id)
                                    elif isinstance(t, ast.Attribute):
                                        mine.add(t.attr)
                        members[prefix + child.name] = mine
                    visit(child, prefix + child.name + ".")
                elif isinstance(child, (ast.Assign, ast.AnnAssign)) and not prefix:
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    for t in targets:
                        if isinstance(t, ast.Name):
                            funcs.setdefault(t.id, t)

        visit(tree, "")
    return funcs, members


def _flat(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    text = text.removeprefix("def").removesuffix(":")
    return text.replace("→", "->")


def structural(prop: dict, w: Path) -> tuple[str, str | None]:
    """存在, 包含, 签名 decided from W's Python files; the result and a reason."""
    tree = prop["tree"]
    node = next(n for n in F.walk(tree) if n["op"] == "struct")
    found = _defs(w)
    if found is None:
        return M.ALIGN_FAILED, "W 中没有 Python 文件，结构命题判不了（细则 11.12）"
    funcs, members = found
    kind, args = node["kind"], node["args"]
    name = args[0].get("name", "")
    if kind == "exists":
        ok = name in funcs
        return (M.PASSED, None) if ok else (M.CHECK_FAILED, f"代码中没有 {name}")
    if kind == "contains":
        x = args[1].get("name", "")
        ok = x in members.get(name, set())
        return (M.PASSED, None) if ok else (M.CHECK_FAILED, f"{name} 中没有 {x}")
    if kind == "signature":
        fn = funcs.get(name)
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return M.CHECK_FAILED, f"代码中没有函数 {name}"
        text = f"{fn.name}({ast.unparse(fn.args)})" + (f" -> {ast.unparse(fn.returns)}" if fn.returns else "")
        ok = _flat(text) == _flat(str(args[1].get("value", "")))
        return (M.PASSED, None) if ok else (M.CHECK_FAILED, f"签名是「{text}」")
    return M.ALIGN_FAILED, "io 对齐判不了这种结构命题"


# ---------------------------------------------------------------------------------------------------
# Which propositions come from the examples (细则 9.1 第 5 项)
# ---------------------------------------------------------------------------------------------------


def from_examples(props: dict[str, dict], sentences: list[dict]) -> set[str]:
    hosts = [s for s in sentences if "示例" in s.get("labels", [])]
    out = set()
    for pid, p in props.items():
        if p.get("source") != "翻译":
            continue
        for q in C.quotes_in(p.get("basis", "")):
            flat = C._spaces(q)  # the same matching as 细则 9.1 第 5 项
            if flat and any(flat in C._spaces(x) for s in hosts for x in s.get("quotable", [])):
                out.add(pid)
    return out


def struct_kind(p: dict) -> str | None:
    return next((n["kind"] for n in F.walk(p["tree"]) if n["op"] == "struct"), None)


# ---------------------------------------------------------------------------------------------------
# Trial, freeze and mutation test (细则 11.12)
# ---------------------------------------------------------------------------------------------------


def _example_lines_ok(examples: list[list[str]], requirement: str) -> list[str]:
    flat = re.sub(r"\s+", "", requirement)
    bad = [
        f"示例 {i} 的一行「{line.strip()[:80]}」不在需求原文中"
        for i, (a, b) in enumerate(examples, 1)
        for line in (a + "\n" + b).splitlines()
        if line.strip() and re.sub(r"\s+", "", line) not in flat
    ]
    return bad[:5]


def _applies(pid: str, p: dict, inp: str, skipped: set[str], ex_inputs: dict[int, str], ex_props, example_of) -> bool:
    if pid in ex_props:
        k = example_of.get(pid)
        return k in ex_inputs and norm_input(inp) == ex_inputs[k]
    return not (names_of(p["tree"]) & skipped)


def trial(module: Module, props: dict[str, dict], entities: set[str], requirement: str, ex_props: set[str],
          cfg: dict) -> dict:  # fmt: skip
    """Everything the module shows on the requirement alone. problems: items 1–4, 7, 8; contradicted: item 5;
    unmapped: item 6."""
    out = {"problems": [], "contradicted": {}, "unmapped": {}, "examples": [], "example_of": {}, "derived": []}
    got = module.call("examples")
    if "load_error" in got:
        out["problems"].append(f"模块不能加载：{got['load_error']}")
        return out
    if "error" in got["examples"]:
        out["problems"].append(f"examples() 出错：{got['examples']['error']}")
        return out
    if "error" in got["example_of"]:
        out["problems"].append(f"EXAMPLE_OF 应是从命题编号到示例序号的对照：{got['example_of']['error']}")
        return out
    examples, example_of = got["examples"]["ok"], got["example_of"]["ok"]
    out["examples"], out["example_of"] = examples, example_of
    out["problems"] += _example_lines_ok(examples, requirement)
    for pid in sorted(ex_props):  # item 6
        if example_of.get(pid) not in range(1, len(examples) + 1):
            out["unmapped"][pid] = "EXAMPLE_OF 没有给出它所说的示例，或者序号不存在"
    valid = module.call("valid", [a for a, _ in examples])
    if "load_error" in valid:
        out["problems"].append(f"valid_input 出错：{valid['load_error']}")
        return out
    for i, v in enumerate(valid["items"], 1):  # item 3
        if "error" in v or not isinstance(v.get("ok"), list):
            out["problems"].append(f"示例 {i} 上 valid_input 出错：{v.get('error', '应返回列表')}")
        elif v["ok"]:
            out["problems"].append(f"示例 {i} 的输入被 valid_input 判为不合法：{'；'.join(map(str, v['ok']))[:200]}")
    obs = module.call("observe", [{"input": a, "output": b} for a, b in examples])
    if "load_error" in obs:
        out["problems"].append(f"示例上调用模块出错：{obs['load_error']}")
        return out
    ex_inputs = {i: norm_input(a) for i, (a, _) in enumerate(examples, 1)}
    derived: set[str] = set()
    for i, ((a, _b), item) in enumerate(zip(examples, obs["items"], strict=False), 1):  # items 4, 5
        sk = item["skip"]
        if "error" in sk or sk.get("ok"):
            out["problems"].append(f"示例 {i} 上 skip 应返回空列表：{sk.get('error') or sk.get('ok')}")
        states, unreadable, err = build_trace(item)
        if err or unreadable:
            out["problems"].append(f"示例 {i}：{err or '示例输出被判为无法读出：' + unreadable}")
            continue
        derived |= {k.split("(")[0] for k in item["derive"]["ok"].get("值", {})}
        derived |= set(item["derive"]["ok"].get("实体", {}))
        for pid, p in props.items():
            if pid in out["unmapped"] or struct_kind(p) is not None:
                continue
            if not _applies(pid, p, a, set(), ex_inputs, ex_props, example_of):
                continue
            try:
                r = judge(p, states, entities, float(cfg["tolerance"]))["result"]
            except M.AlignError as e:
                out["problems"].append(f"示例 {i} 上 {pid} 无法判定：{e}")
                continue
            if r in (M.VIOLATED, M.CHECK_FAILED):
                out["contradicted"].setdefault(pid, f"在示例 {i} 上被判违反")
    out["derived"] = sorted(derived)
    gen = module.call("generate", [[i, 1 + (10 * i) // TRIAL_INPUTS] for i in range(TRIAL_INPUTS)])
    if "load_error" in gen:
        out["problems"].append(f"generate 出错：{gen['load_error']}")
        return out
    texts = []
    for i, g in enumerate(gen["items"]):  # item 7
        if "error" in g or not isinstance(g.get("ok"), str):
            out["problems"].append(f"generate(Random({i}), …) 出错：{g.get('error', '应返回字符串')}")
            return out
        texts.append(g["ok"])
    if len(set(texts)) < MIN_DISTINCT:
        out["problems"].append(f"{TRIAL_INPUTS} 个生成的输入中只有 {len(set(texts))} 种不同的")
    checked = module.call("valid", texts)
    if "load_error" in checked:
        out["problems"].append(f"valid_input 出错：{checked['load_error']}")
        return out
    for t, v in zip(texts, checked["items"], strict=False):
        if "error" in v or v.get("ok"):
            why = v.get("error") or "；".join(map(str, v["ok"]))
            out["problems"].append(f"生成的输入被判为不合法：{why[:200]}\n输入：{t[:300]}")
            break
    skips = module.call("observe", [{"input": t} for t in texts])
    if "load_error" in skips:
        out["problems"].append(f"skip 出错：{skips['load_error']}")
        return out
    skipped = [set(s["skip"].get("ok") or []) if "error" not in s["skip"] else None for s in skips["items"]]
    if any(s is None for s in skipped):
        out["problems"].append("生成的输入上 skip 出错")
        return out
    for pid, p in props.items():  # item 8
        if pid in ex_props or struct_kind(p) is not None:
            continue
        if all(names_of(p["tree"]) & s for s in skipped):
            out["problems"].append(f"{pid} 在全部生成的输入上都因 skip 而无法判定")
    return out


def freeze_inputs(module: Module, examples: list[list[str]], n: int) -> list[str]:
    gen = module.call("generate", [[i, 1 + (10 * i) // n] for i in range(n)])
    texts = [a for a, _ in examples]
    texts += [g["ok"] for g in gen.get("items", []) if isinstance(g.get("ok"), str)]
    seen, out = set(), []
    for t in texts:
        if norm_input(t) not in seen:
            seen.add(norm_input(t))
            out.append(t)
    return out


def mutants(output: str, others: list[str], limit: int) -> list[str]:
    out = [
        output[: m.start()] + str(int(m.group()) + 1) + output[m.end() :]
        for m in list(re.finditer(r"-?\d+", output))[:limit]
    ]
    lines = output.rstrip("\n").split("\n")
    out += ["", "\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""), *others]
    seen, uniq = {output.strip()}, []
    for x in out:
        if x.strip() not in seen:
            seen.add(x.strip())
            uniq.append(x)
    return uniq


def mutation_kills(module: Module, props: dict[str, dict], entities: set[str], examples, example_of,
                   ex_props: set[str], cfg: dict) -> dict[str, int]:  # fmt: skip
    """细则 11.12 变异对照: how many wrong outputs on the examples each proposition stops."""
    kills = dict.fromkeys(props, 0)
    ex_inputs = {i: norm_input(a) for i, (a, _) in enumerate(examples, 1)}
    items = []
    for i, (a, b) in enumerate(examples):
        others = [y for j, (_, y) in enumerate(examples) if j != i][:1]
        items += [{"input": a, "output": m} for m in mutants(b, others, int(cfg["mutants"]))]
    if not items:
        return kills
    obs = module.call("observe", items)
    for it, item in zip(items, obs.get("items", []), strict=False):
        states, _, _ = build_trace(item)
        if states is None:
            continue
        for pid, p in props.items():
            if struct_kind(p) is not None or not _applies(pid, p, it["input"], set(), ex_inputs, ex_props, example_of):
                continue
            try:
                r = judge(p, states, entities, float(cfg["tolerance"]))["result"]
            except M.AlignError:
                continue
            if r in (M.VIOLATED, M.CHECK_FAILED):
                kills[pid] += 1
    return kills


# ---------------------------------------------------------------------------------------------------
# The checker's runs (细则 11.12 运行与判定)
# ---------------------------------------------------------------------------------------------------


def _text(b: bytes, n: int = 2000) -> str:
    return b.decode("utf-8", "replace")[-n:]


def run(run_dir: Path, w: Path, props: dict[str, dict], frozen: dict, entities: set[str], config: dict) -> list[dict]:
    """One result record per proposition, in results.json's shape (细则 13.6)."""
    io = frozen["io"]
    cfg = settings(config, io.get("settings"))
    tol = float(cfg["tolerance"])
    align = Path(run_dir) / "align"
    inputs = S.read_json(align / INPUTS)
    examples, example_of = io["examples"], io["example_of"]
    ex_inputs = {i: norm_input(a) for i, (a, _) in enumerate(examples, 1)}
    ex_props = set(io["example_props"])
    excluded = frozen.get("excluded", {})
    live = {pid: p for pid, p in props.items() if pid not in excluded and pid not in frozen["align_failures"]}
    blank = {"kind": "io", "form": FORM, "result": None, "reason": None, "counterexample": None, "runs": 0}
    recs = {pid: {"proposition": pid, "index": i, **blank, "judged": 0, "multi": 0} for i, pid in enumerate(live)}
    verdicts: dict[str, list[str]] = {pid: [] for pid in live}
    shown: dict[str, list[list[dict]]] = {pid: [] for pid in live}  # judged traces, for 表示 and 13.7
    deadline = time.monotonic() + float(config.get("budget", 3600))
    wcopy = Path(tempfile.mkdtemp(prefix="cv-io-w-"))
    shutil.copytree(w, wcopy / "w", ignore=shutil.ignore_patterns(".git", "__pycache__"), symlinks=True)
    module = Module(align / MODULE, cfg)
    cut = None
    try:
        for n, inp in enumerate(inputs):
            if time.monotonic() >= deadline:
                cut = f"预算用尽，只运行了 {n} 个输入"
                break
            out = box(
                [(wcopy / "w", "/w")], ["/bin/sh", "-c", cfg["command"]], inp.encode(), float(cfg["timeout"]), cfg
            )
            if out["exit"] != 0:
                why = "超时" if out["timeout"] else f"退出码 {out['exit']}"
                cex = {"run": n, "result": M.CHECK_FAILED, "input": inp[:2000], "why": why,
                       "stderr": _text(out["stderr"]), "stdout": _text(out["stdout"])}  # fmt: skip
                for pid in live:
                    _record(recs[pid], verdicts[pid], M.CHECK_FAILED, cex)
                continue
            output = out["stdout"].decode("utf-8", "replace")
            obs = module.call("observe", [{"input": inp, "output": output}], timeout=float(cfg["timeout"]) * 3 + 5)
            item = (obs.get("items") or [None])[0]
            if item is None:
                err, states, unreadable = obs.get("load_error", "模块没有回答"), None, None
            else:
                states, unreadable, err = build_trace(item)
                if err is None and "error" in item["skip"]:
                    err = f"skip 出错：{item['skip']['error']}"
            if err or unreadable:
                res = M.ALIGN_FAILED if err else M.CHECK_FAILED
                cex = {"run": n, "result": res, "input": inp[:2000], "output": output[:2000],
                       "why": err or f"输出无法读出：{unreadable}"}  # fmt: skip
                for pid in live:
                    _record(recs[pid], verdicts[pid], res, cex, reason=err)
                continue
            skipped = set(item["skip"]["ok"] or [])
            for pid, p in live.items():
                if struct_kind(p) is not None:
                    if struct_kind(p) == "represents":
                        shown[pid].append(states)
                    continue
                if not _applies(pid, p, inp, skipped, ex_inputs, ex_props, example_of):
                    continue
                try:
                    r = judge(p, states, entities, tol)
                except M.AlignError as e:
                    _record(recs[pid], verdicts[pid], M.ALIGN_FAILED, None, reason=str(e))
                    continue
                recs[pid]["judged"] += 1
                recs[pid]["multi"] += len(states) > 1
                fired = M.triggered(p["tree"], states, entities)
                if fired is not None:
                    recs[pid]["triggered"] = recs[pid].get("triggered", False) or fired
                cex = {"run": n, "result": r["result"], "input": inp[:2000], "output": output[:2000],
                       "last_states": states[: r["state"] + 1][-3:]}  # fmt: skip
                _record(recs[pid], verdicts[pid], r["result"], cex)
    finally:
        module.close()
    for pid, p in live.items():
        rec = recs[pid]
        kind = struct_kind(p)
        if kind == "represents":
            ok = bool(shown[pid]) and all(represented(p["tree"], s) for s in shown[pid])
            rec["result"] = M.PASSED if ok else M.CHECK_FAILED
            rec["reason"] = None if ok else "有的运行中读不出它的值"
            continue
        if kind is not None:
            rec["result"], rec["reason"] = structural(p, wcopy / "w")
            continue
        if cut:
            verdicts[pid].append(M.ALIGN_FAILED)
            rec["reason"] = rec["reason"] or cut
        if not verdicts[pid]:
            rec.update(result=M.ALIGN_FAILED, reason="没有任何一次运行判定过它（细则 11.12）")
            continue
        rec["result"] = M.worst(verdicts[pid])
        if uses_steps(p["tree"]) and rec["judged"] and not rec["multi"]:
            rec["untested"] = "只在一个状态的轨迹上判定过，含下一步的部分是空真的（细则 11.12）"
        if not io["kills"].get(pid):
            rec["untested"] = "在示例的变异输出上一个也没有拦下（细则 11.12）"
    shutil.rmtree(wcopy, ignore_errors=True)
    return list(recs.values())


def _record(rec: dict, verdicts: list[str], result: str, cex: dict | None, reason: str | None = None) -> None:
    rec["runs"] += 1
    verdicts.append(result)
    if result in (M.VIOLATED, M.CHECK_FAILED, M.ALIGN_FAILED):
        old = rec["counterexample"]
        if old is None or M.ORDER[result] < M.ORDER[old.get("result", M.PASSED)]:
            rec["counterexample"] = cex
            if reason:
                rec["reason"] = reason
