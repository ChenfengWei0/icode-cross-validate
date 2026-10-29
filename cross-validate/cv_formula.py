"""Formulas: the JSON syntax tree, printing, parsing and classification (README 第 10 节).

A node is a dict {"op": ..., "args": [...]} with extra fields for some ops (name, var, type, value,
k, kind). The printed form uses the document's notation; parsing the printed form gives the same
tree back (细则 10.2). Parsing needs the declarations, because a bare name can be an entity, a
zero-argument quantity or a predicate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

SUBSCRIPTS = "₀₁₂₃₄₅₆₇₈₉"
STRUCT_NAMES = {
    "存在": "exists",
    "签名": "signature",
    "包含": "contains",
    "调用": "calls",
    "只由": "only",
    "表示": "represents",
}
STRUCT_PRINT = {v: k for k, v in STRUCT_NAMES.items()}
ACTION = "动作"  # the special term holding a trace state's action (细则 10.2)
TEMPORAL_OPS = {"G", "F", "X", "U", "W"}
COMPARISONS = {"eq": "=", "ne": "≠", "lt": "<", "gt": ">", "le": "≤", "ge": "≥"}
ARITHMETIC = {"add": "+", "sub": "−", "mul": "×", "div": "÷"}
QUANTIFIERS = {"exists": "∃", "exists1": "∃!", "forall": "∀"}
VARIABLE = re.compile(r"[a-z][0-9]?")


class FormulaError(ValueError):
    """A formula or sentence that cannot be parsed or does not type-check."""


# ---------------------------------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------------------------------


@dataclass
class Decls:
    """The subtask's declarations (细则 7.1): name -> category, and parameters for the functional ones."""

    category: dict[str, str] = field(default_factory=dict)  # 实体 谓词 位置 数量 动作 接口
    params: dict[str, list[str]] = field(default_factory=dict)
    meaning: dict[str, str] = field(default_factory=dict)

    def of(self, category: str) -> set[str]:
        return {n for n, c in self.category.items() if c == category}

    def kind(self, name: str) -> str | None:
        return self.category.get(name)

    def is_term(self, name: str) -> bool:
        return self.category.get(name) in ("位置", "数量")

    def to_json(self) -> dict:
        return {"category": self.category, "params": self.params, "meaning": self.meaning}

    @classmethod
    def from_json(cls, data: dict) -> Decls:
        return cls(dict(data["category"]), {k: list(v) for k, v in data["params"].items()}, dict(data["meaning"]))


# ---------------------------------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------------------------------


def node(op: str, *args: dict, **fields) -> dict:
    return {"op": op, "args": list(args), **fields}


def var(name: str) -> dict:
    return node("var", name=name)


def const(name: str) -> dict:
    return node("const", name=name)


def lit(value) -> dict:
    return node("lit", value=value)


def code(name: str) -> dict:
    return node("code", name=name)


def term(name: str, *args: dict) -> dict:
    return node("term", *args, name=name)


def pred(name: str, *args: dict) -> dict:
    return node("pred", *args, name=name)


def mk_not(a: dict) -> dict:
    return node("not", a)


def _flat(op: str, items) -> dict:
    out: list[dict] = []
    for item in items:
        out.extend(item["args"] if item["op"] == op else [item])
    return out[0] if len(out) == 1 else node(op, *out)


def mk_and(*items: dict) -> dict:
    return _flat("and", items)


def mk_or(*items: dict) -> dict:
    return _flat("or", items)


def implies(a: dict, b: dict) -> dict:
    return node("implies", a, b)


def walk(n: dict):
    yield n
    for a in n.get("args", []):
        yield from walk(a)


def free_vars(n: dict, bound: frozenset = frozenset()) -> set[str]:
    if n["op"] == "var":
        return set() if n["name"] in bound else {n["name"]}
    if n["op"] in QUANTIFIERS:
        return free_vars(n["args"][0], bound | {n["var"]})
    out: set[str] = set()
    for a in n["args"]:
        out |= free_vars(a, bound)
    return out


def classify(n: dict) -> str:
    """细则 10.4: the class follows from the tree, not from the proposition's name."""
    ops = {m["op"] for m in walk(n)}
    if "struct" in ops:
        return "结构"
    if ops & (TEMPORAL_OPS | {"next"}):
        return "时序"
    return "具体事实"


# ---------------------------------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------------------------------

