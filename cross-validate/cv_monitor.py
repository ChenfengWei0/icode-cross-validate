"""Four-valued judgement of a formula on a finite trace (README 第 12、13 节; 算法1 第7行).

Temporal propositions are rewritten (′ -> next-step comparisons, 细则 10.3), put in negation normal
form and progressed state by state. ⊥ means 确定违反 and ⊤ 确定成立; anything left at the end is
judged with finite-trace semantics: weak next, G, W, R hold at the end, strong next, F, U, M do not.
"""

from __future__ import annotations

import itertools
import json
import time

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
    def __init__(self, states: list[dict], entities: set[str], tolerance: float | None = None):
        self.states = states
        self.entities = entities
        self.tolerance = tolerance  # 细则 11.12: io alignment compares non-integers relatively
        self.end_final = False  # 细则 11.12: the trace ends where the run ends (set by judge)
        # 细则 12.4: entities listed in some state; the others are singletons named by the entity.
        self.listed: set[str] = set()
        for k, state in enumerate(states):
            listed = state.get("实体")
            if listed is None:
                continue
            if not isinstance(listed, dict):
                raise AlignError(f"状态 {k} 的 实体 应是对象")
            for name, xs in listed.items():
                if not isinstance(xs, list) or not all(_scalar(x) for x in xs):
                    raise AlignError(f"状态 {k} 的 实体.{name} 应是由字符串或数组成的列表（细则 12.4）")
                self.listed.add(name)
        # Per state, every listed individual, for present(): a set answers what scanning the lists answered.
        self._listed_sets: list[set | None] = [
            ({x for xs in st["实体"].values() for x in xs} if isinstance(st.get("实体"), dict) else None)
            for st in states
        ]
        # 细则 12.4: individuals are compared as text (the number 1 and the string 「1」 are one individual)
        self._listed_text: list[set | None] = [None if xs is None else {str(x) for x in xs} for xs in self._listed_sets]

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
        if entity is None:  # 细则 12.4: every listed individual, each number once
            return _unique([x for xs in listed.values() if isinstance(xs, list) for x in xs])
        if entity not in self.listed:
            return [entity]  # 细则 12.4: never listed: one individual, named by the entity
        xs = listed.get(entity, [])
        if not isinstance(xs, list):
            raise AlignError(f"状态 {k} 的 实体.{entity} 不是列表")
        return _unique(xs)

    def member(self, k: int, entity: str, ind) -> bool:
        """细则 12.4: ind is an individual of entity in state k (compared as text)."""
        xs = self.individuals(k, entity)
        return ind in xs or str(ind) in {str(x) for x in xs}

    def constant(self, k: int, entity: str):
        if entity not in self.listed:
            return entity
        xs = (self.state(k).get("实体") or {}).get(entity, [])
        if not xs:
            raise _Absent  # 细则 12.4: nothing there now; atoms and comparisons with it are false
        if len(xs) != 1:
            raise AlignError(
                f"状态 {k} 中实体 {entity} 应恰有一个个体，实际为 {xs!r}：命题不带量词地用 {entity} 作参数，"
                f"指它唯一的个体，每个状态的 实体 下只能列一个（细则 12.4）"
            )
        return xs[0]

    def present(self, k: int, ind) -> bool:
        self.state(k)
        listed = self._listed_sets[k]
        if listed is None:
            return True
        try:
            if ind in listed:
                return True
        except TypeError:  # an unhashable value is never a listed individual (they are scalars, 细则 12.4)
            return False
        if _scalar(ind) and str(ind) in self._listed_text[k]:
            return True
        return isinstance(ind, str) and ind in self.entities and ind not in self.listed

    def value(self, k: int, name: str, ids: list):
        """细则 12.4: the only individual of an entity may be written by the entity's name in a key."""
        key = _key(name, ids)
        values = self.state(k).get("值")
        if not isinstance(values, dict):
            raise AlignError(f"状态 {k} 缺少 {key}（细则 12.3）")
        if key in values:
            return values[key]
        # 细则 12.4: the only individual of an entity may be written by the entity's name. Several entities can
        # have the same only individual (矩阵 [1] and 子矩阵 [1]); the key the module wrote decides which.
        for names in itertools.product(*(self._entities_of_only(k, i) for i in ids)):
            alias = _key(name, list(names))
            if alias in values:
                return values[alias]
        raise AlignError(f"状态 {k} 缺少 {key}（细则 12.3）")

    def _entities_of_only(self, k: int, ind) -> list:
        listed = self.state(k).get("实体")
        out = [ind]
        if isinstance(listed, dict):
            for entity, xs in listed.items():
                if isinstance(xs, list) and len(xs) == 1 and str(xs[0]) == str(ind):
                    out.append(entity)
        return out


