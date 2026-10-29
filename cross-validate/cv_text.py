"""Parsing what the subtask agent writes: the split (第 6 节) and declarations, checklist, propositions (第 7 节)."""

from __future__ import annotations

import json
import re

import cv_grammar as G
from cv_formula import Decls

LABELS = ["规则", "条件", "数量约束", "示例", "接口规定"]
NO_LABEL = "无"
CHECKLIST = ["存在", "状态", "不变式", "动作的效果", "事件的后果", "边界", "终态", "最终会发生的事", "结构"]
CATEGORIES = ["实体", "谓词", "位置", "数量", "动作", "接口"]
FIELDS = ["编号", "来源", "依据", "句子", "公式"]
SECTIONS = ["声明", "清单", "命题"]
SENTENCE_END = re.compile(r"[^。！？；\n]*[。！？；\n]|[^。！？；\n]+$")
BAD_NAME = re.compile(r"[\s()（）,，、`「」]")
COLUMNS = re.compile(r"\s{2,}|\t")


def strip_fences(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
    return "\n".join(lines)


def extract_json(text: str, opener: str):
    """The JSON value in an agent's answer: the span from the first opener to the last closer."""
    closer = "]" if opener == "[" else "}"
    body = strip_fences(text)
    start, end = body.find(opener), body.rfind(closer)
    if start < 0 or end < start:
        raise ValueError(f"回答中没有 JSON {'数组' if opener == '[' else '对象'}")
    return json.loads(body[start : end + 1])


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


# ---------------------------------------------------------------------------------------------------
# 第 6 节 切句与分类
# ---------------------------------------------------------------------------------------------------


def check_split(answer: str, requirement: str) -> tuple[list[dict], list[str]]:
    """细则 6.1, 6.2. Returns the sentences with their offsets in R, and the problems found."""
    try:
        items = extract_json(answer, "[")
    except (ValueError, json.JSONDecodeError) as e:
        return [], [f"切句结果不是 JSON 数组：{e}"]
    if not isinstance(items, list) or not items:
        return [], ["切句结果应是非空数组"]
    errors: list[str] = []
    sentences: list[dict] = []
    pos = 0
    for k, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            errors.append(f"第 {k + 1} 项缺少 text")
            continue
        text = item["text"]
        labels = item.get("labels") or [NO_LABEL]
        if not isinstance(labels, list) or any(lb not in [*LABELS, NO_LABEL] for lb in labels):
            errors.append(f"第 {k + 1} 项的 labels 只能取 {'、'.join([*LABELS, NO_LABEL])}")
            labels = [NO_LABEL]
        if NO_LABEL in labels and len(labels) > 1:
            labels = [lb for lb in labels if lb != NO_LABEL]
        start = requirement.find(text, pos) if text.strip() else -1
        if start < 0:
            errors.append(f"第 {k + 1} 项不是 R 中按顺序出现的逐字片段：{text[:40]}")
            continue
        if _squash(requirement[pos:start]):
            errors.append(f"第 {k + 1} 项之前有 R 的内容没有被任何句子覆盖：{requirement[pos:start][:40]}")
        sentences.append(
            {"id": f"S{len(sentences) + 1}", "text": text, "labels": labels, "start": start, "end": start + len(text)}
        )
        pos = start + len(text)
    if _squash(requirement[pos:]):
        errors.append(f"R 的结尾没有被覆盖：{requirement[pos:][:40]}")
    return sentences, errors


def fallback_split(requirement: str) -> list[dict]:
    """细则 6.1, 17.1: split at sentence-final punctuation; every sentence is labelled 无."""
    out: list[dict] = []
    for m in SENTENCE_END.finditer(requirement):
        if m.group().strip():
            text = m.group().rstrip("\n")
            out.append(
                {
                    "id": f"S{len(out) + 1}",
                    "text": text,
                    "labels": [NO_LABEL],
                    "start": m.start(),
                    "end": m.start() + len(text),
                }
            )
    return out


# ---------------------------------------------------------------------------------------------------
# 第 7 节 声明、清单、命题
# ---------------------------------------------------------------------------------------------------


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for line in strip_fences(text).splitlines():
        head = line.strip().rstrip("：:")
        if head in SECTIONS and not line[:1].isspace():
            current = head
            out.setdefault(current, [])
        elif current is not None:
            out[current].append(line.rstrip())
    return out


def _name_params(text: str) -> tuple[str, list[str]]:
    m = re.fullmatch(r"([^()（）]+)[(（]([^()（）]*)[)）]", text.strip())
    if not m:
        return text.strip().strip("`"), []
    params = [p.strip() for p in re.split(r"[,，、]", m.group(2)) if p.strip()]
    return m.group(1).strip(), params


def parse_declarations(lines: list[str], quotable: str) -> tuple[Decls, list[str]]:
    """细则 7.1, 9.2."""
    decls, errors = Decls(), []
    category = None
    for line in lines:
        if not line.strip():
            continue
        cols = [c for c in COLUMNS.split(line.strip()) if c]
        if not line[:1].isspace() and cols[0] in CATEGORIES:
            category, cols = cols[0], cols[1:]
        elif not line[:1].isspace():
            errors.append(f"声明的类别不合法：{cols[0]}（应为 {'、'.join(CATEGORIES)}）")
            category = None
            continue
        if category is None or not cols:
            if category is None:
                errors.append(f"声明行没有类别：{line.strip()}")
            continue
        entries = [n.strip() for n in re.split(r"[、]", cols[0])] if category in ("实体", "动作") else [cols[0]]
        meaning = " ".join(cols[1:])
        for entry in entries:
            name, params = _name_params(entry)
            if category in ("实体", "动作", "接口") and params:
                errors.append(f"{category} {name} 不带参数")
            if not name or BAD_NAME.search(name):
                errors.append(f"名称不合法：{entry}（不能含空白、括号、逗号、反引号、「」）")
                continue
            if name in G.RESERVED:
                errors.append(f"名称 {name} 是保留字")
                continue
            if name in decls.category:
                errors.append(f"名称重复：{name}")
                continue
            if category == "谓词" and not params:
                errors.append(f"谓词 {name} 至少要有一个参数（谓词带上它所描述的对象）")
            if category == "接口" and name not in quotable:
                errors.append(f"接口名 {name} 没有逐字出现在需求原话中")
            if category in ("谓词", "位置", "数量", "接口") and not meaning:
                errors.append(f"{name} 没有写含义")
            decls.category[name] = category
            decls.params[name] = params
            decls.meaning[name] = meaning
    errors.extend(
        f"{name} 的参数 {p} 不是已声明的实体"
        for name, params in decls.params.items()
        for p in params
        if decls.kind(p) != "实体"
    )
    if not decls.category:
        errors.append("没有任何声明")
    return decls, errors


def parse_checklist(lines: list[str]) -> tuple[dict[str, dict], list[str]]:
    """细则 7.2: one line per item of the nine; ids, or 无： with a reason."""
    out: dict[str, dict] = {}
    errors: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        cols = [c for c in COLUMNS.split(line.strip(), maxsplit=1) if c]
        item = cols[0]
        if item not in CHECKLIST:
            errors.append(f"清单项不合法：{item}")
            continue
        rest = cols[1].strip() if len(cols) > 1 else ""
        if rest.startswith("无"):
            reason = rest[1:].lstrip("：: ")
            if not reason:
                errors.append(f"清单项 {item} 写了 无，没有写理由")
            out[item] = {"ids": [], "none": reason}
        else:
            ids = [x for x in re.split(r"[、，,\s]+", rest) if x]
            if not ids:
                errors.append(f"清单项 {item} 既没有命题编号，也没有写 无：理由")
            out[item] = {"ids": ids, "none": None}
    errors.extend(f"清单缺少一项：{item}" for item in CHECKLIST if item not in out)
    return out, errors


def parse_propositions(lines: list[str]) -> tuple[list[dict], list[str]]:
    """细则 7.3: blocks separated by blank lines, each line a field name, two or more spaces, a value."""
    blocks: list[list[str]] = [[]]
    for line in lines:
        if line.strip():
            blocks[-1].append(line.strip())
        elif blocks[-1]:
            blocks.append([])
    out: list[dict] = []
    errors: list[str] = []
    for block in blocks:
        if not block:
            continue
        prop: dict = {"problems": []}
        for line in block:
            cols = COLUMNS.split(line, maxsplit=1)
            if len(cols) != 2 or cols[0] not in FIELDS:
                prop["problems"].append(f"无法识别的行：{line}")
                continue
            if cols[0] in prop:
                prop["problems"].append(f"字段重复：{cols[0]}")
            prop[cols[0]] = cols[1].strip()
        if "编号" not in prop:
            errors.append(f"有一条命题没有编号：{block[0]}")
            continue
        out.append(prop)
    return out, errors


def parse_subtask(text: str, quotable: str) -> dict:
    sections = _sections(text)
    errors = [f"缺少 {name} 段" for name in SECTIONS if name not in sections]
    decls, decl_errors = parse_declarations(sections.get("声明", []), quotable)
    checklist, checklist_errors = parse_checklist(sections.get("清单", []))
    props, prop_errors = parse_propositions(sections.get("命题", []))
    return {
        "decls": decls,
        "checklist": checklist,
        "propositions": props,
        "errors": errors + decl_errors + checklist_errors,
        "block_errors": prop_errors,
    }
