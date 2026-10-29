"""Controlled sentences -> formula trees (README 第 8 节; 算法1 第5行).

Every sentence either has exactly one parse or fails with a reason. The pattern is chosen from the
leading keywords and the clause structure (细则 8.5); terms and atoms are resolved against the
declarations (细则 8.2, 8.3).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import cv_formula as F
from cv_formula import Decls, FormulaError

# 细则 8.8, laid out as in the README so the two can be compared by eye.
RESERVED = frozenset(
    """的 是 不 没有 并且 或者 如果 那么 等于 不等于 小于 大于 不小于 不大于 加 减 乘 除
    总是 从不 只要 就 下一步 最终 一旦 此后一直 在 之前 不会 存在一个 恰好存在一个 对所有 使
    从 出发 依次执行 之后 调用 得到 失败 初始时 代码中存在 签名是 包含 字段 方法 类型 函数 模块
    不调用 只有 修改 表示 元素 存在 签名 只由 动作""".split()  # noqa: SIM905
)
CMP_WORDS = {"等于": "eq", "不等于": "ne", "小于": "lt", "大于": "gt", "不小于": "ge", "不大于": "le"}
ARITH_WORDS = {"加": "add", "减": "sub", "乘": "mul", "除": "div"}
COMPOUNDS = {"代码中存在表示": ["代码中存在", "表示"], "的元素": ["的", "元素"], "的签名是": ["的", "签名是"]}
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# Pattern names used by the checks (细则 9.1 第 5 项).
STRUCTURE_PATTERNS = {"结构：存在", "结构：签名", "结构：包含", "结构：调用", "结构：只有", "结构：表示"}
EXAMPLE_PATTERNS = {"调用得到", "调用失败", "初始时", "从出发", "具体事实"}


@dataclass
class W:
    """A sentence token. `glued` is true when no space separates it from the previous token."""

    kind: str  # word clause comma list lp rp str code json num
    text: str
    glued: bool = False
    value: object = None


def lex(sentence: str, decls: Decls) -> list[W]:
    s = sentence.strip()
    while s.endswith("。"):
        s = s[:-1].rstrip()
    out: list[W] = []
    i, depth, glued = 0, 0, False

    def add(tok: W) -> None:
        nonlocal glued
        tok.glued = glued and bool(out)
        out.append(tok)
        glued = True

    while i < len(s):
        c = s[i]
        if c.isspace():
            glued = False
            i += 1
        elif c in "，,":
            add(W("comma" if depth > 0 else "clause", c))
            glued = False
            i += 1
        elif c == "、":
            add(W("list", c))
            glued = False
            i += 1
        elif c in "(（":
            depth += 1
            add(W("lp", "("))
            i += 1
        elif c in ")）":
            depth -= 1
            add(W("rp", ")"))
            i += 1
        elif c == "「":
            j = s.find("」", i)
            if j < 0:
                raise FormulaError("「 没有配对的 」")
            add(W("str", s[i : j + 1], value=s[i + 1 : j]))
            i = j + 1
        elif c == "`":
            j = s.find("`", i + 1)
            if j < 0:
                raise FormulaError("反引号不配对")
            add(W("code", s[i + 1 : j]))
            i = j + 1
        elif c in "[{":
            j = F._scan_json(s, i)
            try:
                add(W("json", s[i:j], value=json.loads(s[i:j])))
            except json.JSONDecodeError as e:
                raise FormulaError(f"JSON 字面量不合法：{s[i:j]}") from e
            i = j
        else:
            j = i
            while j < len(s) and not s[j].isspace() and s[j] not in "，,、()（）「`[{":
                j += 1
            for word in _split_word(s[i:j], decls):
                add(W("word", word))
            i = j
    for tok in out:
        if tok.kind == "word":
            if NUMBER.fullmatch(tok.text):
                tok.kind, tok.value = "num", json.loads(tok.text)
            elif tok.text in ("true", "false", "null"):
                tok.kind, tok.value = "json", json.loads(tok.text)
    return out


def _split_word(word: str, decls: Decls) -> list[str]:
    """Undo glued forms: compounds of keywords, and 没有p / 不p (细则 8.3)."""
    if word in COMPOUNDS:
        return COMPOUNDS[word]
    if word in RESERVED or decls.kind(word):
        return [word]
    for neg in ("没有", "不"):
        rest = word[len(neg) :]
        if word.startswith(neg) and rest and decls.kind(rest) == "谓词":
            return [neg, rest]
    return [word]


# ---------------------------------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------------------------------


def _is(tok: W | None, *words: str) -> bool:
    return tok is not None and tok.kind == "word" and tok.text in words


class _P:
    def __init__(self, toks: list[W], decls: Decls):
        self.t, self.i, self.d = toks, 0, decls

    def peek(self, k: int = 0) -> W | None:
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def take(self, *words: str) -> W:
        tok = self.peek()
        if tok is None:
            raise FormulaError(f"句子不完整，期望 {'/'.join(words) or '更多内容'}")
        if words and not _is(tok, *words):
            raise FormulaError(f"期望 {'/'.join(words)}，得到 {tok.text}")
        self.i += 1
        return tok

    def done(self) -> bool:
        return self.i >= len(self.t)

    # state propositions (细则 8.4) ------------------------------------------------------------------
    def prop(self) -> dict:
        if _is(self.peek(), "如果"):
            self.take()
            left = self.disj()
            self.take("那么")
            return F.implies(left, self.disj())
        return self.disj()

    def disj(self) -> dict:
        items = [self.conj()]
        while _is(self.peek(), "或者"):
            self.take()
            items.append(self.conj())
        return F.mk_or(*items)

    def conj(self) -> dict:
        items = [self.neg()]
        while _is(self.peek(), "并且"):
            self.take()
            items.append(self.neg())
        return F.mk_and(*items)

    def neg(self) -> dict:
        tok = self.peek()
        if _is(tok, "不"):
            self.take()
            nxt = self.peek()
            if nxt is not None and nxt.kind == "lp":
                return F.mk_not(self.paren_prop())
            if nxt is not None and nxt.kind == "word" and self._is_call(self.i):
                return F.mk_not(self.atom())
            raise FormulaError("前缀 不 只能作用于函数写法的原子或括号")
        if tok is not None and tok.kind == "lp" and self._paren_is_prop():
            return self.paren_prop()
        return self.atom()

    def paren_prop(self) -> dict:
        self.take_kind("lp")
        inner = self.prop()
        self.take_kind("rp")
        return inner

    def take_kind(self, kind: str) -> W:
        tok = self.peek()
        if tok is None or tok.kind != kind:
            raise FormulaError(f"期望 {'(' if kind == 'lp' else ')'}，得到 {tok.text if tok else '结尾'}")
        self.i += 1
        return tok

    def _matching(self, i: int) -> int:
        depth = 0
        for j in range(i, len(self.t)):
            if self.t[j].kind == "lp":
                depth += 1
            elif self.t[j].kind == "rp":
                depth -= 1
                if depth == 0:
                    return j
        raise FormulaError("括号不配对")

    def _paren_is_prop(self) -> bool:
        """A parenthesis opens a proposition unless a comparison or arithmetic word follows it."""
        end = self._matching(self.i)
        after = self.t[end + 1] if end + 1 < len(self.t) else None
        return not _is(after, *CMP_WORDS, *ARITH_WORDS)

    def _is_call(self, i: int) -> bool:
        return i + 1 < len(self.t) and self.t[i + 1].kind == "lp" and self.t[i + 1].glued

    def _span(self) -> list[W]:
        """Tokens of one atom: up to 并且/或者/那么 or the end, at parenthesis depth 0."""
        depth, j = 0, self.i
        while j < len(self.t):
            tok = self.t[j]
            if tok.kind == "lp":
                depth += 1
            elif tok.kind == "rp":
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and _is(tok, "并且", "或者", "那么"):
                break
            j += 1
        span = self.t[self.i : j]
        self.i = j
        return span

    def atom(self) -> dict:
        span = self._span()
        if not span:
            raise FormulaError("缺少原子命题")
        sub = _P(span, self.d)
        # Comparison: E1 op E2 (细则 8.3).
        depth, cut = 0, None
        for k, tok in enumerate(span):
            if tok.kind == "lp":
                depth += 1
            elif tok.kind == "rp":
                depth -= 1
            elif depth == 0 and _is(tok, *CMP_WORDS):
                cut = k
                break
        if cut is not None:
            left, right = _P(span[:cut], self.d), _P(span[cut + 1 :], self.d)
            a, b = left.expr(), right.expr()
            if not (left.done() and right.done()):
                raise FormulaError(f"比较式两边不是表达式：{_show(span)}")
            return F.node(CMP_WORDS[span[cut].text], a, b)
        # x 是 T, x 不是 T.
        if len(span) == 3 and _is(span[1], "是", "不是"):
            x = sub.term_tok(span[0])
            t = span[2]
            if t.kind != "word" or self.d.kind(t.text) != "实体":
                raise FormulaError(f"{t.text} 不是已声明的实体")
            atom = F.pred(t.text, x)
            return F.mk_not(atom) if span[1].text == "不是" else atom
        # Function form p(a, b).
        if span[0].kind == "word" and sub._is_call(0):
            name = span[0].text
            sub.i = 1
            args = sub.call_args()
            if not sub.done():
                raise FormulaError(f"原子后有多余的内容：{_show(span)}")
            kind = self.d.kind(name)
            if kind not in ("谓词", "实体"):
                raise FormulaError(f"{name} 不是已声明的谓词")
            return F.pred(name, *args)
        # Natural form: a p, a p b, a 不 p, a 没有 p (b).
        negated = len(span) >= 3 and _is(span[1], "不", "没有")
        rest = span[2:] if negated else span[1:]
        if len(span) >= 2 and rest and rest[0].kind == "word" and self.d.kind(rest[0].text) == "谓词":
            p = rest[0].text
            args = [sub.term_tok(span[0])] + [sub.term_tok(tok) for tok in rest[1:]]
            atom = F.pred(p, *args)
            return F.mk_not(atom) if negated else atom
        raise FormulaError(f"无法识别的原子命题：{_show(span)}")

    # expressions and terms (细则 8.2) ----------------------------------------------------------------
    def expr(self) -> dict:
        left = self.mul()
        while _is(self.peek(), "加", "减"):
            op = ARITH_WORDS[self.take().text]
            left = F.node(op, left, self.mul())
        return left

    def mul(self) -> dict:
        left = self.primary()
        while _is(self.peek(), "乘", "除"):
            op = ARITH_WORDS[self.take().text]
            left = F.node(op, left, self.primary())
        return left

    def primary(self) -> dict:
        tok = self.peek()
        if tok is None:
            raise FormulaError("表达式不完整")
        if tok.kind == "lp":
            self.take_kind("lp")
            inner = self.expr()
            self.take_kind("rp")
            return inner
        if tok.kind == "word" and self._is_call(self.i):
            self.i += 1
            args = self.call_args()
            kind = self.d.kind(tok.text)
            if kind not in ("位置", "数量"):
                raise FormulaError(f"{tok.text} 不是已声明的位置或数量")
            return F.term(tok.text, *args)
        self.i += 1
        return self.term_tok(tok)

    def call_args(self) -> list[dict]:
        self.take_kind("lp")
        args: list[dict] = []
        if self.peek() is not None and self.peek().kind != "rp":
            args.append(self.expr())
            while self.peek() is not None and self.peek().kind == "comma":
                self.i += 1
                args.append(self.expr())
        self.take_kind("rp")
        return args

    def term_tok(self, tok: W) -> dict:
        if tok.kind in ("num", "json"):
            return F.lit(tok.value)
        if tok.kind == "str":
            return F.lit(tok.value)
        if tok.kind != "word":
            raise FormulaError(f"这里需要一个项，得到 {tok.text}")
        word = tok.text
        if word in RESERVED and word != F.ACTION:
            raise FormulaError(f"这里需要一个项，得到保留字 {word}")
        candidates = []
        kind = self.d.kind(word)
        if kind == "实体":
            candidates.append(F.const(word))
        elif kind in ("位置", "数量") and not self.d.params.get(word):
            candidates.append(F.term(word))
        elif F.VARIABLE.fullmatch(word) and kind is None:
            candidates.append(F.var(word))
        elif word == F.ACTION and kind is None:
            candidates.append(F.term(F.ACTION))
        for k, ch in enumerate(word):
            if ch != "的" or k == 0 or k == len(word) - 1:
                continue
            left, right = word[:k], word[k + 1 :]
            if self.d.kind(right) not in ("位置", "数量") or len(self.d.params.get(right, [])) != 1:
                continue
            if self.d.kind(left) == "实体":
                candidates.append(F.term(right, F.const(left)))
            elif F.VARIABLE.fullmatch(left) and self.d.kind(left) is None:
                candidates.append(F.term(right, F.var(left)))
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise FormulaError(f"无法识别的项：{word}（不是已声明的实体、位置或数量，也不能按“a的f”切分）")
        raise FormulaError(f"项有歧义：{word}")


def _show(toks: list[W]) -> str:
    return " ".join(t.text for t in toks)


# ---------------------------------------------------------------------------------------------------
# Patterns (细则 8.5)
# ---------------------------------------------------------------------------------------------------


def _clauses(toks: list[W]) -> list[list[W]]:
    out: list[list[W]] = [[]]
    for tok in toks:
        if tok.kind == "clause":
            out.append([])
        else:
            out[-1].append(tok)
    return out


def _state(toks: list[W], decls: Decls) -> dict:
    if not toks:
        raise FormulaError("缺少状态命题")
    p = _P(toks, decls)
    out = p.prop()
    if not p.done():
        raise FormulaError(f"多余的内容：{_show(toks[p.i :])}")
    for m in F.walk(out):
        if m["op"] in F.TEMPORAL_OPS:
            raise FormulaError("状态命题中不能有时序算子")
    return out


def next_step(q: dict) -> dict:
    """细则 8.6: N(Q). Without comparisons X Q; otherwise X on each literal and ′ on each comparison."""
    if not any(m["op"] in F.COMPARISONS for m in F.walk(q)):
        return F.node("X", q)

    def go(n: dict) -> dict:
        op = n["op"]
        if op in F.COMPARISONS:
            return F.node(op, F.node("next", n["args"][0]), n["args"][1])
        if op == "pred" or (op == "not" and n["args"][0]["op"] == "pred"):
            return F.node("X", n)
        if op in ("and", "or", "implies", "not"):
            return F.node(op, *(go(a) for a in n["args"]))
        raise FormulaError(f"下一步 中不能有 {op}")

    return go(q)


def at_state(n: dict, k: int) -> dict:
    """Fix every atom and term of a state proposition to state k (S₀, Qₙ)."""
    if n["op"] in ("pred", "term"):
        return F.node("at", n, k=k)
    if n["op"] in ("var", "const", "lit"):
        return n
    return {**n, "args": [at_state(a, k) for a in n["args"]]}


def _code(tok: W | None) -> str:
    if tok is None or tok.kind != "code":
        raise FormulaError(f"这里需要反引号括起的代码名，得到 {tok.text if tok else '结尾'}")
    return tok.text


def _quantified(head: list[W], decls: Decls) -> tuple[str, str | None]:
    if len(head) == 1:
        typ, name = None, head[0].text
    elif len(head) == 2:
        typ, name = head[0].text, head[1].text
        if decls.kind(typ) != "实体":
            raise FormulaError(f"量词的类型 {typ} 不是已声明的实体")
    else:
        raise FormulaError("量词应写作 存在一个 [T] x")
    if not F.VARIABLE.fullmatch(name) or decls.kind(name):
        raise FormulaError(f"量词后应是变量：{name}")
    return name, typ


def parse_sentence(sentence: str, decls: Decls) -> tuple[dict, str]:
    """Return (formula tree, pattern name) for a controlled sentence, or raise FormulaError."""
    toks = lex(sentence, decls)
    if not toks:
        raise FormulaError("句子为空")
    cl = _clauses(toks)
    first = cl[0][0] if cl[0] else None

    if len(cl) == 1:
        rest = cl[0][1:]
        if _is(first, "总是"):
            return F.node("G", _state(rest, decls)), "总是"
        if _is(first, "从不"):
            return F.node("G", F.mk_not(_state(rest, decls))), "从不"
        if _is(first, "下一步"):
            return next_step(_state(rest, decls)), "下一步"
        if _is(first, "调用"):
            return _call(rest, decls)
        if _is(first, "初始时"):
            p = _P(rest, decls)
            v = p.expr()
            p.take("等于")
            c = p.expr()
            if not p.done():
                raise FormulaError("初始时 v 等于 c 之后有多余的内容")
            return F.node("eq", at_state(v, 0), at_state(c, 0)), "初始时"
        if _is(first, "代码中存在"):
            return _struct_exists(rest, decls)
        if _is(first, "只有"):
            if len(rest) < 3 or not _is(rest[1], "修改"):
                raise FormulaError("应写作 只有 `M` 修改 D")
            m = F.code(_code(rest[0]))
            target = rest[2:]
            if len(target) == 1 and target[0].kind == "code":
                d = F.code(target[0].text)
            else:
                p = _P(target, decls)
                d = p.expr()
                if not p.done() or d["op"] not in ("term", "const"):
                    raise FormulaError("只有 `M` 修改 D：D 应是代码名或已声明的位置、数量")
            return F.node("struct", m, d, kind="only"), "结构：只有"
        if first is not None and first.kind == "code" and len(cl[0]) >= 2:
            return _struct_code(cl[0], decls)
        if _is(first, "只要", "一旦", "在", "存在一个", "恰好存在一个", "对所有", "从"):
            raise FormulaError(f"句式“{first.text}…”需要用 ， 分开的从句")
        return _state(cl[0], decls), "具体事实"

    if len(cl) == 2:
        a, b = cl
        if _is(first, "只要"):
            p = _state(a[1:], decls)
            if _is(b[0] if b else None, "就"):
                return F.node("G", F.implies(p, _state(b[1:], decls))), "只要就"
            if _is(b[0] if b else None, "下一步"):
                return F.node("G", F.implies(p, next_step(_state(b[1:], decls)))), "只要下一步"
            if _is(b[0] if b else None, "最终"):
                return F.node("G", F.implies(p, F.node("F", _state(b[1:], decls)))), "只要最终"
            raise FormulaError("只要 P，之后应是 就 / 下一步 / 最终")
        if _is(first, "一旦"):
            if not _is(b[0] if b else None, "此后一直"):
                raise FormulaError("应写作 一旦 P，此后一直 P")
            p, p2 = _state(a[1:], decls), _state(b[1:], decls)
            if p != p2:
                raise FormulaError("一旦 P，此后一直 P：两处 P 不同")
            return F.node("G", F.implies(p, F.node("G", p))), "一旦"
        if _is(first, "在"):
            if not a or not _is(a[-1], "之前") or not _is(b[0] if b else None, "不会"):
                raise FormulaError("应写作 在 Q 之前，不会 P")
            q, p = _state(a[1:-1], decls), _state(b[1:], decls)
            return F.node("W", F.mk_not(p), q), "在之前"
        if _is(first, "存在一个", "恰好存在一个", "对所有"):
            name, typ = _quantified(a[1:], decls)
            body = b
            if first.text != "对所有":
                if not _is(b[0] if b else None, "使"):
                    raise FormulaError(f"应写作 {first.text} x，使 P")
                body = b[1:]
            op = {"存在一个": "exists", "恰好存在一个": "exists1", "对所有": "forall"}[first.text]
            q = F.node(op, _state(body, decls), var=name)
            if typ:
                q["type"] = typ
            return q, {"exists": "存在", "exists1": "恰好存在", "forall": "对所有"}[op]
        raise FormulaError("没有匹配的句式")

    if len(cl) == 3 and _is(first, "从"):
        a, b, c = cl
        if (
            not _is(a[-1] if a else None, "出发")
            or not _is(b[0] if b else None, "依次执行")
            or not _is(c[0] if c else None, "之后")
        ):
            raise FormulaError("应写作 从 S 出发，依次执行 a1、…、an，之后 Q")
        s = _state(a[1:-1], decls)
        actions = [tok for tok in b[1:] if tok.kind != "list"]
        if not actions:
            raise FormulaError("依次执行 后没有动作")
        for tok in actions:
            if tok.kind != "word" or decls.kind(tok.text) != "动作":
                raise FormulaError(f"{tok.text} 不是已声明的动作")
        q = _state(c[1:], decls)
        steps = [F.node("eq", F.node("at", F.term(F.ACTION), k=k), F.lit(tok.text)) for k, tok in enumerate(actions)]
        return F.implies(F.mk_and(at_state(s, 0), *steps), at_state(q, len(actions))), "从出发"

    raise FormulaError("没有匹配的句式")


def _call(rest: list[W], decls: Decls) -> tuple[dict, str]:
    if not rest or rest[0].kind != "code" or len(rest) < 3 or rest[1].kind != "lp":
        raise FormulaError("应写作 调用 `f`(v, …) 得到 v")
    p = _P(rest, decls)
    p.i = 1
    args = p.call_args()
    for a in args:
        if a["op"] != "lit":
            raise FormulaError("调用的参数应是字面量")
    call = F.node("call", *args, name=rest[0].text)
    if _is(p.peek(), "失败") and p.i + 1 == len(rest):
        return F.node("fails", call), "调用失败"
    p.take("得到")
    result = p.expr()
    if not p.done() or result["op"] != "lit":
        raise FormulaError("调用的结果应是一个字面量")
    return F.node("eq", call, result), "调用得到"


def _struct_exists(rest: list[W], decls: Decls) -> tuple[dict, str]:
    if _is(rest[0] if rest else None, "类型", "函数", "模块"):
        if len(rest) != 2:
            raise FormulaError("应写作 代码中存在 类型/函数/模块 `N`")
        return F.node("struct", F.code(_code(rest[1])), kind="exists"), "结构：存在"
    if _is(rest[0] if rest else None, "表示"):
        if len(rest) < 4 or not (_is(rest[-2], "的") and _is(rest[-1], "元素")):
            raise FormulaError("应写作 代码中存在表示 C 的元素")
        inner = rest[1:-2]
        if len(inner) == 1 and inner[0].kind == "word" and decls.kind(inner[0].text) == "谓词":
            c = F.pred(inner[0].text)
        else:
            p = _P(inner, decls)
            c = p.expr()
            if not p.done() or c["op"] not in ("term", "const"):
                raise FormulaError("代码中存在表示 C 的元素：C 应是已声明的名称或项")
        return F.node("struct", c, kind="represents"), "结构：表示"
    raise FormulaError("应写作 代码中存在 类型/函数/模块 `N`，或 代码中存在表示 C 的元素")


def _struct_code(toks: list[W], decls: Decls) -> tuple[dict, str]:
    n = F.code(toks[0].text)
    rest = toks[1:]
    if len(rest) == 3 and _is(rest[0], "的") and _is(rest[1], "签名是") and rest[2].kind == "str":
        return F.node("struct", n, F.lit(rest[2].value), kind="signature"), "结构：签名"
    if len(rest) == 3 and _is(rest[0], "包含") and _is(rest[1], "字段", "方法"):
        return F.node("struct", n, F.code(_code(rest[2])), kind="contains"), "结构：包含"
    if len(rest) == 2 and _is(rest[0], "调用", "不调用"):
        s = F.node("struct", n, F.code(_code(rest[1])), kind="calls")
        return (F.mk_not(s) if rest[0].text == "不调用" else s), "结构：调用"
    raise FormulaError("无法识别的结构句式")
