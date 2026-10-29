"""Four-valued judgement of a formula on a finite trace (README 第 12、13 节; 算法1 第7行).

Temporal propositions are rewritten (′ -> next-step comparisons, 细则 10.3), put in negation normal
form and progressed state by state. ⊥ means 确定违反 and ⊤ 确定成立; anything left at the end is
judged with finite-trace semantics: weak next, G, W, R hold at the end, strong next, F, U, M do not.
"""

from __future__ import annotations

import json
import math

import cv_formula as F

# Results, worst first (细则 13.3).
CHECK_FAILED = "失败（检查）"
VIOLATED = "确定违反"
ALIGN_FAILED = "失败（对齐）"
PRESUMED_FALSE = "推定为假"
PRESUMED_TRUE = "推定为真"
SATISFIED = "确定成立"
PASSED = "通过"
ORDER = {CHECK_FAILED: 0, VIOLATED: 0, ALIGN_FAILED: 1, PRESUMED_FALSE: 2, PRESUMED_TRUE: 3, SATISFIED: 4, PASSED: 5}
FAILING = {CHECK_FAILED, VIOLATED, ALIGN_FAILED}


def worst(results: list[str]) -> str:
    return min(results, key=lambda r: ORDER[r])


class AlignError(Exception):
    """The trace cannot be used for this formula: a missing value, a bad entity list (细则 11.6)."""


class _Absent(Exception):
    """An argument names an individual not present in this state (细则 12.3)."""


TRUE = {"op": "T"}
FALSE = {"op": "⊥"}


# ---------------------------------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------------------------------


class Trace:
    def __init__(self, states: list[dict], entities: set[str]):
        self.states = states
        self.entities = entities

    def __len__(self) -> int:
        return len(self.states)

    def state(self, k: int) -> dict:
        if k < 0 or k >= len(self.states):
            raise AlignError(f"轨迹没有第 {k} 个状态")
        return self.states[k]

    def individuals(self, k: int, entity: str | None) -> list:
        listed = self.state(k).get("实体")
        if not isinstance(listed, dict):
            if entity is None:
                return sorted(self.entities)
            return [entity]
        if entity is None:
            return [x for xs in listed.values() if isinstance(xs, list) for x in xs]
        xs = listed.get(entity, [])
        if not isinstance(xs, list):
            raise AlignError(f"状态 {k} 的 实体.{entity} 不是列表")
        return xs

    def constant(self, k: int, entity: str):
        listed = self.state(k).get("实体")
        if not isinstance(listed, dict) or entity not in listed:
            return entity
        xs = listed[entity]
        if not isinstance(xs, list) or len(xs) != 1:
            raise AlignError(f"状态 {k} 中实体 {entity} 应恰有一个个体，实际为 {xs!r}（细则 12.4）")
        return xs[0]

    def present(self, k: int, ind) -> bool:
        listed = self.state(k).get("实体")
        if not isinstance(listed, dict):
            return True
        return any(isinstance(xs, list) and ind in xs for xs in listed.values()) or ind in self.entities

    def value(self, k: int, key: str):
        values = self.state(k).get("值")
        if not isinstance(values, dict) or key not in values:
            raise AlignError(f"状态 {k} 缺少 {key}（细则 12.3）")
        return values[key]


def _key(name: str, ids: list) -> str:
    return name if not ids else f"{name}({','.join(str(i) for i in ids)})"


def _ind(n: dict, tr: Trace, k: int):
    if n["op"] == "const":
        return tr.constant(k, n["name"])
    if n["op"] in ("ind", "lit"):
        return n["value"]
    if n["op"] == "var":
        raise AlignError(f"变量 {n['name']} 没有被约束")
    raise AlignError("参数应是个体")


