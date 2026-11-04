"""verify checks: the formula as C monitor code, decided by a verifier (README 细则 11.11).

The verifier never sees temporal logic. The generated code records the states the harness observes,
evaluates the formula on the recorded finite prefix with the finite-trace semantics of 细则 13.2 第 5 步,
and asserts the value. For the safety fragment allowed here a prefix that is false is a bad prefix, so a
failing assertion is a definite violation.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import tempfile
from pathlib import Path

import cv_formula as F
import cv_monitor as M

ARITH_C = {"add": "+", "sub": "-", "mul": "*"}
CMP_C = {"eq": "==", "ne": "!=", "lt": "<", "gt": ">", "le": "<=", "ge": ">="}
SOURCE = re.compile(r"^(\$CV_ALIGN/)?([^\0]+)$")
ENGINES = ("cbmc",)

# Property descriptions; the program reads the verifier's verdict on each of them by these words.
MAIN, REACH, FIRED, OVERFLOW = "主性质", "检查点可达", "前件成立过", "状态数超出"
AFTER, DEPTH = "检查之后又记录", "达到步数"


def _label(pid: str, what: str, nonce: str = "") -> str:
    return f"cv {pid} {what} {nonce}".rstrip()


# ---------------------------------------------------------------------------------------------------
# Which propositions a verifier can decide (细则 11.11 适用的命题)
# ---------------------------------------------------------------------------------------------------


def _arg_id(n: dict) -> str | None:
    if n["op"] == "const":
        return n["name"]
    if n["op"] in ("lit", "ind") and isinstance(n["value"], (str, int)) and not isinstance(n["value"], bool):
        return str(n["value"])
    return None


def _term_problems(n: dict, entities: set[str], out: list[str]) -> None:
    op = n["op"]
    if op == "next":
        _term_problems(n["args"][0], entities, out)
    elif op == "lit":
        if not isinstance(n["value"], int):  # bool is an int here: true/false are allowed
            out.append(f"字面量 {n['value']!r} 不是整数或布尔值")
    elif op == "term":
        if n["name"] == F.ACTION:
            out.append("含 动作")
        # Only entity names: a C state has no list of individuals to tell whether a named one is present.
        out.extend(f"{n['name']} 的参数不是实体名" for a in n["args"] if a["op"] != "const")
    elif op in ARITH_C:
        for a in n["args"]:
            _term_problems(a, entities, out)
    elif op == "div":
        out.append("含除法")
    elif op == "const":
        out.append(f"实体 {n['name']} 不能作为数比较")
    else:
        out.append(f"含 {op}")


def _atom_problems(atom: dict, entities: set[str], out: list[str]) -> None:
    op = atom["op"]
    if op == "pred":
        if atom["name"] in entities:
            out.append("含实体判断")
        out.extend(f"{atom['name']} 的参数不是实体名" for a in atom["args"] if a["op"] != "const")
    elif op in F.COMPARISONS:
        for a in atom["args"]:
            _term_problems(a, entities, out)
    else:
        out.append({"at": "含下标", "fails": "含调用", "struct": "是结构命题"}.get(op, f"含 {op}"))


def problems(tree: dict, entities: set[str]) -> list[str]:
    """Why this proposition cannot be a verify check; empty when it can."""
    if F.classify(tree) != "时序":
        return ["只有时序命题可以用 verify 检查"]
    out: list[str] = []
    try:
        root = M.nnf(tree)
    except M.AlignError as e:
        return [str(e)]

    def go(n: dict) -> None:
        op = n["op"]
        if op in ("F", "U", "M", "Xs") or (op == "Xcmp" and n["neg"]):
            out.append("含活性成分（最终、直到或否定的下一步）")
        elif op == "Q":
            out.append("含量词")
        elif op in ("L", "Xcmp"):
            _atom_problems(n["atom"], entities, out)
        for a in n.get("args", []):
            go(a)

    go(root)
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------------------------------


def _key_of(n: dict) -> str:
    ids = [_arg_id(a) for a in n["args"]]
    return M._key(n["name"], ids)


def keys(tree: dict) -> dict[str, str]:
    """Every key the formula reads, in order of first use, with its kind (谓词 or 值)."""
    out: dict[str, str] = {}
    for n in F.walk(tree):
        if n["op"] == "pred":
            out.setdefault(_key_of(n), "谓词")
        elif n["op"] == "term" and n["name"] != F.ACTION:
            out.setdefault(_key_of(n), "值")
    return out


def constants(tree: dict) -> list[str]:
    """Entity names used as arguments: each gets a presence field (细则 12.4: no individual, atom false)."""
    out: list[str] = []
    for n in F.walk(tree):
        if n["op"] in ("pred", "term"):
            out += [a["name"] for a in n["args"] if a["op"] == "const" and a["name"] not in out]
    return out


class _Gen:
    def __init__(self, pid: str, fields: dict[str, int], present: dict[str, int], cap: int):
        self.pid, self.fields, self.present, self.cap = pid, fields, present, cap
        self.order: list[tuple[str, dict]] = []  # (array, node), children before parents

    def s(self, at: str, key: str) -> str:
        return f"cv_{self.pid}_s[{at}].k{self.fields[key]}"

    def term(self, n: dict, now: str, nxt: str, need: list[str], primed: bool = False) -> str:
        op = n["op"]
        if op == "next":
            return self.term(n["args"][0], now, nxt, need, True)
        if op == "lit":
            return f"((__int128){int(n['value'])})"
        if op == "term":
            at = nxt if primed else now
            need += [f"cv_{self.pid}_s[{at}].p{self.present[a['name']]}" for a in n["args"] if a["op"] == "const"]
            return f"((__int128){self.s(at, _key_of(n))})"
        a, b = (self.term(x, now, nxt, need, primed) for x in n["args"])
        return f"({a} {ARITH_C[op]} {b})"

    def atom(self, atom: dict, now: str, nxt: str) -> str:
        """细则 12.3, 12.4, 10.3: an atom naming an absent individual is false."""
        need: list[str] = []
        if atom["op"] == "pred":
            need = [f"cv_{self.pid}_s[{now}].p{self.present[a['name']]}" for a in atom["args"] if a["op"] == "const"]
            value = f"({self.s(now, _key_of(atom))} != 0)"
        else:
            a, b = (self.term(x, now, nxt, need) for x in atom["args"])
            value = f"({a} {CMP_C[atom['op']]} {b})"
        return "(" + " && ".join([*(f"({x} != 0)" for x in dict.fromkeys(need)), value]) + ")"

    def node(self, n: dict) -> str:
        """The array holding n's value at every position; registers n and its children."""
        for name, m in self.order:
            if m is n:
                return name
        for a in n.get("args", []):
            self.node(a)
        name = f"v{len(self.order)}"
        self.order.append((name, n))
        return name

    def at(self, n: dict, i: int) -> str:
        """C expression for n at position i, given the children's arrays (weak at the end of the prefix)."""
        op = n["op"]
        more = f"({i + 1} < n)"
        child = [self.node(a) for a in n.get("args", [])]
        if op == "T":
            return "1"
        if op == "⊥":
            return "0"
        if op == "L":
            value = self.atom(n["atom"], str(i), str(i + 1))
            return f"(!{value})" if n["neg"] else value
        if op == "Xcmp":
            return f"(!{more} || {self.atom(n['atom'], str(i), str(i + 1))})"
        if op in ("and", "or"):
            joiner = " && " if op == "and" else " || "
            return "(" + joiner.join(f"{c}[{i}]" for c in child) + ")"
        if op == "X":
            return f"(!{more} || {child[0]}[{i + 1}])"
        own = self.node(n)
        if op == "G":
            return f"({child[0]}[{i}] && (!{more} || {own}[{i + 1}]))"
        if op == "W":
            return f"({child[1]}[{i}] || ({child[0]}[{i}] && (!{more} || {own}[{i + 1}])))"
        if op == "R":
            return f"({child[1]}[{i}] && ({child[0]}[{i}] || (!{more} || {own}[{i + 1}])))"
        raise ValueError(f"verify 不支持 {op}")

    def evaluate(self, root: dict) -> tuple[list[str], str]:
        """Straight-line code, last position first, so no loop depends on the verifier's unwinding."""
        top = self.node(root)
        lines = [f"  int {name}[{self.cap + 1}];" for name, _ in self.order]
        for i in range(self.cap - 1, -1, -1):
            body = " ".join(f"{name}[{i}] = {self.at(m, i)};" for name, m in self.order)
            lines.append(f"  if ({i} < n) {{ {body} }}")
        return lines, top