def _unique(xs: list) -> list:
    seen, out = set(), []
    for x in xs:
        key = (type(x).__name__, x) if _scalar(x) else repr(x)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def _scalar(x) -> bool:
    return isinstance(x, (str, int, float)) and not isinstance(x, bool)


def _key(name: str, ids: list) -> str:
    return name if not ids else f"{name}({','.join(str(i) for i in ids)})"


def _ind(n: dict, tr: Trace, k: int, env: dict | None = None):
    if n["op"] == "var" and env and n["name"] in env:
        return env[n["name"]]
    if n["op"] == "const":
        return tr.constant(k, n["name"])
    if n["op"] in ("ind", "lit"):
        return n["value"]
    if n["op"] == "var":
        raise AlignError(f"变量 {n['name']} 没有被约束")
    raise AlignError("参数应是个体")


def term_value(n: dict, tr: Trace, k: int, env: dict | None = None):
    op = n["op"]
    if op == "var" and env and n["name"] in env:
        return env[n["name"]]
    if op in ("lit", "ind"):
        return n["value"]
    if op == "const":
        return tr.constant(k, n["name"])
    if op == "at":
        return term_value(n["args"][0], tr, n["k"], env)
    if op == "term":
        if n["name"] == F.ACTION and not n["args"]:
            state = tr.state(k)
            if "动作" not in state:
                raise AlignError(f"状态 {k} 缺少 动作")
            return state["动作"]
        ids = [_ind(a, tr, k, env) for a in n["args"]]
        for i in ids:
            if not tr.present(k, i):
                raise _Absent
        return tr.value(k, n["name"], ids)
    if op in F.ARITHMETIC:
        a, b = (term_value(x, tr, k, env) for x in n["args"])
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b)):
            raise AlignError(f"算术的操作数不是数：{a!r}、{b!r}")
        if op == "div" and b == 0:
            raise AlignError("除以零")
        return {"add": a + b, "sub": a - b, "mul": a * b, "div": a / b if op == "div" and b else 0}[op]
    if op == "call":
        raise AlignError("调用只能用 command 检查")
    raise AlignError(f"无法在轨迹上求值：{op}")


def _same(a, b, tolerance: float | None = None) -> bool:
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, int) and isinstance(b, int):
        return a == b  # 细则 12.3: integers exactly
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if tolerance is not None:  # 细则 11.12
            return abs(a - b) <= tolerance * max(1.0, abs(a), abs(b))
        return abs(a - b) <= 1e-9
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def compare(op: str, a, b, tolerance: float | None = None) -> bool:
    if op == "eq":
        return _same(a, b, tolerance)
    if op == "ne":
        return not _same(a, b, tolerance)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b)):
        raise AlignError(f"大小比较的操作数不是数：{a!r}、{b!r}")
    if tolerance is not None and _same(a, b, tolerance):  # 细则 11.12: equal within the tolerance
        return op in ("le", "ge")
    return {"lt": a < b, "gt": a > b, "le": a <= b, "ge": a >= b}[op]