def term_value(n: dict, tr: Trace, k: int):
    op = n["op"]
    if op in ("lit", "ind"):
        return n["value"]
    if op == "const":
        return tr.constant(k, n["name"])
    if op == "at":
        return term_value(n["args"][0], tr, n["k"])
    if op == "term":
        if n["name"] == F.ACTION and not n["args"]:
            state = tr.state(k)
            if "动作" not in state:
                raise AlignError(f"状态 {k} 缺少 动作")
            return state["动作"]
        ids = [_ind(a, tr, k) for a in n["args"]]
        for i in ids:
            if not tr.present(k, i):
                raise _Absent
        return tr.value(k, _key(n["name"], ids))
    if op in F.ARITHMETIC:
        a, b = (term_value(x, tr, k) for x in n["args"])
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b)):
            raise AlignError(f"算术的操作数不是数：{a!r}、{b!r}")
        if op == "div" and b == 0:
            raise AlignError("除以零")
        return {"add": a + b, "sub": a - b, "mul": a * b, "div": a / b if op == "div" and b else 0}[op]
    if op == "call":
        raise AlignError("调用只能用 command 检查")
    raise AlignError(f"无法在轨迹上求值：{op}")


def _same(a, b) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def compare(op: str, a, b) -> bool:
    if op == "eq":
        return _same(a, b)
    if op == "ne":
        return not _same(a, b)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b)):
        raise AlignError(f"大小比较的操作数不是数：{a!r}、{b!r}")
    return {"lt": a < b, "gt": a > b, "le": a <= b, "ge": a >= b}[op]


def atom_value(n: dict, tr: Trace, k: int) -> bool:
    op = n["op"]
    try:
        if op == "at":
            return atom_value(n["args"][0], tr, n["k"])
        if op == "pred":
            if n["name"] in tr.entities:
                return _ind(n["args"][0], tr, k) in tr.individuals(k, n["name"])
            ids = [_ind(a, tr, k) for a in n["args"]]
            for i in ids:
                if not tr.present(k, i):
                    return False
            v = tr.value(k, _key(n["name"], ids))
            if not isinstance(v, bool):
                raise AlignError(f"谓词 {_key(n['name'], ids)} 的值应是布尔值，实际为 {v!r}")
            return v
        if op in F.COMPARISONS:
            a, b = (term_value(x, tr, k) for x in n["args"])
            return compare(op, a, b)
    except _Absent:
        return False
    if op == "lit" and isinstance(n["value"], bool):
        return n["value"]
    if op in ("struct", "fails"):
        raise AlignError("结构命题和调用只能用 command 检查")
    raise AlignError(f"不是原子：{op}")


def holds(n: dict, tr: Trace, k: int) -> bool:
    """A non-temporal formula at state k (quantifiers range over the individuals listed there)."""
    op = n["op"]
    if op == "not":
        return not holds(n["args"][0], tr, k)
    if op == "and":
        return all(holds(a, tr, k) for a in n["args"])
    if op == "or":
        return any(holds(a, tr, k) for a in n["args"])
    if op == "implies":
        return not holds(n["args"][0], tr, k) or holds(n["args"][1], tr, k)
    if op in F.QUANTIFIERS:
        values = [holds(subst(n["args"][0], n["var"], i), tr, k) for i in tr.individuals(k, n.get("type"))]
        if op == "forall":
            return all(values)
        if op == "exists":
            return any(values)
        return sum(values) == 1
    return atom_value(n, tr, k)


def subst(n: dict, name: str, value) -> dict:
    if n["op"] == "var" and n["name"] == name:
        return {"op": "ind", "args": [], "value": value}
    if n["op"] in F.QUANTIFIERS and n["var"] == name:
        return n
    if not n.get("args"):
        return n
    return {**n, "args": [subst(a, name, value) for a in n["args"]]}


# ---------------------------------------------------------------------------------------------------
# Negation normal form (细则 10.3)
# ---------------------------------------------------------------------------------------------------


def _lit(atom: dict, neg: bool) -> dict:
    return {"op": "L", "atom": atom, "neg": neg}