def _layout(tree: dict) -> tuple[dict[str, str], dict[str, int], dict[str, int]]:
    used = keys(tree)
    return used, {k: i for i, k in enumerate(used)}, {c: i for i, c in enumerate(constants(tree))}


def _struct(pid: str, tree: dict) -> list[str]:
    used, fields, present = _layout(tree)
    out = ["typedef struct {"]
    for key, i in fields.items():
        kind = used[key]
        ctype = "int" if kind == "谓词" else "long long"
        note = "谓词，0 或 1" if kind == "谓词" else "数量或位置（位置编码成一一对应的整数）"
        out.append(f"  {ctype} k{i}; /* {key}：{note} */")
    for name, i in present.items():
        out.append(f"  int p{i}; /* 实体 {name} 此刻有个体为 1，没有为 0（没有时含它的原子为假，细则 12.4） */")
    out.append(f"}} cv_{pid}_state;")
    init = [f".k{i} = 0" for i in fields.values()] + [f".p{i} = 1" for i in present.values()]
    out.append(f"#define CV_{pid}_INIT {{ {', '.join(init)} }} /* 先用它初始化，再填写每个字段 */")
    return out


TIMING = [
    "/* 用法（细则 11.11、12.2）：每个状态调用一次 observe，状态 i 写时刻 i 的值和从 i 到 i+1 这一步发生的事件。",
    "   先读出当前的值，再执行这一步，按这一步发生了什么填事件谓词，然后 observe；最后一个状态的事件谓词填 0。",
    "   共记录 steps + 1 个状态（游戏结束后也继续记录），最后调用一次 check。",
    "   结构体先用 CV_Rn_INIT 初始化：没有初始化的字段在验证器中可以取任意值。 */",
]