def atom_value(n: dict, tr: Trace, k: int, env: dict | None = None) -> bool:
    op = n["op"]
    try:
        if op == "at":
            return atom_value(n["args"][0], tr, n["k"], env)
        if op == "pred":
            if n["name"] in tr.entities:
                return tr.member(k, n["name"], _ind(n["args"][0], tr, k, env))
            ids = [_ind(a, tr, k, env) for a in n["args"]]
            for i in ids:
                if not tr.present(k, i):
                    return False
            v = tr.value(k, n["name"], ids)
            if not isinstance(v, bool):
                raise AlignError(f"谓词 {_key(n['name'], ids)} 的值应是布尔值，实际为 {v!r}")
            return v
        if op in F.COMPARISONS:
            a, b = (term_value(x, tr, k, env) for x in n["args"])
            return compare(op, a, b, tr.tolerance)
    except _Absent:
        return False
    if op == "lit" and isinstance(n["value"], bool):
        return n["value"]
    if op in ("struct", "fails"):
        raise AlignError("结构命题和调用只能用 command 检查")
    raise AlignError(f"不是原子：{op}")


def holds(n: dict, tr: Trace, k: int, env: dict | None = None) -> bool:
    """A non-temporal formula at state k (quantifiers range over the individuals listed there); env binds the
    variables of enclosing quantifiers."""
    op = n["op"]
    if op == "not":
        return not holds(n["args"][0], tr, k, env)
    # Every operand is evaluated, so a missing key is found even where the result is already known (细则 12.3).
    if op == "and":
        return all([holds(a, tr, k, env) for a in n["args"]])  # noqa: C419 — no short circuit
    if op == "or":
        return any([holds(a, tr, k, env) for a in n["args"]])  # noqa: C419 — no short circuit
    if op == "implies":
        a, b = holds(n["args"][0], tr, k, env), holds(n["args"][1], tr, k, env)
        return not a or b
    if op in F.QUANTIFIERS:
        body, var = n["args"][0], n["var"]
        values = [holds(body, tr, k, {**(env or {}), var: i}) for i in tr.individuals(k, n.get("type"))]
        if op == "forall":
            return all(values)
        if op == "exists":
            return any(values)
        return sum(values) == 1
    return atom_value(n, tr, k, env)


# 细则 11.12: at the last state of a run, a quantifier whose body has no temporal operator is evaluated by holds()
# with its variables bound in an environment, instead of a substituted copy of the body for every individual.
_TIMED = set(F.TEMPORAL_OPS) | {"next", "X", "Xs", "R", "M", "Xcmp"}


def _untimed(n: dict) -> bool:
    return not any(m["op"] in _TIMED for m in F.walk(n))


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
    if op in F.COMPARISONS and any(m["op"] == "next" for m in F.walk(n)):
        # 细则 10.3: X (the comparison, primed terms next, the others now). Negated: the strong next.
        return {"op": "Xcmp", "atom": n, "neg": neg}
    if op == "next":
        return nnf(F.node("X", n["args"][0]), neg)
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
            if n["node"]["op"] == "exists1":  # its expansion negates the body too
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


def _later(kind: str, n: dict, end: bool = False) -> dict:
    """An obligation on the next state: weak (holds if the trace ends) or strong (fails if it ends).

    Only Wk(⊤) = ⊤ and St(⊥) = ⊥ are equivalences on finite traces; Wk(⊥) and St(⊤) stay (细则 13.2).
    When the trace is known to end here (end), the obligation is settled now, as finish() would settle it.
    """
    if end:
        return TRUE if kind == "Wk" else FALSE
    if n["op"] == "T" and kind == "Wk":
        return TRUE
    if n["op"] == "⊥" and kind == "St":
        return FALSE
    return {"op": kind, "args": [n]}


