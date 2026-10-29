"""The requirement ledger: recording, preprocessing and checking the consolidation (README 第 15 节)."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import re
from pathlib import Path

import cv_state as S

REPORT_MARKER = "交叉验证可信度报告"
PLACEHOLDER = "〔已省略：粘贴的代码或日志〕"
DISPOSITIONS = ("需求", "补充", "更正", "撤销", "已被更正", "非需求")
MUST_QUOTE = ("需求", "补充", "更正")
ASCII_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
STACK_LINE = re.compile(
    r"(Traceback \(most recent call last\)|^\s*File \".*\", line \d+|^\s*at \S.*:\d+|^\s*\w*(Error|Exception): ).*$",
    re.MULTILINE,
)
PATH = re.compile(r"(?<![\w/])(?:~?/|\.{1,2}/|[A-Za-z]:\\)?(?:[\w.\-]+[/\\])+[\w.\-]+")


def ledger_dir(project: Path | str) -> Path:
    return S.ensure_private(S.project_dir(project) / "ledger")


def current_id(project: Path | str) -> str:
    path = ledger_dir(project) / "current"
    if not path.exists():
        S.write_text(path, "Q1\n")
    return path.read_text(encoding="utf-8").strip() or "Q1"


def ledger_file(project: Path | str, qid: str | None = None) -> Path:
    return ledger_dir(project) / f"{qid or current_id(project)}.jsonl"


def read_messages(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_message(project: Path | str, session: str, text: str, injected: bool) -> dict:
    """细则 15.2: one line per message; ids increase within a ledger; appends hold the file lock."""
    path = ledger_file(project)
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        count = sum(1 for line in f if line.strip())
        entry = {
            "id": f"M{count + 1}",
            "time": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "session": session,
            "injected": bool(injected),
            "text": text,
        }
        f.seek(0, 2)
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
    return entry


def new_ledger(project: Path | str) -> str:
    """细则 15.6: start a new requirement; old ledgers stay."""
    existing = [int(p.stem[1:]) for p in ledger_dir(project).glob("Q*.jsonl") if p.stem[1:].isdigit()]
    qid = f"Q{max([*existing, int(current_id(project)[1:])]) + 1}"
    S.write_text(ledger_dir(project) / "current", qid + "\n")
    ledger_file(project, qid).touch()
    return qid


def _is_path(token: str) -> bool:
    if not re.search(r"[A-Za-z]", token):
        return False
    return (
        token.startswith(("/", "~/", "./", "../"))
        or bool(re.search(r"\.\w{1,6}$", token))
        or token.count("/") >= 2
        or ":\\" in token
    )


def preprocess(messages: list[dict]) -> list[dict]:
    """细则 15.3: strip pasted code, stack traces and paths; drop pasted reports."""
    out = []
    for m in messages:
        text = m["text"]
        rec = {**m, "replaced": False, "dropped": False}
        if REPORT_MARKER in text:
            rec.update(text="", dropped=True)
            out.append(rec)
            continue
        new = FENCE.sub(PLACEHOLDER, text)
        new = STACK_LINE.sub(PLACEHOLDER, new)
        new = PATH.sub(lambda mt: PLACEHOLDER if _is_path(mt.group()) else mt.group(), new)
        rec["replaced"] = new != text
        rec["text"] = new
        out.append(rec)
    return out


def consolidation_input(messages: list[dict]) -> str:
    return "\n".join(f"{m['id']}：{m['text']}" for m in messages if not m["dropped"])


def _num(mid: str) -> int:
    return int(mid[1:]) if re.fullmatch(r"M\d+", mid) else -1


def check_consolidation(answer: str, messages: list[dict]) -> tuple[dict | None, list[str]]:
    """细则 15.4: the program's checks on the consolidated requirement."""
    from cv_text import extract_json

    try:
        data = extract_json(answer, "{")
    except (ValueError, json.JSONDecodeError) as e:
        return None, [f"整理结果不是 JSON 对象：{e}"]
    errors: list[str] = []
    by_id = {m["id"]: m for m in messages}
    visible = [m["id"] for m in messages if not m["dropped"]]
    reqs = data.get("requirement") if isinstance(data, dict) else None
    disp = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(reqs, list) or not isinstance(disp, dict):
        return None, ["整理结果应有 requirement 数组和 messages 对象"]
    parsed: dict[str, tuple[str, str | None]] = {}
    errors.extend(f"消息 {mid} 没有去向" for mid in visible if mid not in disp)
    for mid, value in disp.items():
        if mid not in by_id or by_id[mid]["dropped"]:
            errors.append(f"去向中有不存在的消息 {mid}")
            continue
        m = re.fullmatch(r"(需求|补充|更正|撤销|已被更正|非需求)(?:\s*(M\d+))?", str(value).strip())
        if not m or (m.group(1) in ("更正", "撤销")) != bool(m.group(2)):
            errors.append(f"消息 {mid} 的去向不合法：{value}")
            continue
        kind, target = m.group(1), m.group(2)
        if target and (_num(target) >= _num(mid) or target not in by_id):
            errors.append(f"消息 {mid} 指向的 {target} 不是更早的消息")
        parsed[mid] = (kind, target)
    quoted: set[str] = set()
    entries = []
    for k, item in enumerate(reqs):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            errors.append(f"R 第 {k + 1} 条缺少 text")
            continue
        quotes = item.get("quotes")
        if not isinstance(quotes, list) or not quotes:
            errors.append(f"R 第 {k + 1} 条没有原话片段")
            continue
        good = []
        for q in quotes:
            if (
                not isinstance(q, dict)
                or q.get("from") not in by_id
                or not isinstance(q.get("text"), str)
                or not q["text"]
            ):
                errors.append(f"R 第 {k + 1} 条的原话片段格式不对：{q}")
                continue
            if q["text"] not in by_id[q["from"]]["text"]:
                errors.append(f"R 第 {k + 1} 条的片段「{q['text'][:30]}」不是 {q['from']} 的逐字子串")
                continue
            quoted.add(q["from"])
            good.append({"from": q["from"], "text": q["text"]})
        joined = " ".join(q["text"] for q in good)
        names = sorted(set(ASCII_NAME.findall(item["text"])))
        errors.extend(f"R 第 {k + 1} 条中的 {name} 没有出现在它的原话片段中" for name in names if name not in joined)
        entries.append({"text": item["text"].strip(), "quotes": good})
    for mid, (kind, _) in parsed.items():
        if kind in MUST_QUOTE and mid not in quoted:
            errors.append(f"消息 {mid}（{kind}）没有被 R 的任何一条引用")
        superseded = any(t == mid and k in ("更正", "撤销") and _num(o) > _num(mid) for o, (k, t) in parsed.items())
        if kind == "已被更正" and not superseded:
            errors.append(f"消息 {mid} 标为已被更正，但没有后来的更正或撤销指向它")
    if not entries:
        errors.append("R 为空")
    dispositions = {mid: (kind + (f" {target}" if target else "")) for mid, (kind, target) in parsed.items()}
    for m in messages:
        if m["dropped"]:
            dispositions[m["id"]] = "非需求"
    return {"entries": entries, "messages": dispositions}, errors


def build_requirement(entries: list[dict], messages: dict[str, str], mode: str) -> tuple[dict, str]:
    """requirement.json and requirement.txt (细则 15.4): entry texts joined by newlines, with offsets."""
    text, out = "", []
    for e in entries:
        start = len(text)
        text += e["text"]
        out.append({**e, "start": start, "end": len(text)})
        text += "\n"
    return {"mode": mode, "entries": out, "messages": messages}, text.rstrip("\n")
