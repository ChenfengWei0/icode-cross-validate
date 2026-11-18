"""The credibility report (README 第 14 节; 算法1 第8行)."""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

import cv_monitor as M
import cv_state as S

TITLE = "交叉验证可信度报告"
INVALID, NO_CONCLUSION, FAILED, PASSED = "无效", "无结论", "比对未通过", "比对通过"
ASCII_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def _without_review(p: dict) -> dict:
    """细则 17.12: the proposition as it was before the review (细则 7.5) marked it."""
    q = {k: v for k, v in p.items() if k != "conflict"}
    if q.get("unstated_by") == "review":
        q.pop("unstated", None)
    return q


def _failing(results: dict, accepted: list[dict]) -> list[str]:
    """细则 13.8, 14.2, 7.5: counted failures; a conflict group fails only when every member fails."""
    by_id = {p["id"]: p for p in accepted}
    failed = []
    for p in accepted:
        r = results.get(p["id"])
        if not r or r["result"] not in M.FAILING or p.get("unstated"):
            continue
        group = [p["id"], *p.get("conflict", [])]
        if all(results.get(q, {}).get("result") in M.FAILING and not by_id.get(q, {}).get("unstated") for q in group):
            failed.append(p["id"])
    return failed


def conclusion(
    data: dict, max_discard_ratio: float, max_confirm_ratio: float = 0.5, ignore_review: bool = False
) -> tuple[str, str]:
    """细则 14.2: event A of chapter 2, or why there is none. ignore_review: as if no review mark were made."""
    if data.get("invalid"):
        return INVALID, "；".join(data["invalid"])
    if data.get("stopped"):
        return NO_CONCLUSION, data["stopped"]
    submitted = data["stats"]["submitted"]
    results = data["results"]
    if len(results) == 0:
        return NO_CONCLUSION, "没有命题进入检查"
    if submitted and data["stats"]["discarded"] / submitted > max_discard_ratio:
        return NO_CONCLUSION, f"被丢弃的命题超过提交总数的 {max_discard_ratio:.0%}"
    accepted = [_without_review(p) for p in data["accepted"]] if ignore_review else data["accepted"]
    # 细则 14.2: an error found is reported even when the rest would give no conclusion.
    if _failing(results, accepted):
        return FAILED, "至少一条计入结论的命题的结果是失败或确定违反"
    marked = [p for p in accepted if _for_a_person(p)]
    if accepted and len(marked) / len(accepted) > max_confirm_ratio:
        return NO_CONCLUSION, f"标了需求未说明或需求矛盾的命题超过通过检查的命题的 {max_confirm_ratio:.0%}"
    # 细则 14.2: structure alone, or a check that never met its antecedent, finds no error in behaviour
    # (约束 1), so it issues no certificate. A conflict group is counted (细则 7.5).
    counted = [
        p["id"]
        for p in accepted
        if p["id"] in results
        and not p.get("unstated")
        and p.get("class") != "结构"
        and not results[p["id"]].get("vacuous")
    ]
    if not counted:
        return NO_CONCLUSION, "计入结论的命题中没有一条真正检验过行为的非结构命题"
    return PASSED, "没有计入结论的命题失败或被确定违反"


def _for_a_person(p: dict) -> bool:
    """细则 13.8: marked unstated (细则 7.3, 7.5) or in conflict with another proposition (细则 7.5)."""
    return bool(p.get("unstated") or p.get("conflict"))


def to_confirm(results: dict, accepted: list[dict]) -> list[str]:
    """细则 13.8: what a person has to look at, one line each; none of it decides the conclusion."""
    found = (*M.FAILING, M.PRESUMED_FALSE)
    lines = []
    for p in sorted(accepted, key=lambda p: _num(p["id"])):
        r = results.get(p["id"], {})
        if p.get("unstated") and r.get("result") in found:
            detail = _detail(r)
            lines.append(
                f"{p['id']}（需求未说明）{p['sentence']}：{r['result']}。依据：{_plain(p['basis'])}"
                + (f"；{detail}" if detail else "")
            )
        if p.get("conflict") and not p.get("unstated") and r.get("result") in found:
            detail = _detail(r)
            why = "；".join(_plain(x["reason"], 120) for x in p.get("review", []) if x.get("mark") == "〔需求矛盾〕")
            lines.append(
                f"{p['id']}（需求矛盾，与 {'、'.join(p['conflict'])}）{p['sentence']}：{r['result']}。复查：{why}"
                + (f"；{detail}" if detail else "")
            )
        for c in r.get("unstated_inputs", []):
            if c["result"] in found:
                detail = _detail(c)
                lines.append(
                    f"{p['id']}（超出需求的输入，检查「{_plain(c.get('form') or '', 40)}」）{p['sentence']}："
                    f"{c['result']}" + (f"；{detail}" if detail else "")
                )
    return lines