# Formula levels, loosest first. Quantifiers extend to the right and are parenthesised when nested.
_LEVEL = {"implies": 1, "U": 2, "W": 2, "or": 3, "and": 4, "not": 5, "G": 5, "F": 5, "X": 5}
_ATOM = 6


def _lit(value) -> str:
    if isinstance(value, str):
        return f"「{value}」"
    return json.dumps(value, ensure_ascii=False)


def _expr(n: dict, ctx: int = 0) -> str:
    """Terms and arithmetic. Levels: + − 1, × ÷ 2, primary 3."""
    op = n["op"]
    if op in ARITHMETIC:
        level = 1 if op in ("add", "sub") else 2
        text = f"{_expr(n['args'][0], level)} {ARITHMETIC[op]} {_expr(n['args'][1], level + 1)}"
        return f"({text})" if level < ctx else text
    if op in ("var", "const"):
        return n["name"]
    if op == "lit":
        return _lit(n["value"])
    if op == "code":
        return f"`{n['name']}`"
    if op in ("term", "pred", "call"):
        name = f"`{n['name']}`" if op == "call" else n["name"]
        return f"{name}({', '.join(_expr(a) for a in n['args'])})" if n["args"] or op == "call" else name
    if op == "next":
        inner = n["args"][0]
        return f"{_expr(inner, 3)}′" if inner["op"] not in ARITHMETIC else f"({_expr(inner)})′"
    if op == "at":
        inner = n["args"][0]
        sub = "".join(SUBSCRIPTS[int(d)] for d in str(n["k"]))
        if inner["op"] in ("term", "pred", "var", "const", "lit"):
            return f"{_expr(inner, 3)}{sub}"
        return f"({_expr(inner) if inner['op'] in ARITHMETIC else to_text(inner)}){sub}"
    if op == "fails":
        return f"失败({_expr(n['args'][0])})"
    raise FormulaError(f"not a term: {op}")


def _formula(n: dict, ctx: int) -> str:
    op = n["op"]
    if op in QUANTIFIERS:
        head = QUANTIFIERS[op] + n["var"] + (f":{n['type']}" if n.get("type") else "")
        text = f"{head} {_formula(n['args'][0], 0)}"
        return f"({text})" if ctx > 0 else text
    if op in COMPARISONS:
        a, b = n["args"]
        text = f"{_expr(a)} {COMPARISONS[op]} {_expr(b)}"
        return f"({text})" if ctx > 5 else text
    if op == "struct":
        kind = n["kind"]
        if kind == "signature":
            text = f"签名({_expr(n['args'][0])}) = {_expr(n['args'][1])}"
            return f"({text})" if ctx > 5 else text
        return f"{STRUCT_PRINT[kind]}({', '.join(_expr(a) for a in n['args'])})"
    if op in ("pred", "fails", "at"):
        text = _expr(n)
        return f"({text})" if op == "at" and ctx > 5 and n["args"][0]["op"] != "pred" else text
    if op == "lit" and isinstance(n["value"], bool):
        return _lit(n["value"])
    level = _LEVEL.get(op)
    if level is None:
        raise FormulaError(f"not a formula: {op}")
    if op in ("not", "G", "F", "X"):
        symbol = "¬" if op == "not" else op + " "
        inner = n["args"][0]
        child = _formula(inner, 5 if inner["op"] in ("not", "G", "F", "X") else _ATOM)
        if op != "not" and child.startswith("("):
            symbol = op
        text = symbol + child
    elif op == "implies":
        text = f"{_formula(n['args'][0], 2)} → {_formula(n['args'][1], 1)}"
    elif op in ("U", "W"):
        text = f"{_formula(n['args'][0], 3)} {op} {_formula(n['args'][1], 2)}"
    else:
        sym = " ∧ " if op == "and" else " ∨ "
        text = sym.join(_formula(a, level + 1) for a in n["args"])
    return f"({text})" if level < ctx else text


def to_text(n: dict) -> str:
    """Print a formula in the document's notation (细则 10.1)."""
    return _formula(n, 0)


# ---------------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------------

