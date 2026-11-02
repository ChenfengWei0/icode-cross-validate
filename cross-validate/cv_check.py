"""Checking the propositions (README 第 9 节; 算法1 第4行) and generating their formulas (算法1 第5行)."""

from __future__ import annotations

import re

import cv_formula as F
import cv_grammar as G
import cv_text as T
from cv_formula import Decls, FormulaError

UNPATTERNED = "未套用句式"
ASCII_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
SUBSCRIPT = re.compile(r"_\d+\b")
EXCEPTION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)(?![A-Za-z0-9_])")
REASON = re.compile(r"^〔([^〕]+)〕\s*(.+)$")
CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}
QUOTE_NUMBER = re.compile(r"[-−]?[0-9０-９]+(?:[.．][0-9０-９]+)?|[零〇一二两三四五六七八九十百千万]+")
# Common words written with numeral characters that are not numbers.
NOT_NUMBERS = re.compile("一直|一旦|一定|一起|一样|一般|一些|一切|一下|一边|万一|千万|十分|统一|唯一|之一|同一|一致")
CONDITION = ("如果", "时", "当", "每当", "只要", "若")
# 细则 9.1 第 5 项: what a pattern needs as its basis. Keywords only allow; they never forbid.
PATTERN_BASIS = {
    "总是": ("总是", "始终", "一直", "永远"),
    "从不": ("不能", "不得", "不可", "不会", "不允许", "禁止", "从不", "永不"),
    "只要就": CONDITION,
    "只要下一步": (*CONDITION, "之后", "后", "然后", "最终"),
    "只要最终": (*CONDITION, "之后", "后", "然后", "最终"),
    "一旦": ("一旦", "此后", "从此", "之后一直"),
    "在之前": ("之前", "以前", "直到"),
}
# 细则 9.1 第 5 项, 17.9: a rule sentence may have no keyword at all. `从不` is not here: only a negation allows it.
RULE_LABELS = {"规则", "条件", "数量约束"}
RULE_PATTERNS = {"总是", "只要就", "只要下一步", "只要最终"}
LABEL_BASIS = {
    **dict.fromkeys(("调用得到", "调用失败", "从出发"), "示例"),
    **dict.fromkeys(G.STRUCTURE_PATTERNS, "接口规定"),
}


def quotable_for(sentence: dict, entries: list[dict] | None) -> list[str]:
    """细则 6.3: the verbatim user text a translation of this sentence may quote.

    In goal mode (no entries) that is the sentence itself. In accumulation mode it is the quotes of
    every R entry whose text overlaps the sentence in requirement.txt.
    """
    if not entries:
        return [sentence["text"]]
    out: list[str] = []
    for entry in entries:
        if entry["start"] < sentence["end"] and sentence["start"] < entry["end"]:
            out.extend(q["text"] for q in entry["quotes"])
    return out


def pattern_basis(pattern: str, quote: str, labels: list[str]) -> bool:
    """细则 9.1 第 5 项: whether the quote's keywords or the sentence's labels allow this pattern."""
    if pattern in RULE_PATTERNS and RULE_LABELS & set(labels):
        return True
    if pattern in PATTERN_BASIS:
        return any(w in quote.replace("初始时", "") for w in PATTERN_BASIS[pattern])
    if pattern in LABEL_BASIS:
        return LABEL_BASIS[pattern] in labels
    return True


def quotes_in(text: str) -> list[str]:
    """The 「…」 quotes of a basis, reading nested 「」 by matching brackets (细则 9.1 第 5 项)."""
    out, depth, start = [], 0, 0
    for i, c in enumerate(text):
        if c == "「":
            if depth == 0:
                start = i + 1
            depth += 1
        elif c == "」" and depth:
            depth -= 1
            if depth == 0:
                out.append(text[start:i])
    return out


def _cn_number(s: str) -> int | None:
    if all(c in CN_DIGIT for c in s):
        return int("".join(str(CN_DIGIT[c]) for c in s))
    total, section, digit = 0, 0, None
    for c in s:
        if c in CN_DIGIT:
            digit = CN_DIGIT[c]
        elif c == "万":
            total += (section + (digit or 0)) * 10000
            section, digit = 0, None
        else:
            section += (1 if digit is None else digit) * CN_UNIT[c]
            digit = None
    return total + section + (digit or 0)


