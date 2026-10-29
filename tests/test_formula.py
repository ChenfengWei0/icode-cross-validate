import pytest

import cv_formula as F
import cv_grammar as G
from test_grammar import SNAKE


@pytest.mark.parametrize("pid", list(SNAKE))
def test_round_trip(pid, decls):
    """细则 10.2 printing then parsing gives the same tree."""
    tree, _ = G.parse_sentence(SNAKE[pid][0], decls)
    assert F.parse(F.to_text(tree), decls) == tree


def test_ascii_notation(decls):
    """细则 10.1 the ASCII spelling parses to the same tree as the document's notation."""
    uni = F.parse("G(吃到(蛇, 食物) → 长度(蛇)′ = 长度(蛇) + 1) ∧ ¬结束(游戏)₀ ∨ 长度(蛇) ≥ 2", decls)
    asc = F.parse("G(吃到(蛇, 食物) -> 长度(蛇)' = 长度(蛇) + 1) & !结束(游戏)_0 | 长度(蛇) >= 2", decls)
    assert uni == asc


def test_precedence(decls):
    """细则 10.1 ¬ > G F X > ∧ > ∨ > U W > →, U/W and → right-associative, quantifiers extend right."""
    t = F.parse("移动(蛇) ∧ 结束(游戏) ∨ 移动(蛇) U 结束(游戏) → 移动(蛇) → 结束(游戏)", decls)
    assert t["op"] == "implies" and t["args"][1]["op"] == "implies"
    assert t["args"][0]["op"] == "U" and t["args"][0]["args"][0]["op"] == "or"
    q = F.parse("∀x:食物 新生成(x) → X ¬位于蛇身(x)", decls)
    assert q["op"] == "forall" and q["args"][0]["op"] == "implies"


def test_ops(decls):
    """细则 10.2 the tree uses only the listed ops."""
    allowed = {
        "not",
        "and",
        "or",
        "implies",
        "G",
        "F",
        "X",
        "U",
        "W",
        "exists",
        "exists1",
        "forall",
        "eq",
        "ne",
        "lt",
        "gt",
        "le",
        "ge",
        "add",
        "sub",
        "mul",
        "div",
        "pred",
        "term",
        "var",
        "const",
        "lit",
        "next",
        "at",
        "call",
        "fails",
        "struct",
        "code",
    }
    for sentence, _ in SNAKE.values():
        tree, _ = G.parse_sentence(sentence, decls)
        assert {n["op"] for n in F.walk(tree)} <= allowed


def test_classes(decls):
    """细则 10.4 the class follows from the tree."""
    cls = {pid: F.classify(G.parse_sentence(s, decls)[0]) for pid, (s, _) in SNAKE.items()}
    assert cls["R1"] == "具体事实" and cls["R9"] == "结构"
    assert all(cls[p] == "时序" for p in ("R2", "R3", "R4", "R5", "R6", "R7", "R8", "R10"))
    assert F.classify(F.parse("长度(蛇)′ = 1", decls)) == "时序"