def _num(pid: str) -> int:
    return int(pid[1:]) if pid[1:].isdigit() else 10**9


def _rows(props: list[dict], results: dict, forms: dict) -> list[dict]:
    rows = []
    for p in sorted(props, key=lambda p: _num(p["id"])):
        r = results.get(p["id"], {})
        rows.append(
            {
                "ids": [p["id"]],
                "source": p["source"]
                + ("（未套用句式）" if p.get("pattern") == "未套用句式" else "")
                + ("（需求未说明）" if p.get("unstated") else "")
                + ("（需求矛盾）" if p.get("conflict") else ""),
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


def _plain(text: str, limit: int = 300) -> str:
    """Untrusted text (program output, bindings) on one line, unable to forge report structure."""
    one = " ".join(str(text).split())[-limit:]
    return one.replace("|", "｜").replace("*", "＊").replace("#", "＃").replace("`", "'")


def _detail(r: dict) -> str:
    parts = []
    if r.get("reason"):
        parts.append(_plain(r["reason"]))
    parts.extend(r.get("notes") or [])  # 细则 14.1 第 3 项: timeouts, truncation
    cex = r.get("counterexample")
    if cex:
        if cex.get("engine"):  # 细则 11.11: a counterexample from the verifier, over the recorded states
            shown = json.dumps([st["值"] for st in cex.get("last_states", [])[-3:]], ensure_ascii=False)
            parts.append(
                f"反例（{_plain(cex['engine'], 20)}）：状态 {cex.get('state')}，最后的状态 {_plain(shown, 400)}"
            )
        elif "input" in cex:  # 细则 11.12: a run of the program on a frozen input
            said = f"输入「{_plain(cex['input'], 200)}」，输出「{_plain(cex.get('output') or '', 200)}」"
            why = f"，{_plain(cex['why'], 200)}" if cex.get("why") else ""
            parts.append(f"反例：第 {cex.get('run')} 次运行，{said}{why}")
        elif "actions" in cex:
            acts = [a for a in cex["actions"] if a is not None]
            shown = acts if len(acts) <= 12 else ["…", *acts[-12:]]
            seq = f"动作序列（共 {len(acts)} 步）{json.dumps(shown, ensure_ascii=False)}"
            parts.append(f"反例：第 {cex.get('run')} 次运行，状态 {cex['state']}，{seq}，完整序列见 report.json")
        elif "state" in cex:
            parts.append(f"反例：第 {cex.get('run')} 次运行，状态 {cex['state']}")
        elif cex.get("stderr") is not None:
            said = " ".join(_plain(cex.get(k) or "") for k in ("stderr", "stdout")).strip()  # 细则 12.6
            parts.append(f"程序异常退出（退出码 {cex.get('exit')}）：" + said)
        elif cex.get("stdout"):
            parts.append("反例：" + _plain(cex["stdout"]))
    return "；".join(parts)


def _ids(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else f"{ids[0]} 至 {ids[-1]}"


def _groups(accepted: list[dict]) -> list[list[str]]:
    """细则 7.5: the conflict groups, each sorted by id."""
    seen, groups = set(), []
    for p in accepted:
        if p.get("conflict") and p["id"] not in seen:
            group = sorted({p["id"], *p["conflict"]}, key=_num)
            seen.update(group)
            groups.append(group)
    return groups


def review_lines(results: dict, accepted: list[dict]) -> list[str]:
    """细则 14.1: every review mark with its reason; each conflict group with every member's result."""
    lines = []
    for p in sorted(accepted, key=lambda p: _num(p["id"])):
        if p.get("unstated_by") == "review":
            why = next((x["reason"] for x in p.get("review", []) if x.get("mark") == "〔需求未说明〕"), "")
            result = results.get(p["id"], {}).get("result", "未检查")
            lines.append(f"{p['id']}（需求未说明）{p['sentence']}：{result}。复查：{_plain(why, 200)}")
    by_id = {p["id"]: p for p in accepted}
    for group in _groups(accepted):
        sides = "；".join(
            f"{q} {by_id[q]['sentence']}：{results.get(q, {}).get('result', '未检查')}" for q in group if q in by_id
        )
        why = "；".join(
            dict.fromkeys(
                _plain(x["reason"], 120)
                for q in group
                for x in by_id.get(q, {}).get("review", [])
                if x.get("mark") == "〔需求矛盾〕"
            )
        )
        lines.append(f"需求矛盾 {'、'.join(group)}：{sides}。复查：{why}")
    return lines


def build(run: Path, extra: dict, config: dict) -> dict:
    """Collect everything the report needs from the run directory."""
    run = Path(run)
    info = S.read_json(run / "run.json", {})
    req = S.read_json(run / "requirement.json", {}) or {}
    props = S.read_json(run / "propositions.json", {}) or {}
    frozen = S.read_json(run / "align" / "checks.frozen.json", {}) or {}
    results = S.read_json(run / "results.json", {}) or {}
    # 细则 11.12: propositions io alignment set aside before freezing count as discarded.
    excluded = frozen.get("excluded", {})
    accepted = [p for p in props.get("propositions", []) if not p.get("problems") and p["id"] not in excluded]
    discarded = [p for p in props.get("propositions", []) if p.get("problems")]
    discarded += [
        {**p, "problems": [excluded[p["id"]]]}
        for p in props.get("propositions", [])
        if not p.get("problems") and p["id"] in excluded
    ]
    forms: dict[str, list[str]] = {}
    for c in frozen.get("checks", []):
        form = _plain(c["form"], 40)
        if form and form not in forms.setdefault(c["proposition"], []):
            forms[c["proposition"]].append(form)
    submitted = props.get("propositions", [])
    stats = {
        "submitted": len(submitted),
        "translated": sum(p["source"] == "翻译" and p.get("pattern") != "未套用句式" for p in submitted),
        "reasoned": sum(p["source"] == "推理" and p.get("pattern") != "未套用句式" for p in submitted),
        "unpatterned": sum(p.get("pattern") == "未套用句式" for p in submitted),
        "discarded": len(discarded),
        "by_result": {},
        "vacuous": 0,
        "unstated": sum(bool(p.get("unstated")) for p in accepted),
        "unstated_by_review": sum(p.get("unstated_by") == "review" for p in accepted),
        "conflict": len(_groups(accepted)),
        "verified": len(
            {
                c["proposition"]
                for c in results.get("checks", [])
                if c.get("kind") == "verify" and c.get("result") in (M.PRESUMED_TRUE, M.VIOLATED)
            }
        ),
        "unstated_inputs": 0,
    }
    prop_results = results.get("propositions", {})
    for r in prop_results.values():
        stats["by_result"][r["result"]] = stats["by_result"].get(r["result"], 0) + 1
        stats["vacuous"] += 1 if r.get("vacuous") else 0
        stats["unstated_inputs"] += len(r.get("unstated_inputs", []))
    stats["counted"] = sum(
        1 for p in accepted if p["id"] in prop_results and not p.get("unstated") and p.get("class") != "结构"
    )
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
        "to_confirm": to_confirm(prop_results, accepted),
        "review": review_lines(prop_results, accepted),
    }
    pre = (frozen.get("io") or {}).get("precheck") or {}
    if pre.get("dropped"):  # 细则 11.12 输入预检, 细则 14.1 第 3 项
        data["notes"].append(
            f"io 输入预检去掉了 {pre['dropped']} 个生成的输入（共 {pre.get('generated', '?')} 个），"
            f"第一个原因：{_plain(pre.get('first') or '', 200)}（细则 11.12）"
        )
    ratios = (config["max_discard_ratio"], config.get("max_confirm_ratio", 0.5))
    data["conclusion"], data["conclusion_reason"] = conclusion(data, *ratios)
    data["conclusion_without_review"] = conclusion(data, *ratios, ignore_review=True)[0]  # 细则 17.12
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
        implicit = sorted(
            ((m, v.split()[-1]) for m, v in req.get("messages", {}).items() if v.startswith("隐含更正")),
            key=lambda x: int(x[0][1:]),
        )
        if implicit:  # 细则 15.4: the subtask's own judgment, so each one is shown for the user to confirm
            out.append("- 隐含更正（没有明说是修改，按后一条为准，请确认）：")
            texts = req.get("message_texts", {})
            for m, k in implicit:
                out.append(
                    f"  - {m} 取代 {k}：「{_plain(texts.get(m, ''), 120)}」取代「{_plain(texts.get(k, ''), 120)}」"
                )
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
    if data["to_confirm"]:
        out.append("## 需要人确认")
        out.append(
            "下面的命题或输入，需求没有规定代码该怎样，命题和代码可能只是读法不同。结果不计入比对结论（细则 13.8），"
            "请判断是补充需求还是修改代码。"
        )
        out.extend(f"- {line}" for line in data["to_confirm"])
        out.append("")
    if data.get("review"):
        out.append("## 复查标记")
        out.append(
            "子任务在复查中标出的需求未说明和需求矛盾（细则 7.5）。需求矛盾的一组，组内全部失败才计入比对未通过。"
        )
        out.extend(f"- {line}" for line in data["review"])
        out.append("")
    if data["bindings"]:
        out.append("## 绑定")
        for name, what in data["bindings"].items():
            out.append(f"- {_plain(name, 100)}：{_plain(what)}")
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
    if s.get("vacuous"):
        out.append(f"- 其中前件从未成立、实际没有被检验 {s['vacuous']} 条（细则 13.7）")
    if s.get("verified"):
        out.append(f"- 由验证器在给定步数内考察了全部输入的命题 {s['verified']} 条（细则 11.11）")
    if s.get("unstated"):
        by_review = f"（其中复查补标 {s['unstated_by_review']} 条）" if s.get("unstated_by_review") else ""
        out.append(f"- 其中需求未说明 {s['unstated']} 条{by_review}，结果不计入比对结论（细则 13.8）")
    if s.get("conflict"):
        out.append(f"- 需求矛盾 {s['conflict']} 组，每组全部失败才计入比对未通过（细则 7.5）")
    if "counted" in s:
        out.append(f"- 计入结论的命题 {s['counted']} 条")
    if data.get("conclusion_without_review"):
        out.append(f"- 不算复查标记时的结论：{data['conclusion_without_review']}（细则 17.12）")
    if s.get("unstated_inputs"):
        out.append(
            f"- 对齐 agent 标为超出需求的输入的检查 {s['unstated_inputs']} 项，结果照常计入比对结论，另列供人确认（细则 13.8）"
        )
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
    if data["to_confirm"]:
        out.append(f"另有 {len(data['to_confirm'])} 处需要人确认，不计入结论，见“需要人确认”。")
    out.append("")
    out.append("## 说明")
    out.append(
        "本报告是概率凭证。只有满足文档式 (7) 时，通过检查的代码精确率才高于基准正确率；报告不判断式 (7) 是否成立。"
        "交叉验证不能确保命题与需求的语义一致，只让通过检查的代码与需求对齐的概率更高。"
    )
    return "\n".join(out) + "\n"


def write_minimal(run: Path, reason: str) -> str:
    """细则 4.8: when the report itself cannot be built, a short one with no conclusion and the reason."""
    text = f"# {TITLE}\n\n## 比对结论\n**{NO_CONCLUSION}**：{reason}。\n"
    with contextlib.suppress(OSError):
        S.write_text(Path(run) / "report.md", text)
        S.write_json(
            Path(run) / "report.json", {"title": TITLE, "conclusion": NO_CONCLUSION, "conclusion_reason": reason}
        )
    return text


def write(run: Path, extra: dict, config: dict) -> str:
    data = build(run, extra, config)
    text = render(data)
    S.write_text(Path(run) / "report.md", text)
    S.write_json(Path(run) / "report.json", data)
    return text