def quote_numbers(quote: str) -> list[float]:
    """Every number in a quote, by value: ASCII, full-width and simple Chinese numerals."""
    out = []
    quote = NOT_NUMBERS.sub("　", quote)
    for m in QUOTE_NUMBER.finditer(quote):
        text = m.group().translate(str.maketrans("０１２３４５６７８９．−", "0123456789.-"))
        if text[-1:].isdigit():
            out.append(float(text))
        elif m.group() in ("一", "两") and len(quote) > m.end() and quote[m.end()] in "个条次只":
            out.append(float(CN_DIGIT[m.group()]))
        else:
            value = _cn_number(m.group())
            if value is not None:
                out.append(float(value))
    return out


def _numbers(value) -> list:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [value]
    if isinstance(value, list):
        return [n for v in value for n in _numbers(v)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _numbers(v)]
    return []


def _ascii_names(text: str) -> set[str]:
    """细则 9.1 第 4 项: ASCII names outside 「」 strings, JSON strings and subscripts."""
    out, i, plain = [], 0, []
    while i < len(text):
        c = text[i]
        if c == "「":
            depth, j = 0, i
            while j < len(text):
                depth += {"「": 1, "」": -1}.get(text[j], 0)
                if depth == 0:
                    break
                j += 1
            if j >= len(text):  # unmatched: not a string, read on
                plain.append(c)
                i += 1
                continue
            i = j + 1
        elif c in "[{":
            try:
                j = F._scan_json(text, i)
            except FormulaError:
                j = len(text)
            plain.append(re.sub(r'"(?:[^"\\]|\\.)*"', " ", text[i:j]))
            i = j
        else:
            plain.append(c)
            i += 1
    out = SUBSCRIPT.sub(" ", "".join(plain))
    return {m for m in ASCII_NAME.findall(out) if m not in ("true", "false", "null")}


def first_state_only(tree: dict, decls: Decls) -> bool:
    """细则 9.1 第 8 项: a formula that depends on the state but says nothing about when."""
    if F.classify(tree) != "具体事实" or any(n["op"] == "at" for n in F.walk(tree)):
        return False
    return any(
        n["op"] in ("pred", "term") and decls.kind(n["name"]) != "实体" and any(a["op"] != "lit" for a in n["args"])
        for n in F.walk(tree)
    )


def lost_exceptions(quote: str, sentence: str, formula: str, tree: dict | None, decls: Decls) -> list[str]:
    """细则 9.1 第 5 项: exception names of the quote that the proposition no longer mentions."""
    used = [n["name"] for n in F.walk(tree) if n["op"] in ("pred", "term")] if tree else []
    text = " ".join([sentence, formula, *used, *(decls.meaning.get(n, "") for n in used)])
    return [name for name in EXCEPTION_NAME.findall(quote) if name not in text]


def _parses(sentence: str, decls: Decls) -> bool:
    try:
        G.parse_sentence(sentence, decls)
    except (FormulaError, RecursionError):
        return False
    return bool(sentence.strip())


def check_propositions(parsed: dict, sentences: list[dict], original: str) -> list[dict]:
    """细则 9.1. `sentences` carry labels and `quotable`; `original` is all of R's verbatim text.

    The propositions are untrusted text: a proposition the program cannot check is discarded with the
    reason, it never stops the run (细则 4.8).
    """
    out = []
    for prop in parsed["propositions"]:
        try:
            out.extend(_check(parsed, {**parsed, "propositions": [prop]}, sentences, original))
        except Exception as e:  # noqa: PERF203 — one bad proposition must not stop the others
            out.append(
                {
                    "id": prop.get("编号", ""),
                    "source": prop.get("来源", ""),
                    "basis": prop.get("依据", ""),
                    "sentence": prop.get("句子", ""),
                    "pattern": None,
                    "tree": None,
                    "problems": [f"程序无法检查这条命题：{type(e).__name__}"],
                }
            )
    return out


