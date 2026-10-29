import pytest

import cv_formula as F
import cv_grammar as G
from cv_formula import FormulaError

# The document's ten snake propositions (第 3 章"推理"), R6 and R9 written as 细则 17.2 requires.
SNAKE = {
    "R1": ("存在一个 x，使 x 是 蛇", "∃x 蛇(x)"),
    "R2": ("只要 蛇的长度 不小于 2，就 蛇的头 不等于 蛇的尾", "G(长度(蛇) ≥ 2 → 头(蛇) ≠ 尾(蛇))"),
    "R3": ("总是 蛇的长度 不小于 1", "G(长度(蛇) ≥ 1)"),
    "R4": ("只要 蛇 吃到 食物，下一步 蛇的长度 等于 蛇的长度 加 1", "G(吃到(蛇, 食物) → 长度(蛇)′ = 长度(蛇) + 1)"),
    "R5": ("只要 蛇 撞到自身，下一步 游戏 结束", "G(撞到自身(蛇) → X 结束(游戏))"),
    "R6": ("只要 食物 新生成，就 食物 不 位于蛇身", "G(新生成(食物) → ¬位于蛇身(食物))"),
    "R7": ("一旦 游戏 结束，此后一直 游戏 结束", "G(结束(游戏) → G 结束(游戏))"),
    "R8": ("只要 游戏 没有结束，最终 蛇 移动", "G(¬结束(游戏) → F 移动(蛇))"),
    "R9": ("代码中存在表示 蛇的长度 的元素", "表示(长度(蛇))"),
    "R10": ("只要 蛇的长度 等于 格子总数，就 游戏 结束", "G(长度(蛇) = 格子总数 → 结束(游戏))"),
}


def text(sentence, decls):
    return F.to_text(G.parse_sentence(sentence, decls)[0])


@pytest.mark.parametrize("pid", list(SNAKE))
def test_snake_propositions(pid, decls):
    """细则 8.9 D-22 D-9 the document's R1–R10 give the document's formulas."""
    sentence, formula = SNAKE[pid]
    assert text(sentence, decls) == formula


def test_snake_rewrites(decls):
    """细则 17.2 the document's own wording of R6 and R9 does not parse; the rewritten one does."""
    with pytest.raises(FormulaError):
        G.parse_sentence("只要 食物 新生成，就 食物 不 位于 蛇身 上", decls)
    with pytest.raises(FormulaError):
        G.parse_sentence("代码中存在表示蛇的长度的元素", decls)


def test_lexing(decls):
    """细则 8.1 spaces separate words, ， separates clauses, literals in JSON or 「」, 。 optional."""
    assert text("蛇的头 等于 [1, 2]。", decls) == "头(蛇) = [1, 2]"
    assert text("蛇的头 等于 「左上角」", decls) == "头(蛇) = 「左上角」"
    assert text("蛇的长度 等于 -1.5", decls) == "长度(蛇) = -1.5"
    assert text('调用 `move`(「右」, {"x": 1}) 得到 true', decls) == '`move`(「右」, {"x": 1}) = true'
    with pytest.raises(FormulaError):
        G.parse_sentence("只要 蛇 吃到 食物 就 游戏 结束", decls)


def test_terms(decls):
    """细则 8.2 a的f, f, f(a), entity names, variables, arithmetic precedence and ambiguity."""
    assert text("长度(蛇) 等于 格子总数 减 2 乘 3", decls) == "长度(蛇) = 格子总数 − 2 × 3"
    assert text("蛇的长度 等于 (格子总数 减 2) 乘 3", decls) == "长度(蛇) = (格子总数 − 2) × 3"
    d = F.Decls(dict(decls.category), dict(decls.params), {})
    d.category["蛇的头"], d.params["蛇的头"] = "实体", []
    d.category["头的长度"], d.params["头的长度"] = "数量", ["蛇"]
    with pytest.raises(FormulaError, match="歧义"):
        G.parse_sentence("总是 蛇的头的长度 大于 0", d)
    with pytest.raises(FormulaError, match="无法识别的项"):
        G.parse_sentence("总是 蛇的速度 大于 0", decls)


def test_atoms(decls):
    """细则 8.3 natural and functional predicates, 是/不是, 不/没有 and their glued forms."""
    assert text("蛇 吃到 食物", decls) == "吃到(蛇, 食物)"
    assert text("吃到(蛇, 食物)", decls) == "吃到(蛇, 食物)"
    assert text("游戏 不 结束", decls) == "¬结束(游戏)"
    assert text("游戏 没有结束", decls) == "¬结束(游戏)"
    assert text("游戏 不结束", decls) == "¬结束(游戏)"
    assert text("存在一个 x，使 x 不是 食物", decls) == "∃x ¬食物(x)"
    tree, _ = G.parse_sentence("蛇 吃到", decls)
    assert F.type_errors(tree, decls) == ["吃到 声明了 2 个参数，写了 1 个"]