_SINGLE = {
    "(": "(",
    "（": "(",
    ")": ")",
    "）": ")",
    ",": ",",
    "，": ",",
    "¬": "¬",
    "!": "¬",
    "∧": "∧",
    "&": "∧",
    "∨": "∨",
    "|": "∨",
    "→": "→",
    "=": "=",
    "≠": "≠",
    "<": "<",
    ">": ">",
    "≤": "≤",
    "≥": "≥",
    "+": "+",
    "−": "−",
    "×": "×",
    "*": "×",
    "÷": "÷",
    "/": "÷",
    "′": "′",
    "'": "′",
    ":": ":",
    "∀": "∀",
}
_ASCII_PAIRS = {"->": "→", "<=": "≤", ">=": "≥", "!=": "≠"}
_STOP = set(_SINGLE) | set("「」`[]{}∃- \t\n") | set(SUBSCRIPTS)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Tok:
    kind: str  # sym ident num str code json sub
    text: str
    value: object = None


def _scan_json(s: str, i: int) -> int:
    """Index after the bracket that closes the one at s[i], skipping JSON strings."""
    depth, j, in_str = 0, i, False
    while j < len(s):
        c = s[j]
        if in_str:
            if c == "\\":
                j += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise FormulaError("括号不配对")


def tokenize(s: str, decls: Decls) -> list[Tok]:
    out: list[Tok] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
        elif s[i : i + 2] in _ASCII_PAIRS:
            out.append(Tok("sym", _ASCII_PAIRS[s[i : i + 2]]))
            i += 2
        elif c == "∃":
            if s[i + 1 : i + 2] == "!":
                out.append(Tok("sym", "∃!"))
                i += 2
            else:
                out.append(Tok("sym", "∃"))
                i += 1
        elif (
            c == "-"
            and (m := _NUMBER.match(s, i))
            and (not out or (out[-1].kind == "sym" and out[-1].text not in (")", "′")))
        ):
            out.append(Tok("num", m.group(), json.loads(m.group())))
            i = m.end()
        elif c == "-":
            out.append(Tok("sym", "−"))
            i += 1
        elif c in _SINGLE:
            out.append(Tok("sym", _SINGLE[c]))
            i += 1
        elif c in SUBSCRIPTS:
            j = i
            while j < len(s) and s[j] in SUBSCRIPTS:
                j += 1
            out.append(Tok("sub", s[i:j], int("".join(str(SUBSCRIPTS.index(d)) for d in s[i:j]))))
            i = j
        elif c == "_" and (m := re.compile(r"_(\d+)").match(s, i)):
            out.append(Tok("sub", m.group(), int(m.group(1))))
            i = m.end()
        elif c == "「":
            j = s.find("」", i)
            if j < 0:
                raise FormulaError("「 没有配对的 」")
            out.append(Tok("str", s[i : j + 1], s[i + 1 : j]))
            i = j + 1
        elif c == "`":
            j = s.find("`", i + 1)
            if j < 0:
                raise FormulaError("反引号不配对")
            out.append(Tok("code", s[i + 1 : j]))
            i = j + 1
        elif c in "[{":
            j = _scan_json(s, i)
            try:
                out.append(Tok("json", s[i:j], json.loads(s[i:j])))
            except json.JSONDecodeError as e:
                raise FormulaError(f"JSON 字面量不合法：{s[i:j]}") from e
            i = j
        elif c.isdigit():
            m = _NUMBER.match(s, i)
            out.append(Tok("num", m.group(), json.loads(m.group())))
            i = m.end()
        else:
            j = i
            while j < len(s) and s[j] not in _STOP:
                j += 1
            if j == i:
                raise FormulaError(f"无法识别的字符：{c}")
            word = s[i:j]
            m = re.fullmatch(r"(.+?)_(\d+)", word)
            if m and (decls.kind(m.group(1)) or m.group(1) == ACTION):
                out.append(Tok("ident", m.group(1)))
                out.append(Tok("sub", "_" + m.group(2), int(m.group(2))))
            else:
                out.append(Tok("ident", word))
            i = j
    return out