def _check(all_parsed: dict, parsed: dict, sentences: list[dict], original: str) -> list[dict]:
    decls: Decls = parsed["decls"]
    checklist = parsed["checklist"]
    seen: dict[str, int] = {}
    for prop in all_parsed["propositions"]:
        seen[prop.get("编号", "")] = seen.get(prop.get("编号", ""), 0) + 1
    labelled = [s for s in sentences if s["labels"] != [T.NO_LABEL]]
    out: list[dict] = []
    for prop in parsed["propositions"]:
        problems = list(prop.get("problems", []))
        pid = prop.get("编号", "")
        rec = {
            "id": pid,
            "source": prop.get("来源", ""),
            "basis": prop.get("依据", ""),
            "sentence": prop.get("句子", ""),
        }
        # 1. fields and values; 7. unique ids
        if not re.fullmatch(r"R\d+", pid):
            problems.append("编号应是 R 加序号")
        if seen.get(pid, 0) > 1:
            problems.append(f"编号 {pid} 重复")
        problems.extend(f"缺少字段 {name}" for name in ("来源", "依据", "句子") if not prop.get(name))
        if rec["source"] not in ("翻译", "推理"):
            problems.append("来源只能是 翻译 或 推理")
        # 2. parse (算法1 第5行)
        tree, pattern = None, None
        formula_field = prop.get("公式")
        try:
            if formula_field is not None:
                if not formula_field.startswith(UNPATTERNED):
                    raise FormulaError("公式只在未套用句式时填写，写作 未套用句式：公式")
                tree = F.parse(formula_field[len(UNPATTERNED) :].lstrip("：: "), decls)
                pattern = UNPATTERNED
                if _parses(rec["sentence"], decls):
                    problems.append("句子套用了句式，不应再写公式；删去公式，由程序生成（细则 9.1 第 2 项）")
            elif rec["sentence"]:
                tree, pattern = G.parse_sentence(rec["sentence"], decls)
        except FormulaError as e:
            problems.append(f"无法解析：{e}")
        except RecursionError:
            problems.append("无法解析：嵌套过深")
        # 3. declared names, arity, types, bound variables
        if tree is not None:
            problems.extend(F.type_errors(tree, decls))
            if first_state_only(tree, decls):
                problems.append(
                    "这条命题依赖状态却没有说明时间，只会在第一个状态上判定：说初始情况写 初始时，"
                    "说任何时刻写 总是 或 只要，需要量词时写 未套用句式：∀x:T G(…)（细则 9.1 第 8 项）"
                )
        # 4. no implementation names
        for name in sorted(_ascii_names(rec["sentence"] + " " + (formula_field or ""))):
            if F.VARIABLE.fullmatch(name):
                continue
            if name not in original:
                problems.append(f"{name} 没有出现在需求原话中，可能是实现上的名字")
        # 5. translation
        if rec["source"] == "翻译":
            quotes = quotes_in(rec["basis"])
            if not quotes:
                problems.append("翻译的依据应用「」引用原话")
            hosted = []
            for quote in quotes:
                hosts = [s for s in labelled if any(quote and quote in q for q in s["quotable"])]
                if not hosts:
                    problems.append(f"引文「{quote}」不是带标签句子的原话片段")
                    continue
                hosted.append((quote, sorted({lb for s in hosts for lb in s["labels"]})))
            if (
                hosted
                and pattern
                and pattern != UNPATTERNED
                and not any(pattern_basis(pattern, q, labels) for q, labels in hosted)
            ):
                problems.append(f"句式 {pattern} 在引文中没有对应的关键词或标签（细则 9.1 第 5 项）")
            for quote, labels in hosted:
                if tree is not None:
                    problems.extend(
                        f"引文中的异常名 {name} 丢失了：写进谓词的名称或含义，不要抽象掉（细则 9.1 第 5 项）"
                        for name in lost_exceptions(quote, rec["sentence"], formula_field or "", tree, decls)
                    )
                if tree is not None and "示例" in labels:
                    have = quote_numbers(quote)
                    # 细则 9.1 第 5 项: S of 从 S 出发 only selects the runs; it asserts nothing.
                    scope = tree["args"][1] if pattern == "从出发" else tree
                    numbers = [n for m in F.walk(scope) if m["op"] == "lit" for n in _numbers(m["value"])]
                    problems.extend(
                        f"数 {n} 没有在引文中出现（翻译不做推广）"
                        for n in numbers
                        if not any(abs(n - h) <= 1e-9 for h in have)
                    )
        # 6. reasoning
        if rec["source"] == "推理":
            m = REASON.match(rec["basis"])
            if not m:
                problems.append("推理的依据应以 〔清单项〕 开头并写明理由")
            elif m.group(1) not in T.CHECKLIST:
                problems.append(f"清单项 {m.group(1)} 不是九项之一")
            elif pid not in checklist.get(m.group(1), {}).get("ids", []):
                problems.append(f"清单段的 {m.group(1)} 一项没有列出 {pid}")
        rec.update(pattern=pattern, tree=tree, problems=problems)
        if tree is not None:
            rec["formula"] = F.to_text(tree)
            rec["class"] = F.classify(tree)
        out.append(rec)
    return out