def _now_values(n: dict, tr: Trace, k: int, primed: bool = False) -> dict:
    """A primed comparison with its unprimed terms replaced by their values at state k (细则 10.3)."""
    op = n["op"]
    if op == "next":
        if primed or any(m["op"] == "next" for m in F.walk(n["args"][0])):
            raise AlignError("′ 不能叠用")
        return _now_values(n["args"][0], tr, k, True)
    if op in F.COMPARISONS or op in F.ARITHMETIC:
        return {**n, "args": [_now_values(a, tr, k, primed) for a in n["args"]]}
    if primed or op in ("lit", "ind"):
        return n
    return F.lit(term_value(n, tr, k))


def progress(n: dict, tr: Trace, k: int) -> dict:
    op = n["op"]
    if op in ("T", "⊥"):
        return n
    end = tr.end_final and k == len(tr) - 1
    if op == "L":
        return TRUE if atom_value(n["atom"], tr, k) != n["neg"] else FALSE
    if op == "Xcmp":
        try:
            atom = _now_values(n["atom"], tr, k)
        except _Absent:
            atom = F.lit(False)  # 细则 10.3, 12.3: false at the next state, if there is one
        return _later("St" if n["neg"] else "Wk", nnf(atom, n["neg"]), end)
    if op in ("and", "or"):
        return _junction(op, [progress(a, tr, k) for a in n["args"]])
    if op in ("Wk", "St"):
        return progress(n["args"][0], tr, k)
    if op == "X":
        return _later("Wk", n["args"][0], end)
    if op == "Xs":
        return _later("St", n["args"][0], end)
    if op == "G":
        return _junction("and", [progress(n["args"][0], tr, k), _later("Wk", n, end)])
    if op == "F":
        return _junction("or", [progress(n["args"][0], tr, k), _later("St", n, end)])
    a, b = n["args"] if op in ("U", "W", "R", "M") else (None, None)
    if op in ("U", "W"):
        keep = _later("St" if op == "U" else "Wk", n, end)
        return _junction("or", [progress(b, tr, k), _junction("and", [progress(a, tr, k), keep])])
    if op in ("R", "M"):
        keep = _later("Wk" if op == "R" else "St", n, end)
        return _junction("and", [progress(b, tr, k), _junction("or", [progress(a, tr, k), keep])])
    if op == "Q":
        q = n["node"]
        if end and _untimed(q):
            return TRUE if holds(q, tr, k) != n["neg"] else FALSE
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


def judge(
    tree: dict,
    states: list[dict],
    entities: set[str],
    max_states: int | None = None,
    deadline: float | None = None,
    tolerance: float | None = None,
    end_final: bool = False,
) -> dict:
    """Judge one proposition on one trace. Raises AlignError when the trace cannot be used.

    end_final: the trace ends where the run ends (细则 11.12), so obligations on later states are settled at the
    last state as finish() settles them; the verdict is the same, without carrying them as a residual."""
    if not states:
        raise AlignError("轨迹为空（细则 12.6）")
    truncated = False
    if max_states is not None and len(states) > max_states:
        states, truncated = states[:max_states], True
    tr = Trace(states, entities, tolerance)
    tr.end_final = end_final and not truncated
    cls = F.classify(tree)
    if cls == "结构":
        raise AlignError("结构命题不能用 trace 检查（细则 11.4）")
    atoms = [a for a in _static_atoms(tree, []) if a["name"] not in tr.entities]
    if cls == "具体事实":
        _require_keys(atoms, tr, 0)
        ok = holds(tree, tr, 0)
        return {"result": PASSED if ok else CHECK_FAILED, "state": 0, "truncated": truncated}
    residual = nnf(tree)
    for k in range(len(tr)):
        # 细则 12.3: a missing key fails the alignment, unless the result was decided before it.
        _require_keys(atoms, tr, k)
        if deadline is not None and time.monotonic() > deadline:
            raise AlignError(f"判定在第 {k} 个状态超时，公式可能嵌套过深（细则 11.8）")
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