def test_connectives(decls):
    """细则 8.4 不 > 并且 > 或者 > 如果…那么, with parentheses."""
    tree, _ = G.parse_sentence("总是 如果 游戏 结束 或者 蛇 移动 并且 蛇 吃到 食物 那么 不 结束(游戏)", decls)
    assert F.to_text(tree) == "G(结束(游戏) ∨ 移动(蛇) ∧ 吃到(蛇, 食物) → ¬结束(游戏))"
    tree, _ = G.parse_sentence("总是 不 (游戏 结束 或者 蛇 移动)", decls)
    assert F.to_text(tree) == "G ¬(结束(游戏) ∨ 移动(蛇))"


@pytest.mark.parametrize(
    ("sentence", "pattern", "formula"),
    [
        ("调用 `move`(1) 得到 2", "调用得到", "`move`(1) = 2"),
        ("调用 `move`(-1) 失败", "调用失败", "失败(`move`(-1))"),
        ("初始时 蛇的长度 等于 3", "初始时", "长度(蛇)₀ = 3"),
        ("蛇的长度 等于 3", "具体事实", "长度(蛇) = 3"),
        ("总是 蛇 移动", "总是", "G 移动(蛇)"),
        ("从不 游戏 结束", "从不", "G ¬结束(游戏)"),
        ("只要 蛇 吃到 食物，就 蛇 移动", "只要就", "G(吃到(蛇, 食物) → 移动(蛇))"),
        ("只要 蛇 吃到 食物，下一步 蛇 移动", "只要下一步", "G(吃到(蛇, 食物) → X 移动(蛇))"),
        ("只要 蛇 吃到 食物，最终 游戏 结束", "只要最终", "G(吃到(蛇, 食物) → F 结束(游戏))"),
        ("一旦 游戏 结束，此后一直 游戏 结束", "一旦", "G(结束(游戏) → G 结束(游戏))"),
        ("在 游戏 结束 之前，不会 蛇 撞到自身", "在之前", "¬撞到自身(蛇) W 结束(游戏)"),
        ("存在一个 食物 x，使 x 新生成", "存在", "∃x:食物 新生成(x)"),
        ("恰好存在一个 x，使 x 是 蛇", "恰好存在", "∃!x 蛇(x)"),
        ("对所有 食物 x，x 不 位于蛇身", "对所有", "∀x:食物 ¬位于蛇身(x)"),
        ("下一步 蛇的长度 等于 3", "下一步", "长度(蛇)′ = 3"),
        (
            "从 蛇的长度 等于 3 出发，依次执行 右、下，之后 游戏 结束",
            "从出发",
            "长度(蛇)₀ = 3 ∧ 动作₀ = 「右」 ∧ 动作₁ = 「下」 → 结束(游戏)₂",
        ),
        ("代码中存在 函数 `move`", "结构：存在", "存在(`move`)"),
        ("`move` 的签名是 「(方向) → 无」", "结构：签名", "签名(`move`) = 「(方向) → 无」"),
        ("`move` 包含 方法 `move`", "结构：包含", "包含(`move`, `move`)"),
        ("`move` 不调用 `move`", "结构：调用", "¬调用(`move`, `move`)"),
        ("只有 `move` 修改 蛇的头", "结构：只有", "只由(`move`, 头(蛇))"),
        ("代码中存在表示 结束 的元素", "结构：表示", "表示(结束)"),
    ],
)
def test_patterns(sentence, pattern, formula, decls):
    """细则 8.5 每个句式都有一个"句子 → 公式"的测试 (D-9)."""
    tree, got = G.parse_sentence(sentence, decls)
    assert (got, F.to_text(tree)) == (pattern, formula)
    assert not F.type_errors(tree, decls)


def test_next_step(decls):
    """细则 8.6 下一步: X on literals, ′ on the left of comparisons, the right side read now."""
    assert text("下一步 游戏 结束 并且 蛇 不 移动", decls) == "X(结束(游戏) ∧ ¬移动(蛇))"
    assert text("下一步 蛇的长度 不等于 蛇的长度 并且 游戏 结束", decls) == "长度(蛇)′ ≠ 长度(蛇) ∧ X 结束(游戏)"


def test_quantifiers(decls):
    """细则 8.7 quantifiers only outermost; an untyped variable only in x 是 T."""
    with pytest.raises(FormulaError):
        G.parse_sentence("总是 存在一个 x，使 x 是 蛇", decls)
    tree, _ = G.parse_sentence("存在一个 x，使 x 吃到 食物", decls)
    assert any("类型不明" in e for e in F.type_errors(tree, decls))


def test_reserved_words():
    """细则 8.8 the reserved list in the code is the README's."""
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    block = readme.split("细则 8.8　**保留字。**")[1].split("```")[1]
    assert set(block.split()) == G.RESERVED