def stub(pid: str, tree: dict, steps: int, formula_text: str = "") -> str:
    """The header the alignment agent writes against before freezing: same names, no verdict (细则 11.7)."""
    p = f"cv_{pid}"
    return "\n".join(
        [
            f"/* 试验用的空壳（README 细则 11.11）：与检查时的头文件名字相同，但不判定命题。{pid}: {formula_text} */",
            f"#ifndef CV_{pid}_STUB_H",
            f"#define CV_{pid}_STUB_H",
            *TIMING,
            f"#define CV_{pid}_STEPS {steps}",
            *_struct(pid, tree),
            f"static int {p}_n = 0;",
            f"static void {p}_observe(const {p}_state *s) {{ (void)s; {p}_n = {p}_n + 1; }}",
            f"static void {p}_check(void) {{ }}",
            "#endif",
            "",
        ]
    )


def header(pid: str, tree: dict, steps: int, formula_text: str = "", nonce: str = "") -> str:
    """The C header cv_<pid>.h of 细则 11.11, checking every recorded prefix; `steps + 1` states at most.

    `nonce` is fresh for every verifier run, so a harness can neither pre-define the include guard nor
    forge a property with the same description.
    """
    cap = steps + 1
    _, fields, present = _layout(tree)
    body, top = _Gen(pid, fields, present, cap).evaluate(M.nnf(tree))
    trig = M._trigger(tree)
    fired = ["  int fired = 0;"]
    if trig is not None and not trig[1]:
        tlines, ttop = _Gen(pid, fields, present, cap).evaluate(M.nnf(trig[0]))
        fired += ["  {", *("  " + x for x in tlines)]
        fired += [f"    if ({i} < n && {ttop}[{i}]) fired = 1;" for i in range(cap)]
        fired.append("  }")
    p = f"cv_{pid}"

    def label(what: str) -> str:
        return _label(pid, what, nonce)

    return "\n".join(
        [
            "/* 由交叉验证程序生成（README 细则 11.11）：检查时使用重新生成的副本。 */",
            f"/* {pid}: {formula_text} */",
            f"#ifndef CV_{pid}_H_{nonce}",
            f"#define CV_{pid}_H_{nonce}",
            "#ifdef __CPROVER_assert",
            '#error "__CPROVER_assert 不得被重定义"',
            "#endif",
            *TIMING,
            f"#define CV_{pid}_STEPS {steps}",
            *_struct(pid, tree),
            f"static {p}_state {p}_s[{cap + 1}];",
            f"static int {p}_n = 0;",
            f"static int {p}_checked = 0;",
            f"static void {p}_eval(void) {{",
            f"  int n = {p}_n;",
            *body,
            f'  __CPROVER_assert(n == 0 || {top}[0], "{label(MAIN)}");',
            "}",
            f"static void {p}_observe(const {p}_state *s) {{",
            f'  __CPROVER_assert(!{p}_checked, "{label(AFTER)}");',
            f'  __CPROVER_assert({p}_n < {cap}, "{label(OVERFLOW)}");',
            f"  if ({p}_n < {cap}) {{ {p}_s[{p}_n] = *s; {p}_n = {p}_n + 1; {p}_eval(); }}",
            "}",
            f"static void {p}_check(void) {{",
            f"  int n = {p}_n;",
            f"  {p}_checked = 1;",
            *fired,
            f'  __CPROVER_assert(n == 0, "{label(REACH)}");',
            f'  __CPROVER_assert(n < {cap}, "{label(DEPTH)}");',
            f'  __CPROVER_assert(!fired, "{label(FIRED)}");',
            "}",
            "#endif",
            "",
        ]
    )


