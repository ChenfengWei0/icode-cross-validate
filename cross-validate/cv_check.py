"""Checking the propositions (README 第 9 节; 算法1 第4行) and generating their formulas (算法1 第5行)."""

from __future__ import annotations

import re

import cv_formula as F
import cv_grammar as G
import cv_text as T
from cv_formula import Decls, FormulaError

UNPATTERNED = "未套用句式"
ASCII_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
QUOTE = re.compile(r"「([^」]*)」")
REASON = re.compile(r"^〔([^〕]+)〕\s*(.+)$")
CHINESE_DIGITS = {
    0: "零〇",
    1: "一",
    2: "二两",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
    10: "十",
}
KEYWORDS = [
    (("总是", "始终"), {"总是"}),
    (("不能", "禁止"), {"从不"}),
    (("如果", "时"), {"只要就"}),
    (("之后",), {"只要下一步", "只要最终"}),
]


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


def allowed_patterns(quote: str, labels: list[str]) -> set[str]:
    """细则 9.1 第 5 项: keywords in the quote and the sentence's labels give the usable patterns."""
    allowed: set[str] = set()
    text = quote.replace("初始时", "")
    for words, patterns in KEYWORDS:
        if any(w in text for w in words):
            allowed |= patterns
    if "示例" in labels:
        allowed |= G.EXAMPLE_PATTERNS
    if "接口规定" in labels:
        allowed |= G.STRUCTURE_PATTERNS
    return allowed


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _number_in(n, quote: str) -> bool:
    text = str(n)
    if text in quote:
        return True
    return isinstance(n, int) and n in CHINESE_DIGITS and any(c in quote for c in CHINESE_DIGITS[n])


def _ascii_names(text: str) -> set[str]:
    stripped = QUOTE.sub("", text)
    stripped = re.sub(r'"[^"]*"', "", stripped)
    return {m for m in ASCII_NAME.findall(stripped) if m not in ("true", "false", "null")}


def check_propositions(parsed: dict, sentences: list[dict], original: str) -> list[dict]:
    """细则 9.1. `sentences` carry labels and `quotable`; `original` is all of R's verbatim text."""
    decls: Decls = parsed["decls"]
    checklist = parsed["checklist"]
    seen: dict[str, int] = {}
    for prop in parsed["propositions"]:
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
            elif rec["sentence"]:
                tree, pattern = G.parse_sentence(rec["sentence"], decls)
        except FormulaError as e:
            problems.append(f"无法解析：{e}")
        # 3. declared names, arity, types, bound variables
        if tree is not None:
            problems.extend(F.type_errors(tree, decls))
        # 4. no implementation names
        for name in sorted(_ascii_names(rec["sentence"] + " " + (formula_field or ""))):
            if F.VARIABLE.fullmatch(name):
                continue
            if name not in original:
                problems.append(f"{name} 没有出现在需求原话中，可能是实现上的名字")
        # 5. translation
        if rec["source"] == "翻译":
            quotes = QUOTE.findall(rec["basis"])
            if not quotes:
                problems.append("翻译的依据应用「」引用原话")
            for quote in quotes:
                hosts = [s for s in labelled if any(quote and quote in q for q in s["quotable"])]
                if not hosts:
                    problems.append(f"引文「{quote}」不是带标签句子的原话片段")
                    continue
                labels = sorted({lb for s in hosts for lb in s["labels"]})
                allowed = allowed_patterns(quote, labels)
                if pattern and pattern != UNPATTERNED and allowed and pattern not in allowed:
                    problems.append(f"引文的关键词或标签要求句式为 {'、'.join(sorted(allowed))}，命题用的是 {pattern}")
                if tree is not None and "示例" in labels:
                    numbers = [m["value"] for m in F.walk(tree) if m["op"] == "lit" and _is_number(m["value"])]
                    problems.extend(
                        f"数 {n} 没有在引文中出现（翻译不做推广）" for n in numbers if not _number_in(n, quote)
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