class _Parser:
    def __init__(self, toks: list[Tok], decls: Decls):
        self.t, self.i, self.d = toks, 0, decls

    def peek(self, k: int = 0) -> Tok | None:
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def at(self, text: str, kind: str = "sym") -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == kind and tok.text == text

    def take(self, text: str | None = None) -> Tok:
        tok = self.peek()
        if tok is None or (text is not None and not (tok.text == text and tok.kind in ("sym", "ident"))):
            raise FormulaError(f"期望 {text or '更多内容'}，得到 {tok.text if tok else '结尾'}")
        self.i += 1
        return tok

    # formulas ---------------------------------------------------------------------------------------
    def formula(self) -> dict:
        left = self.until()
        if self.at("→"):
            self.take()
            return implies(left, self.formula())
        return left

    def until(self) -> dict:
        left = self.disj()
        tok = self.peek()
        if tok and tok.kind == "ident" and tok.text in ("U", "W"):
            self.take()
            return node(tok.text, left, self.until())
        return left

    def disj(self) -> dict:
        items = [self.conj()]
        while self.at("∨"):
            self.take()
            items.append(self.conj())
        return mk_or(*items)

    def conj(self) -> dict:
        items = [self.unary()]
        while self.at("∧"):
            self.take()
            items.append(self.unary())
        return mk_and(*items)

    def unary(self) -> dict:
        tok = self.peek()
        if tok is None:
            raise FormulaError("公式不完整")
        if tok.kind == "sym" and tok.text == "¬":
            self.take()
            return mk_not(self.unary())
        if tok.kind == "ident" and tok.text in ("G", "F", "X"):
            self.take()
            return node(tok.text, self.unary())
        if tok.kind == "sym" and tok.text in ("∃", "∃!", "∀"):
            self.take()
            name = self.take().text
            if not VARIABLE.fullmatch(name):
                raise FormulaError(f"量词后应是变量：{name}")
            typ = None
            if self.at(":"):
                self.take()
                typ = self.take().text
            op = {"∃": "exists", "∃!": "exists1", "∀": "forall"}[tok.text]
            q = node(op, self.formula(), var=name)
            if typ:
                q["type"] = typ
            return q
        return self.primary()

    def primary(self) -> dict:
        if self.at("("):
            save = self.i
            try:
                self.take()
                inner = self.formula()
                self.take(")")
                nxt = self.peek()
                if nxt is None or not (nxt.kind == "sub" or (nxt.kind == "sym" and nxt.text in "=≠<>≤≥+−×÷′")):
                    return inner
                if nxt.kind == "sub" and inner["op"] not in ARITHMETIC:
                    self.take()
                    return node("at", inner, k=nxt.value)
            except FormulaError:
                pass
            self.i = save
        left = self.expr()
        tok = self.peek()
        if tok and tok.kind == "sym" and tok.text in "=≠<>≤≥":
            self.take()
            right = self.expr()
            op = {v: k for k, v in COMPARISONS.items()}[tok.text]
            if left["op"] == "sig":
                if op != "eq" or right["op"] != "lit" or not isinstance(right["value"], str):
                    raise FormulaError("签名只能写作 签名(`N`) = 「…」")
                return node("struct", left["args"][0], right, kind="signature")
            return node(op, left, right)
        if left["op"] in ("pred", "struct", "fails") or (left["op"] == "at" and left["args"][0]["op"] == "pred"):
            return left
        if left["op"] == "lit" and isinstance(left["value"], bool):
            return left
        raise FormulaError(f"这里需要命题，得到的是项：{_expr(left)}")

    # terms -------------------------------------------------------------------------------------------
    def expr(self) -> dict:
        left = self.mul()
        while self.peek() and self.peek().kind == "sym" and self.peek().text in "+−":
            op = "add" if self.take().text == "+" else "sub"
            left = node(op, left, self.mul())
        return left

    def mul(self) -> dict:
        left = self.postfix()
        while self.peek() and self.peek().kind == "sym" and self.peek().text in "×÷":
            op = "mul" if self.take().text == "×" else "div"
            left = node(op, left, self.postfix())
        return left

    def postfix(self) -> dict:
        base = self.base()
        while True:
            tok = self.peek()
            if tok and tok.kind == "sym" and tok.text == "′":
                self.take()
                base = node("next", base)
            elif tok and tok.kind == "sub":
                self.take()
                base = node("at", base, k=tok.value)
            else:
                return base

    def args(self) -> list[dict]:
        self.take("(")
        out: list[dict] = []
        if not self.at(")"):
            out.append(self.expr())
            while self.at(","):
                self.take()
                out.append(self.expr())
        self.take(")")
        return out

    def base(self) -> dict:
        tok = self.peek()
        if tok is None:
            raise FormulaError("公式不完整")
        if tok.kind == "sym" and tok.text == "(":
            self.take()
            inner = self.expr()
            self.take(")")
            return inner
        self.take()
        if tok.kind in ("num", "json"):
            return lit(tok.value)
        if tok.kind == "str":
            return lit(tok.value)
        if tok.kind == "code":
            return node("call", *self.args(), name=tok.text) if self.at("(") else code(tok.text)
        if tok.kind != "ident":
            raise FormulaError(f"意外的符号：{tok.text}")
        name = tok.text
        if name in ("true", "false", "null"):
            return lit(json.loads(name))
        if self.at("("):
            args = self.args()
            if name in STRUCT_NAMES and self.d.kind(name) is None:
                kind = STRUCT_NAMES[name]
                return node("sig", *args) if kind == "signature" else node("struct", *args, kind=kind)
            if name == "失败" and self.d.kind(name) is None:
                if len(args) != 1 or args[0]["op"] != "call":
                    raise FormulaError("失败(…) 中应是一次调用")
                return node("fails", args[0])
            kind = self.d.kind(name)
            if kind == "谓词" or kind == "实体":
                return pred(name, *args)
            if kind in ("位置", "数量"):
                return term(name, *args)
            raise FormulaError(f"未声明的名称：{name}")
        if VARIABLE.fullmatch(name) and self.d.kind(name) is None:
            return var(name)
        if name == ACTION and self.d.kind(name) is None:
            return term(ACTION)
        kind = self.d.kind(name)
        if kind == "实体":
            return const(name)
        if kind in ("位置", "数量"):
            return term(name)
        if kind == "谓词":
            return pred(name)
        raise FormulaError(f"未声明的名称：{name}")


