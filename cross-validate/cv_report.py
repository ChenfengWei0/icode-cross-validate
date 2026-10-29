"""The credibility report (README 第 14 节; 算法1 第8行)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import cv_monitor as M
import cv_state as S

TITLE = "交叉验证可信度报告"
INVALID, NO_CONCLUSION, FAILED, PASSED = "无效", "无结论", "比对未通过", "比对通过"
ASCII_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def conclusion(data: dict, max_discard_ratio: float) -> tuple[str, str]:
    """细则 14.2: event A of chapter 2, or why there is none."""
    if data.get("invalid"):
        return INVALID, "；".join(data["invalid"])
    if data.get("stopped"):
        return NO_CONCLUSION, data["stopped"]
    submitted = data["stats"]["submitted"]
    checked = len(data["results"])
    if checked == 0:
        return NO_CONCLUSION, "没有命题进入检查"
    if submitted and data["stats"]["discarded"] / submitted > max_discard_ratio:
        return NO_CONCLUSION, f"被丢弃的命题超过提交总数的 {max_discard_ratio:.0%}"
    if any(r["result"] in M.FAILING for r in data["results"].values()):
        return FAILED, "至少一条命题的结果是失败或确定违反"
    return PASSED, "没有命题失败或被确定违反"


def _num(pid: str) -> int:
    return int(pid[1:]) if pid[1:].isdigit() else 10**9


def _rows(props: list[dict], results: dict, forms: dict) -> list[dict]:
    rows = []
    for p in sorted(props, key=lambda p: _num(p["id"])):
        r = results.get(p["id"], {})
        rows.append(
            {
                "ids": [p["id"]],
                "source": p["source"] + ("（未套用句式）" if p.get("pattern") == "未套用句式" else ""),
                "result": r.get("result", "未检查"),
                "form": "、".join(forms.get(p["id"], [])) or "—",
                "liveness": p.get("liveness", False),
                "detail": _detail(r),
            }
        )
    merged: list[dict] = []
    for row in rows:
        last = merged[-1] if merged else None
        same = last and not last["detail"] and not row["detail"] and _num(row["ids"][0]) == _num(last["ids"][-1]) + 1
        if same and all(last[k] == row[k] for k in ("source", "result", "form", "liveness")):
            last["ids"].append(row["ids"][0])
        else:
            merged.append(row)
    return merged


def _detail(r: dict) -> str:
    parts = []
    if r.get("reason"):
        parts.append(r["reason"])
    cex = r.get("counterexample")
    if cex:
        if "actions" in cex:
            acts = [a for a in cex["actions"] if a is not None]
            shown = acts if len(acts) <= 12 else ["…", *acts[-12:]]
            seq = f"动作序列（共 {len(acts)} 步）{json.dumps(shown, ensure_ascii=False)}"
            parts.append(f"反例：第 {cex.get('run')} 次运行，状态 {cex['state']}，{seq}，完整序列见 report.json")
        elif "state" in cex:
            parts.append(f"反例：第 {cex.get('run')} 次运行，状态 {cex['state']}")
        elif cex.get("stdout"):
            parts.append("反例：" + cex["stdout"].strip()[-300:])
        elif cex.get("stderr") is not None:
            parts.append(f"程序异常退出（退出码 {cex.get('exit')}）：" + (cex.get("stderr") or "").strip()[-300:])
    return "；".join(parts)


def _ids(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else f"{ids[0]} 至 {ids[-1]}"


def build(run: Path, extra: dict, config: dict) -> dict:
    """Collect everything the report needs from the run directory."""
    run = Path(run)
    info = S.read_json(run / "run.json", {})
    req = S.read_json(run / "requirement.json", {}) or {}
    props = S.read_json(run / "propositions.json", {}) or {}
    frozen = S.read_json(run / "align" / "checks.frozen.json", {}) or {}
    results = S.read_json(run / "results.json", {}) or {}
    accepted = [p for p in props.get("propositions", []) if not p.get("problems")]
    discarded = [p for p in props.get("propositions", []) if p.get("problems")]
    forms: dict[str, list[str]] = {}
    for c in frozen.get("checks", []):
        if c["form"] and c["form"] not in forms.setdefault(c["proposition"], []):
            forms[c["proposition"]].append(c["form"])
    submitted = props.get("propositions", [])
    stats = {
        "submitted": len(submitted),
        "translated": sum(p["source"] == "翻译" and p.get("pattern") != "未套用句式" for p in submitted),
        "reasoned": sum(p["source"] == "推理" and p.get("pattern") != "未套用句式" for p in submitted),
        "unpatterned": sum(p.get("pattern") == "未套用句式" for p in submitted),
        "discarded": len(discarded),
        "by_result": {},
    }
    prop_results = results.get("propositions", {})
    for r in prop_results.values():
        stats["by_result"][r["result"]] = stats["by_result"].get(r["result"], 0) + 1
    data = {
        "title": TITLE,
        "run": run.name,
        "mode": info.get("mode"),
        "trigger": info.get("trigger"),
        "code": info.get("code", {}),
        "requirement": req,
        "notes": list(info.get("notes", [])) + list(extra.get("notes", [])),
        "accepted": accepted,
        "discarded": discarded,
        "bindings": frozen.get("bindings", {}),
        "results": prop_results,
        "forms": forms,
        "stats": stats,
        "invalid": extra.get("invalid", []),
        "stopped": extra.get("stopped"),
        "w_changes": extra.get("w_changes", {}),
        "suspect_names": extra.get("suspect_names", []),
    }
    data["conclusion"], data["conclusion_reason"] = conclusion(data, config["max_discard_ratio"])
    return data


def render(data: dict) -> str:
    out = [f"# {TITLE}", ""]
    req = data["requirement"] or {}
    out.append("## 需求")
    for k, e in enumerate(req.get("entries", []), 1):
        out.append(f"{k}. {e['text']}")
        if req.get("mode") == "积累":
            out.extend(f"   - {q['from']}：「{q['text']}」" for q in e["quotes"])
    if req.get("mode") == "积累":
        others = {
            d: [m for m, v in req.get("messages", {}).items() if v.startswith(d)]
            for d in ("非需求", "已被更正", "撤销")
        }
        for d, ms in others.items():
            if ms:
                out.append(f"- {d}：{'、'.join(sorted(ms, key=lambda m: int(m[1:])))}")
    if data["suspect_names"]:
        out.append(f"- 疑似来自实现（R 中出现、W 的代码中也出现）：{'、'.join(data['suspect_names'])}")
    out.append("")
    code = data["code"]
    out.append("## 代码")
    out.append(f"- 版本：{code.get('version', '未知')}")
    if code.get("snapshot_time"):
        out.append(f"- 快照时间：{code['snapshot_time']}")
    if data["trigger"]:
        out.append(f"- 触发方式：{data['trigger']}")
    changes = data["w_changes"]
    if changes.get("added"):
        out.append(
            f"- 检查期间 W 中新增的文件：{'、'.join(changes['added'][:20])}{' 等' if len(changes['added']) > 20 else ''}"
        )
    out.append("")
    out.append("## 命题")
    rows = _rows(data["accepted"], data["results"], data["forms"])
    if rows:
        out.append("| 编号 | 来源 | 结果 | 形态 | 说明 |")
        out.append("| --- | --- | --- | --- | --- |")
        for row in rows:
            mark = " ※" if row["liveness"] else ""
            out.append(
                f"| {_ids(row['ids'])} | {row['source']} | {row['result']}{mark} | {row['form']} | {row['detail'] or ''} |"
            )
        if any(r["liveness"] for r in rows):
            out.append("")
            out.append(
                "※ 活性命题：有限运行上通常得不到确定违反，它的推定为假只表示没有观察到，不是发现了违反（细则 13.5）。"
            )
    else:
        out.append("没有命题进入检查。")
    out.extend(
        f"- {p['id']}　{p['sentence']}　`{p['formula']}`" for p in sorted(data["accepted"], key=lambda p: _num(p["id"]))
    )
    out.append("")
    if data["discarded"]:
        out.append("## 未通过检查")
        discarded = sorted(data["discarded"], key=lambda p: _num(p["id"]))
        out.extend(f"- {p['id'] or '（无编号）'}　{p['sentence']}：{'；'.join(p['problems'])}" for p in discarded)
        out.append("")
    if data["bindings"]:
        out.append("## 绑定")
        for name, what in data["bindings"].items():
            out.append(f"- {name}：{what}")
        out.append("")
    s = data["stats"]
    out.append("## 统计")
    out.append(
        f"- 提交检查的命题 {s['submitted']} 条：翻译 {s['translated']} 条，推理 {s['reasoned']} 条，未套用句式 {s['unpatterned']} 条"
    )
    out.append(f"- 其中被丢弃 {s['discarded']} 条")
    if s["by_result"]:
        order = sorted(s["by_result"].items(), key=lambda kv: M.ORDER.get(kv[0], 9))
        out.append("- 结果：" + "，".join(f"{k} {v} 条" for k, v in order))
    out.append("")
    if data["notes"]:
        out.append("## 注")
        out.extend(f"- {n}" for n in data["notes"])
        out.append("")
    out.append("## 比对结论")
    out.append(f"**{data['conclusion']}**：{data['conclusion_reason']}。")
    presumed_false = [pid for pid, r in data["results"].items() if r["result"] == M.PRESUMED_FALSE]
    if presumed_false:
        out.append(f"推定为假不计入未通过，单独列出：{'、'.join(sorted(presumed_false, key=_num))}（细则 14.2）。")
    out.append("")
    out.append("## 说明")
    out.append(
        "本报告是概率凭证。只有满足文档式 (7) 时，通过检查的代码精确率才高于基准正确率；报告不判断式 (7) 是否成立。"
        "交叉验证不能确保命题与需求的语义一致，只让通过检查的代码与需求对齐的概率更高。"
    )
    return "\n".join(out) + "\n"


def write(run: Path, extra: dict, config: dict) -> str:
    data = build(run, extra, config)
    text = render(data)
    S.write_text(Path(run) / "report.md", text)
    S.write_json(Path(run) / "report.json", data)
    return text