def write_headers(
    directory: Path, props: dict[str, dict], entities: set[str], steps: int, nonce: str = "", stubs: bool = False
) -> list[str]:
    """Headers for every proposition a verifier can decide; returns their ids."""
    directory.mkdir(parents=True, exist_ok=True)
    done = []
    for pid, p in props.items():
        if p.get("tree") is not None and not problems(p["tree"], entities):
            text = (
                stub(pid, p["tree"], steps, p.get("formula", ""))
                if stubs
                else header(pid, p["tree"], steps, p.get("formula", ""), nonce)
            )
            (directory / f"cv_{pid}.h").write_text(text, "utf-8")
            done.append(pid)
    return done


# ---------------------------------------------------------------------------------------------------
# Running the verifier and reading its verdict
# ---------------------------------------------------------------------------------------------------


def resolve(source: str, w: Path, align_dir: Path) -> Path | None:
    """A source of checks.json: `$CV_ALIGN/x` in align/, anything else relative to W; no `..`."""
    m = SOURCE.match(source)
    if not m or ".." in Path(m.group(2)).parts or Path(m.group(2)).is_absolute():
        return None
    base = align_dir if m.group(1) else w
    path = base / m.group(2)
    return path if path.is_file() else None


def command(
    engine: str, sources: list[Path], include: Path, unwind: int, out: Path, w: Path, unwindset: dict | None = None
) -> str:
    if engine != "cbmc":
        raise ValueError(f"不支持的验证器 {engine}")
    argv = ["cbmc", "--json-ui", "--trace", "--unwind", str(unwind), "--unwinding-assertions"]
    if unwindset:  # 细则 11.11: tighter bounds for single loops; every loop still gets an unwinding assertion
        argv += ["--unwindset", ",".join(f"{k}:{v}" for k, v in sorted(unwindset.items()))]
    # The generated headers come first, so a header of the same name in W cannot answer for them.
    argv += ["-I", str(include), "-I", str(w), *map(str, sources)]
    return " ".join(shlex.quote(a) for a in argv) + f" > {shlex.quote(str(out))} 2>&1"