def nnf(n: dict, neg: bool = False) -> dict:
    op = n["op"]
    if op == "lit" and isinstance(n["value"], bool):
        return TRUE if n["value"] != neg else FALSE
    if op in F.COMPARISONS and n["args"][0]["op"] == "next":
        # L′ op R  ≡  X (L op c), c the value of R now. Its negation takes the strong next.
        return {"op": "Xcmp", "cmp": op, "L": n["args"][0]["args"][0], "R": n["args"][1], "neg": neg}
    if op in ("pred", "at", "fails", "struct") or op in F.COMPARISONS:
        return _lit(n, neg)
    if op == "not":
        return nnf(n["args"][0], not neg)
    if op in ("and", "or"):
        flipped = {"and": "or", "or": "and"}[op] if neg else op
        return {"op": flipped, "args": [nnf(a, neg) for a in n["args"]]}
    if op == "implies":
        return nnf(F.mk_or(F.mk_not(n["args"][0]), n["args"][1]), neg)
    if op == "X":
        return {"op": "Xs" if neg else "X", "args": [nnf(n["args"][0], neg)]}
    if op in ("G", "F"):
        dual = {"G": "F", "F": "G"}[op] if neg else op
        return {"op": dual, "args": [nnf(n["args"][0], neg)]}
    if op in ("U", "W"):
        dual = {"U": "R", "W": "M"}[op] if neg else op
        return {"op": dual, "args": [nnf(a, neg) for a in n["args"]]}
    if op in F.QUANTIFIERS:
        return {"op": "Q", "node": n, "neg": neg}
    raise AlignError(f"无法求值：{op}")


def is_liveness(tree: dict) -> bool:
    """细则 13.5: the NNF has a liveness part (F, U, M or the strong next)."""
    found = False

    def go(n: dict) -> None:
        nonlocal found
        if n["op"] in ("F", "U", "M", "Xs") or (n["op"] == "Xcmp" and n["neg"]):
            found = True
        for a in n.get("args", []):
            go(a)
        if n["op"] == "Q":
            body = n["node"]["args"][0]
            go(nnf(body, n["neg"]))
            go(nnf(body, not n["neg"]))

    go(nnf(tree))
    return found


# ---------------------------------------------------------------------------------------------------
# Progression
# ---------------------------------------------------------------------------------------------------


def _canon(n: dict) -> str:
    return json.dumps(n, sort_keys=True, ensure_ascii=False)


def _junction(op: str, items: list[dict]) -> dict:
    unit, zero = (TRUE, FALSE) if op == "and" else (FALSE, TRUE)
    out: list[dict] = []
    seen: set[str] = set()
    for item in items:
        parts = item["args"] if item["op"] == op else [item]
        for part in parts:
            if part is zero or part["op"] == zero["op"]:
                return zero
            if part["op"] == unit["op"]:
                continue
            key = _canon(part)
            if key not in seen:
                seen.add(key)
                out.append(part)
    if not out:
        return unit
    return out[0] if len(out) == 1 else {"op": op, "args": out}


def _later(kind: str, n: dict) -> dict:
    """An obligation on the next state: weak (holds if the trace ends) or strong (fails if it ends)."""
    if n["op"] == "T":
        return TRUE
    if n["op"] == "⊥" and kind == "St":
        return FALSE
    return {"op": kind, "args": [n]}


def progress(n: dict, tr: Trace, k: int) -> dict:
    op = n["op"]
    if op in ("T", "⊥"):
        return n
    if op == "L":
        return TRUE if atom_value(n["atom"], tr, k) != n["neg"] else FALSE
    if op == "Xcmp":
        try:
            c = term_value(n["R"], tr, k)
            atom = F.node(n["cmp"], n["L"], F.lit(c))
        except _Absent:
            atom = F.lit(False)
        return _later("St" if n["neg"] else "Wk", nnf(atom, n["neg"]))
    if op in ("and", "or"):
        return _junction(op, [progress(a, tr, k) for a in n["args"]])
    if op in ("Wk", "St"):
        return progress(n["args"][0], tr, k)
    if op == "X":
        return _later("Wk", n["args"][0])
    if op == "Xs":
        return _later("St", n["args"][0])
    if op == "G":
        return _junction("and", [progress(n["args"][0], tr, k), _later("Wk", n)])
    if op == "F":
        return _junction("or", [progress(n["args"][0], tr, k), _later("St", n)])
    a, b = n["args"] if op in ("U", "W", "R", "M") else (None, None)
    if op in ("U", "W"):
        keep = _later("St" if op == "U" else "Wk", n)
        return _junction("or", [progress(b, tr, k), _junction("and", [progress(a, tr, k), keep])])
    if op in ("R", "M"):
        keep = _later("Wk" if op == "R" else "St", n)
        return _junction("and", [progress(b, tr, k), _junction("or", [progress(a, tr, k), keep])])
    if op == "Q":
        q = n["node"]
        parts = []
        domain = tr.individuals(k, q.get("type"))
        body = q["args"][0]
        if q["op"] == "exists1":
            for i in domain:
                others = [F.mk_not(subst(body, q["var"], j)) for j in domain if j != i]
                parts.append(F.mk_and(subst(body, q["var"], i), *others) if others else subst(body, q["var"], i))
            expanded = F.mk_or(*parts) if parts else F.lit(False)
            return progress(nnf(expanded, n["neg"]), tr, k)
        inst = [nnf(subst(body, q["var"], i), n["neg"]) for i in domain]
        conj = (q["op"] == "forall") != n["neg"]
        if not inst:
            return TRUE if conj else FALSE
        return _junction("and" if conj else "or", [progress(x, tr, k) for x in inst])
    raise AlignError(f"无法推进：{op}")


