import re
from pathlib import Path

import cv_check as C
import cv_flow as FLOW
import cv_grammar as G
import cv_text as T

PROMPTS = Path(__file__).resolve().parents[1] / "cross-validate" / "prompts"
PROPOSE = (PROMPTS / "propose.md").read_text(encoding="utf-8")


def examples():
    blocks = re.findall(r"```\n(声明\n.*?)```", PROPOSE, re.DOTALL)
    assert len(blocks) == 2
    return blocks


def test_example_one_passes():
    """D-19 细则 7 the first worked example in the subtask prompt passes every check."""
    r = "实现一个交通信号灯。"
    parsed = T.parse_subtask(examples()[0], r)
    assert parsed["errors"] == []
    out = C.check_propositions(parsed, [{"id": "S1", "text": r, "labels": ["无"], "quotable": [r]}], r)
    assert {p["id"]: p["problems"] for p in out} == {f"R{i}": [] for i in range(1, 7)}


def test_example_two_passes():
    """D-6 细则 9.1 the second example's translations quote labelled sentences and pass."""
    r = "账户余额不能为负。转账时，转出账户减少的金额等于转入账户增加的金额。例如余额 100 时转出 30，余额变为 70。"
    texts = [
        "账户余额不能为负。",
        "转账时，转出账户减少的金额等于转入账户增加的金额。",
        "例如余额 100 时转出 30，余额变为 70。",
    ]
    labels = [["规则"], ["规则", "条件"], ["示例"]]
    sentences = [
        {"id": f"S{i + 1}", "text": t, "labels": lb, "quotable": [t]}
        for i, (t, lb) in enumerate(zip(texts, labels, strict=True))
    ]
    parsed = T.parse_subtask(examples()[1], r)
    assert parsed["errors"] == []
    out = {p["id"]: p for p in C.check_propositions(parsed, sentences, r)}
    assert {k: p["problems"] for k, p in out.items()} == {f"R{i}": [] for i in range(1, 7)}
    assert [out[k]["source"] for k in ("R1", "R2", "R3")] == ["翻译"] * 3 and out["R3"]["pattern"] == "从出发"


def test_reserved_words_in_prompt():
    """细则 8.8 the subtask prompt lists exactly the reserved words."""
    block = PROPOSE.split("## 保留字")[1].split("```")[1]
    assert set(block.split()) == G.RESERVED


def test_checklist_in_prompt():
    """细则 7.2 D-7 the prompt names the nine items in the document's order."""
    section = PROPOSE.split("## 清单")[1].split("## 命题")[0]
    items = re.findall(r"^\d\. (\S+?)：", section, re.MULTILINE)
    assert items == T.CHECKLIST


def test_placeholders_filled():
    """细则 4.6 every prompt template is completely filled; the subtask prompts take only requirement text."""
    values = {
        "MESSAGES": "m",
        "REQUIREMENT": "r",
        "SENTENCES": "s",
        "WHAT": "w",
        "ERRORS": "e",
        "PREVIOUS": "p",
        "TASK": "t",
        "PROBLEMS": "x",
        "DECLS": "d",
        "PROPOSITIONS": "q",
        "W": "/w",
        "ALIGN": "/a",
    }
    for path in PROMPTS.glob("*.md"):
        text = FLOW.prompt(path.stem, **values)
        assert "{{" not in text, path.name
    for name in ("consolidate", "split", "propose"):
        placeholders = set(re.findall(r"\{\{(\w+)\}\}", (PROMPTS / f"{name}.md").read_text(encoding="utf-8")))
        assert placeholders <= {"MESSAGES", "REQUIREMENT", "SENTENCES"}, name