def _states(trace: list[dict], pid: str, tree: dict) -> list[dict]:
    """The states the harness recorded on the failing execution, as trace states (细则 12.1, 13.4)."""
    _, fields, present = _layout(tree)
    names = {f"k{i}": k for k, i in fields.items()}
    entity = {f"p{i}": c for c, i in present.items()}
    pattern = re.compile(rf"^cv_{re.escape(pid)}_s\[(\d+)[uUlL]*\]\.([kp]\d+)$")
    count = f"cv_{pid}_n"
    raw: dict[int, dict] = {}
    recorded = 0
    for step in trace:
        if step.get("stepType") != "assignment":
            continue
        lhs = str(step.get("lhs", ""))
        value = _number((step.get("value") or {}).get("data"))
        if lhs == count and isinstance(value, int):
            recorded = value
            continue
        m = pattern.match(lhs)
        if m:
            raw.setdefault(int(m.group(1)), {})[m.group(2)] = value
    states = []
    for i in sorted(raw):
        if i >= recorded:  # static initialisation writes every slot; only recorded ones are states
            continue
        f = raw[i]
        values = {}
        for field, key in names.items():
            v = f.get(field, 0)
            values[key] = bool(v) if keys(tree)[key] == "谓词" and isinstance(v, int) else v
        listed = {name: ([name] if f.get(field, 0) else []) for field, name in entity.items()}
        states.append({"实体": listed, "值": values} if listed else {"值": values})
    return states


def _number(data):
    """CBMC writes integers with C suffixes, e.g. `3l`."""
    if isinstance(data, str):
        m = re.fullmatch(r"(-?\d+)[uUlL]*", data.strip())
        if m:
            return int(m.group(1))
    return data


def read_result(raw: str, pid: str, tree: dict, nonce: str, entities: set[str]) -> dict:
    """CBMC's JSON output → the result of this check (细则 11.11 执行与判定)."""
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {"result": M.ALIGN_FAILED, "reason": "验证器没有给出可读的结果：" + " ".join(raw.split())[-300:]}
    found: dict[str, dict] = {}
    errors = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        for r in item.get("result", []):
            found[r.get("description") or r.get("property", "")] = r
            found.setdefault(r.get("property", ""), r)
        if item.get("messageType") == "ERROR":
            errors.append(str(item.get("messageText", "")))
    if errors and not found:
        return {
            "result": M.ALIGN_FAILED,
            "reason": "不能编译或验证器出错：" + " ".join(" ".join(errors).split())[-300:],
        }

    def status(what: str) -> str | None:
        r = found.get(_label(pid, what, nonce))
        return r.get("status") if r else None

    if status(MAIN) is None:
        return {
            "result": M.ALIGN_FAILED,
            "reason": f"验证器的结果中没有 {pid} 的性质：驱动程序没有包含 cv_{pid}.h 或从未记录状态",
        }
    # 细则 13.3: a real counterexample comes first; problems of the harness come after it.
    if status(MAIN) == "FAILURE":
        states = _states(found[_label(pid, MAIN, nonce)].get("trace") or [], pid, tree)
        cex = {"state": max(len(states) - 1, 0), "last_states": states[-5:], "engine": "cbmc"}
        try:  # 细则 11.11: the checker of 细则 13.2 must agree on the verifier's counterexample
            agreed = M.judge(tree, states, entities)["result"] == M.VIOLATED
        except Exception:  # a disagreement of any kind is reported, never a crash
            agreed = False
        if not agreed:
            return {
                "result": M.ALIGN_FAILED,
                "reason": "验证器的反例在轨迹判定上不是确定违反：生成的代码与 checker 不一致",
                "counterexample": cex,
            }
        return {"result": M.VIOLATED, "counterexample": cex}
    unwind = [r for name, r in found.items() if ".unwind." in name and r.get("status") == "FAILURE"]
    checks = [
        (bool(unwind), "展开不足：unwind 小于程序中某个循环需要的次数"),
        (status(OVERFLOW) == "FAILURE", "记录的状态超过 steps + 1 个"),
        (status(AFTER) == "FAILURE", "调用检查之后又记录了状态"),
        (status(REACH) != "FAILURE", "检查点不可达：没有一条执行在记录了状态之后调用检查"),
        (status(DEPTH) != "FAILURE", "没有一条执行记录满 steps + 1 个状态，范围不实"),
        (status(MAIN) != "SUCCESS", f"主性质的结果是 {status(MAIN)}"),
    ]
    for bad, why in checks:
        if bad:
            return {"result": M.ALIGN_FAILED, "reason": f"{why}（细则 11.11）"}
    out = {"result": M.PRESUMED_TRUE}
    if M._trigger(tree) is not None:
        out["triggered"] = status(FIRED) == "FAILURE"
    return out