def _require_keys(atoms: list[dict], tr: Trace, k: int) -> None:
    """细则 12.3: state k gives every key the formula uses, whether or not evaluation reaches it."""
    for a in atoms:
        try:
            ids = [_ind(x, tr, k) for x in a["args"]]
        except _Absent:
            continue
        if all(tr.present(k, i) for i in ids):
            tr.value(k, a["name"], ids)


def _trigger(tree: dict) -> tuple[dict, list[dict]] | None:
    """The antecedent P and the quantifiers of [∀x] G [∀x] (P → …), or None for any other shape."""
    quantifiers, n, always = [], tree, False
    while n["op"] == "forall" or (n["op"] == "G" and not always):
        if n["op"] == "G":
            always = True
        else:
            quantifiers.append(n)
        n = n["args"][0]
    if not always or n["op"] != "implies":
        return None
    antecedent = n["args"][0]
    if any(m["op"] in F.TEMPORAL_OPS or m["op"] in ("next", "at") for m in F.walk(antecedent)):
        return None
    return antecedent, quantifiers


def triggered(tree: dict, states: list[dict], entities: set[str]) -> bool | None:
    """细则 13.7: did P of G(P → …) ever hold on this trace? None when that cannot be said."""
    shape = _trigger(tree)
    if shape is None:
        return None
    antecedent, quantifiers = shape
    tr = Trace(states, entities)

    def fires(n: dict, rest: list[dict], k: int) -> bool:
        if not rest:
            return holds(n, tr, k)
        q = rest[0]
        return any(fires(subst(n, q["var"], i), rest[1:], k) for i in tr.individuals(k, q.get("type")))

    try:
        return any(fires(antecedent, quantifiers, k) for k in range(len(states)))
    except AlignError:
        return None


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


def _conjuncts(n: dict) -> list[dict]:
    return [c for a in n["args"] for c in _conjuncts(a)] if n["op"] == "and" else [n]


def _action_at(n: dict):
    """(k, a) for a conjunct 动作ₖ = a of a 从 S 出发 antecedent, else None."""
    if n["op"] != "eq":
        return None
    a, b = n["args"]
    if a["op"] == "at" and a["args"][0]["op"] == "term" and a["args"][0]["name"] == F.ACTION and b["op"] == "lit":
        return a["k"], b["value"]
    return None


def _arguments(n: dict) -> list:
    """Individuals and literals written as arguments of a predicate or term in n."""
    return [
        a["value"] for m in F.walk(n) if m["op"] in ("term", "pred") for a in m["args"] if a["op"] in ("lit", "ind")
    ]


def scenario_problem(tree: dict, states: list[dict], entities: set[str]) -> str | None:
    """细则 11.7: why a trial trace does not reach a 从 S 出发 antecedent, or None when it does.

    Only the antecedent is looked at; the consequent, the proposition's verdict, is never evaluated.
    """
    if scenario_matches(tree, states, entities):
        return None
    parts = _conjuncts(tree["args"][0])
    wanted = [a for _, a in sorted(x for x in map(_action_at, parts) if x)]
    last = max((n["k"] for n in F.walk(tree) if n["op"] == "at"), default=0)
    seen = [st.get("动作") if isinstance(st, dict) else None for st in states[: len(wanted)]]
    if seen != wanted:
        return f"动作序列与句子不符：句子要求依次执行 {wanted}，CV_RUN=0 的轨迹开头是 {seen}"
    if len(states) <= last:
        return f"轨迹只有 {len(states)} 个状态，执行完句子中的动作之后至少要有 {last + 1} 个"
    try:
        tr = Trace(states, entities)
        for c in parts:
            if _action_at(c) or holds(c, tr, 0):
                continue
            absent = [v for v in _arguments(c) if _scalar(v) and not tr.present(0, v)]
            if absent:
                return f"起始条件中作参数的个体 {absent[0]!r} 不在状态 0 的 实体 中：作参数的个体要列在 实体 下"
            return f"状态 0 不满足起始条件 {F.to_text(c)}"
    except AlignError as e:
        return f"起始条件无法求值：{e}"
    return "前件没有成立"