def finish(n: dict) -> bool:
    op = n["op"]
    if op == "T":
        return True
    if op == "⊥":
        return False
    if op == "Wk":
        return True
    if op == "St":
        return False
    if op == "and":
        return all(finish(a) for a in n["args"])
    if op == "or":
        return any(finish(a) for a in n["args"])
    raise AlignError(f"残余公式中不应有 {op}")


def judge(tree: dict, states: list[dict], entities: set[str], max_states: int | None = None) -> dict:
    """Judge one proposition on one trace. Raises AlignError when the trace cannot be used."""
    if not states:
        raise AlignError("轨迹为空（细则 12.6）")
    truncated = False
    if max_states is not None and len(states) > max_states:
        states, truncated = states[:max_states], True
    tr = Trace(states, entities)
    _require_keys(tree, tr)
    cls = F.classify(tree)
    if cls == "结构":
        raise AlignError("结构命题不能用 trace 检查（细则 11.4）")
    if cls == "具体事实":
        ok = holds(tree, tr, 0)
        return {"result": PASSED if ok else CHECK_FAILED, "state": 0, "truncated": truncated}
    residual = nnf(tree)
    for k in range(len(tr)):
        residual = progress(residual, tr, k)
        if residual["op"] in ("T", "⊥"):
            return {"result": SATISFIED if residual["op"] == "T" else VIOLATED, "state": k, "truncated": truncated}
    return {
        "result": PRESUMED_TRUE if finish(residual) else PRESUMED_FALSE,
        "state": len(tr) - 1,
        "truncated": truncated,
    }


def _static_atoms(n: dict, out: list[dict]) -> list[dict]:
    """Predicates and terms whose arguments are all constants, outside any fixed-state subscript."""
    if n["op"] == "at":
        return out
    if (n["op"] in ("pred", "term") and n["args"] and all(a["op"] in ("const", "lit") for a in n["args"])) or (
        n["op"] == "term" and not n["args"] and n["name"] != F.ACTION
    ):
        out.append(n)
    for a in n.get("args", []):
        _static_atoms(a, out)
    return out


def _require_keys(tree: dict, tr: Trace) -> None:
    """细则 12.3: every state gives every key the formula uses, whether or not evaluation reaches it."""
    atoms = [a for a in _static_atoms(tree, []) if a["name"] not in tr.entities]
    for k in range(len(tr)):
        for a in atoms:
            ids = [_ind(x, tr, k) for x in a["args"]]
            if all(tr.present(k, i) for i in ids):
                tr.value(k, _key(a["name"], ids))


def scenario_matches(tree: dict, states: list[dict], entities: set[str]) -> bool:
    """细则 12.5: a 从 S 出发 proposition is judged only on traces whose actions match its antecedent."""
    antecedent = tree["args"][0]
    last = max((n["k"] for n in F.walk(tree) if n["op"] == "at"), default=0)
    if len(states) <= last:
        return False
    try:
        return holds(antecedent, Trace(states, entities), 0)
    except AlignError:
        return False