def parse(text: str, decls: Decls) -> dict:
    """Parse a formula written in the document's notation, or its ASCII spelling (细则 10.1)."""
    p = _Parser(tokenize(text.strip().rstrip("。"), decls), decls)
    out = p.formula()
    if p.peek() is not None:
        raise FormulaError(f"多余的内容：{p.peek().text}")
    return out


# ---------------------------------------------------------------------------------------------------
# Type checking (细则 9.1 第 3 项)
# ---------------------------------------------------------------------------------------------------


def type_errors(n: dict, decls: Decls) -> list[str]:
    errors: list[str] = []
    types: dict[str, str | None] = {}

    def arg_ok(a: dict, want: str, where: str) -> None:
        if a["op"] == "const":
            if a["name"] != want:
                errors.append(f"{where} 的参数应是 {want}，写的是 {a['name']}")
        elif a["op"] == "var":
            have = types.get(a["name"])
            if have is None:
                errors.append(f"变量 {a['name']} 的类型不明，{where} 要求 {want}")
            elif have != want:
                errors.append(f"变量 {a['name']} 的类型是 {have}，{where} 要求 {want}")
        elif a["op"] == "lit" and isinstance(a["value"], str):
            pass  # an individual named directly
        else:
            errors.append(f"{where} 的参数应是 {want} 的个体")

    def go(m: dict) -> None:
        op = m["op"]
        if op == "struct" and m["kind"] == "represents" and m["args"][0]["op"] == "pred" and not m["args"][0]["args"]:
            if decls.kind(m["args"][0]["name"]) != "谓词":
                errors.append(f"未声明的名称：{m['args'][0]['name']}")
            return
        if op in QUANTIFIERS:
            if m.get("type") and decls.kind(m["type"]) != "实体":
                errors.append(f"量词的类型 {m['type']} 不是已声明的实体")
            saved = types.get(m["var"], "unset")
            types[m["var"]] = m.get("type")
            go(m["args"][0])
            if saved == "unset":
                types.pop(m["var"], None)
            else:
                types[m["var"]] = saved
            return
        if op in ("pred", "term") and not (op == "term" and m["name"] == ACTION):
            name = m["name"]
            if decls.kind(name) == "实体":
                if len(m["args"]) != 1:
                    errors.append(f"{name}(…) 表示个体属于实体 {name}，只能有一个参数")
                elif m["args"][0]["op"] not in ("var", "const", "lit"):
                    errors.append(f"{name}(…) 的参数应是个体")
                return
            want = decls.params.get(name)
            if want is None:
                errors.append(f"未声明的名称：{name}")
                return
            if len(want) != len(m["args"]):
                errors.append(f"{name} 声明了 {len(want)} 个参数，写了 {len(m['args'])} 个")
            else:
                for a, w in zip(m["args"], want, strict=True):
                    arg_ok(a, w, name)
            return
        if op == "code" and m["name"] not in decls.of("接口"):
            errors.append(f"代码名 `{m['name']}` 没有声明为接口")
        if op == "call" and m["name"] not in decls.of("接口"):
            errors.append(f"调用的 `{m['name']}` 没有声明为接口")
        for a in m["args"]:
            go(a)

    go(n)
    errors.extend(f"变量 {v} 没有被量词约束" for v in sorted(free_vars(n)))
    return errors