ASSUME = re.compile(r"__CPROVER_assume\s*\((.*)\)\s*;")


def assumptions(sources: list[Path], align_dir: Path) -> list[str]:
    """The assumptions written in the alignment agent's files, for the report's 范围 (细则 11.11)."""
    out = []
    for path in sources:
        if align_dir in path.parents:
            text = path.read_text(encoding="utf-8", errors="replace")
            out += [" ".join(m.group(1).split()) for m in ASSUME.finditer(text)]
    return list(dict.fromkeys(out))[:10]


def run(
    check: dict,
    props: dict[str, dict],
    entities: set[str],
    w: Path,
    align_dir: Path,
    config: dict,
    run_command,
    timeout: float,
    cache: dict | None = None,
) -> dict:
    """One verify check. `run_command` is cv_run.run_command (the wrapper of 细则 5.5).

    A harness may include the headers of several propositions, so every header is generated, from
    propositions.json, never from the alignment agent's copy. One verifier run serves every check with
    the same sources, steps and unwind (`cache`).
    """
    prop = props[check["proposition"]]
    pid = prop["id"]
    engine = config.get("verify_engine", "cbmc")
    if engine not in ENGINES or shutil.which(engine) is None:
        return {"result": M.ALIGN_FAILED, "reason": f"环境中没有验证器 {engine}"}
    sources = [resolve(s, w, align_dir) for s in check["sources"]]
    if any(s is None for s in sources):
        return {"result": M.ALIGN_FAILED, "reason": "sources 中有不存在的文件"}
    key = json.dumps([check["sources"], check["steps"], check["unwind"], check.get("unwindset") or {}], sort_keys=True)
    cache = {} if cache is None else cache
    if key not in cache:
        nonce = secrets.token_hex(8)
        with tempfile.TemporaryDirectory(prefix="cv-verify-") as tmp:
            include = Path(tmp) / "include"
            write_headers(include, props, entities, check["steps"], nonce)
            out = Path(tmp) / "out.json"
            cmd = command(engine, sources, include, check["unwind"], out, w, check.get("unwindset"))
            done = run_command(cmd, w, {"CV_ALIGN": str(align_dir), "CV_W": str(w)}, timeout)
            raw = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
            cache[key] = (None if done.get("timeout") else raw, nonce)
    raw, nonce = cache[key]
    if raw is None:
        return {"result": M.ALIGN_FAILED, "reason": "验证器超时（细则 11.11）"}
    result = read_result(raw, pid, prop["tree"], nonce, entities)
    names = ", ".join(os.path.basename(s) for s in check["sources"])
    assumed = assumptions(sources, align_dir)
    result["scope"] = (
        f"{check['steps']} 步以内、驱动程序假设之内的全部输入（{engine}，驱动程序 {names}"
        + (f"；假设 {'；'.join(assumed)}" if assumed else "")
        + "）"
    )
    return result
