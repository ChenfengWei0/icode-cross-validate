"""verify checks (README 细则 11.11): generated monitors, their agreement with the checker, and CBMC."""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

import cv_formula as F
import cv_grammar as G
import cv_monitor as M
import cv_run as RUN
import cv_text as T
import cv_verify as V
from test_regressions import _formula

GCC = shutil.which("gcc")
CBMC = shutil.which("cbmc")
SNAKE = ["实体  蛇、食物、游戏", "谓词  吃到(蛇, 食物)  这一步吃到", "      结束(游戏)  游戏处于结束状态"]
SNAKE += ["      位于蛇身(食物)  食物所在的格子被蛇身占据", "数量  长度(蛇)  长度"]


def snake_decls():
    decls, errors = T.parse_declarations(SNAKE, "")
    assert errors == []
    return decls


def test_fragment():
    """细则 11.11 适用的命题: safety only; no quantifier, subscript, action, call, structure or division."""
    d = snake_decls()
    ok = [
        "只要 蛇 吃到 食物，下一步 蛇的长度 等于 蛇的长度 加 1",
        "一旦 游戏 结束，此后一直 游戏 结束",
        "总是 蛇的长度 不小于 1",
    ]
    for s in ok:
        assert V.problems(G.parse_sentence(s, d)[0], {"蛇", "食物", "游戏"}) == [], s
    bad = {
        "只要 游戏 没有 结束，最终 蛇 吃到 食物": "活性",
        "蛇的长度 等于 1": "时序",
        "只要 蛇 吃到 食物，下一步 蛇的长度 除 2 等于 1": "除法",
    }
    for s, why in bad.items():
        assert any(why in x for x in V.problems(G.parse_sentence(s, d)[0], {"蛇", "食物", "游戏"})), s
    negated = F.parse("G ¬(长度(蛇)′ = 长度(蛇))", d)  # the strong next of 细则 10.3
    assert any("活性" in x for x in V.problems(negated, set()))
    assert any("量词" in x for x in V.problems(F.parse("∀x:食物 G(位于蛇身(x) → 结束(游戏))", d), set()))


def _c_program(cases: list[tuple[str, dict, list[dict]]]) -> str:
    lines = ["#include <stdio.h>", 'void __CPROVER_assert(int c, const char *m) { if (!c) printf("%s\\n", m); }']
    for pid, tree, _ in cases:
        lines.append(V.header(pid, tree, 7, "", "t"))
    lines.append("int main(void) {")
    for pid, tree, states in cases:
        _, fields, present = V._layout(tree)
        for st in states:
            parts = [f".k{i} = {int(st['值'][k])}" for k, i in fields.items()]
            parts += [f".p{i} = {int(bool(st.get('实体', {}).get(c, [c])))}" for c, i in present.items()]
            lines.append(f"  {{ cv_{pid}_state s = {{ {', '.join(parts)} }}; cv_{pid}_observe(&s); }}")
        lines.append(f"  cv_{pid}_check();")
    lines.append("  return 0;\n}")
    return "\n".join(lines)


@pytest.mark.skipif(GCC is None, reason="needs gcc")
def test_generated_code_agrees_with_the_checker(tmp_path):
    """细则 11.11 生成代码与 checker 的一致性: on random safety formulas and traces, the C monitor fails
    exactly where 细则 13.2 judges 确定违反, and sees the antecedent exactly where 细则 13.7 does."""
    rng = random.Random(7)
    cases = []
    while len(cases) < 300:
        f = _formula(rng, 3)
        if V.problems(f, set()):
            continue
        n = rng.randint(1, 7)
        states = [{"值": {"p": rng.random() < 0.5, "q": rng.random() < 0.5, "n": rng.randint(0, 3)}} for _ in range(n)]
        cases.append((f"R{len(cases) + 1}", f, states))
    src = tmp_path / "t.c"
    src.write_text(_c_program(cases), encoding="utf-8")
    subprocess.run([GCC, "-std=gnu11", "-w", "-o", str(tmp_path / "t"), str(src)], check=True)
    printed = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True).stdout.split("\n")
    failed = {line.split()[1] for line in printed if f" {V.MAIN} " in f" {line} "}
    fired = {line.split()[1] for line in printed if f" {V.FIRED} " in f" {line} "}
    for pid, tree, states in cases:
        want = M.judge(tree, states, set())["result"] == M.VIOLATED
        assert (pid in failed) == want, (pid, F.to_text(tree), states)
        trig = M.triggered(tree, states, set())
        if trig is not None:
            assert (pid in fired) == trig, (pid, F.to_text(tree))


@pytest.mark.skipif(GCC is None, reason="needs gcc")
def test_absent_individual_makes_atoms_false(tmp_path):
    """细则 11.11 12.4 a constant whose entity has no individual now: its atoms are false, their negation true."""
    d = snake_decls()
    tree = G.parse_sentence("从不 食物 位于蛇身", d)[0]
    states = [
        {"实体": {"食物": ["食物"]}, "值": {"位于蛇身(食物)": False}},
        {"实体": {"食物": []}, "值": {"位于蛇身(食物)": True}},  # a stale value while no food exists
    ]
    assert M.judge(tree, states, {"蛇", "食物", "游戏"})["result"] != M.VIOLATED
    src = tmp_path / "t.c"
    src.write_text(_c_program([("R1", tree, states)]), encoding="utf-8")
    subprocess.run([GCC, "-std=gnu11", "-w", "-o", str(tmp_path / "t"), str(src)], check=True)
    out = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True, check=True).stdout
    assert V.MAIN not in out


GAME = """
typedef struct { int len, head, food, over; } Game;
void step(Game *g) {
  if (g->over) return;
  g->head++;
  if (g->head == g->food) { g->len += GROWTH; g->food = g->head + 3; }
  if (g->head >= 20) g->over = 1;
}
"""

HARNESS = """
#include <cv_R4.h>
#include <cv_R7.h>
int nondet_int(void);
typedef struct { int len, head, food, over; } Game;
void step(Game *g);
int main(void) {
  Game g; g.len = 1; g.head = nondet_int(); g.food = nondet_int(); g.over = 0;
  __CPROVER_assume(0 <= g.head && g.head < 20 && g.head < g.food && g.food < 20);
  for (int i = 0; i <= CV_R4_STEPS; i++) {
    Game before = g;
    int last = i == CV_R4_STEPS;
    if (!last) step(&g);
    cv_R4_state a = CV_R4_INIT; a.k0 = !last && g.head == before.food && !before.over; a.k1 = before.len;
    cv_R4_observe(&a);
    PRUNE
    cv_R7_state b = CV_R7_INIT; b.k0 = before.over;
    cv_R7_observe(&b);
  }
  CHECK
  return 0;
}
"""


def _game(tmp_path, growth=1, prune="", check="cv_R4_check(); cv_R7_check();", unwind=12, prefix=""):
    w, align = tmp_path / "w", tmp_path / "align"
    for d in (w, align):
        d.mkdir(parents=True, exist_ok=True)
    (w / "game.c").write_text(GAME.replace("GROWTH", str(growth)), encoding="utf-8")
    (align / "h.c").write_text(prefix + HARNESS.replace("PRUNE", prune).replace("CHECK", check), encoding="utf-8")
    d = snake_decls()
    props = {}
    for pid, s in {
        "R4": "只要 蛇 吃到 食物，下一步 蛇的长度 等于 蛇的长度 加 1",
        "R7": "一旦 游戏 结束，此后一直 游戏 结束",
    }.items():
        props[pid] = {"id": pid, "tree": G.parse_sentence(s, d)[0], "formula": s}
    out = {}
    cache: dict = {}
    for pid in props:
        check_ = {"proposition": pid, "sources": ["$CV_ALIGN/h.c", "game.c"], "steps": 8, "unwind": unwind}
        out[pid] = V.run(check_, props, {"蛇", "食物", "游戏"}, w, align, {}, RUN.run_command, 120, cache)
    return out


@pytest.mark.skipif(CBMC is None, reason="needs cbmc")
def test_cbmc_decides_for_every_input(tmp_path):
    """细则 11.11 执行与判定: correct code is presumed true for all inputs within the steps; growing by two is a
    definite violation whose counterexample the checker of 细则 13.2 confirms."""
    good = _game(tmp_path / "a")
    assert good["R4"]["result"] == M.PRESUMED_TRUE and good["R4"]["triggered"] is True
    assert "8 步以内" in good["R4"]["scope"] and "0 <= g.head" in good["R4"]["scope"]
    bad = _game(tmp_path / "b", growth=2)
    assert bad["R4"]["result"] == M.VIOLATED and bad["R7"]["result"] == M.PRESUMED_TRUE
    lengths = [s["值"]["长度(蛇)"] for s in bad["R4"]["counterexample"]["last_states"]]
    assert lengths[-1] - lengths[-2] == 2


@pytest.mark.skipif(CBMC is None, reason="needs cbmc")
def test_harness_cannot_hide_or_skip_the_check(tmp_path):
    """细则 11.11 13.7 辅助性质: pruning after observing is named as untested; a check never called, a check
    called before the end, and too small an unwinding are alignment failures."""
    pruned = _game(tmp_path / "a", growth=2, prune="__CPROVER_assume(!a.k0);")
    # Pruning the future of an eating step leaves no execution where the antecedent was followed up:
    # the check passes only as untested (细则 13.7), which the report names.
    assert pruned["R4"]["result"] == M.PRESUMED_TRUE and pruned["R4"]["triggered"] is False
    never = _game(tmp_path / "b", check="")
    assert never["R4"]["result"] == M.ALIGN_FAILED and "不可达" in never["R4"]["reason"]
    early = _game(tmp_path / "c", prune="cv_R7_check();", check="cv_R4_check();")
    assert early["R7"]["result"] == M.ALIGN_FAILED and "之后又记录" in early["R7"]["reason"]
    short = _game(tmp_path / "d", unwind=3)
    assert short["R4"]["result"] == M.ALIGN_FAILED and "展开不足" in short["R4"]["reason"]


@pytest.mark.skipif(CBMC is None, reason="needs cbmc")
def test_a_guard_or_property_cannot_be_forged(tmp_path):
    """细则 11.11 a harness that pre-defines an include guard or asserts a property under the same words
    cannot stand in for the generated header: both carry a fresh nonce."""
    forged = '#define CV_R4_H\n#define CV_R7_H\nstatic void fake(void) { __CPROVER_assert(1, "cv R4 主性质"); }\n'
    out = _game(tmp_path, growth=2, prune="fake();", check="cv_R4_check(); cv_R7_check();", prefix=forged)
    assert out["R4"]["result"] == M.VIOLATED


def test_stub_has_the_same_names_and_no_verdict():
    """细则 11.11 11.7 the header the agent works with compiles the same harness and decides nothing."""
    tree = G.parse_sentence("只要 蛇 吃到 食物，下一步 蛇的长度 等于 蛇的长度 加 1", snake_decls())[0]
    text = V.stub("R4", tree, 8)
    for name in ("cv_R4_state", "cv_R4_observe", "cv_R4_check", "CV_R4_STEPS", "k0", "k1"):
        assert name in text
    assert "__CPROVER_assert" not in text


def test_sources_must_stay_inside(tmp_path):
    """细则 11.11 sources: `$CV_ALIGN/` or relative to W, never `..` or absolute."""
    (tmp_path / "w").mkdir()
    (tmp_path / "w" / "a.c").write_text("", encoding="utf-8")
    (tmp_path / "al").mkdir()
    (tmp_path / "al" / "h.c").write_text("", encoding="utf-8")
    assert V.resolve("a.c", tmp_path / "w", tmp_path / "al") == tmp_path / "w" / "a.c"
    assert V.resolve("$CV_ALIGN/h.c", tmp_path / "w", tmp_path / "al") == tmp_path / "al" / "h.c"
    for bad in ("../w/a.c", "/etc/passwd", "$CV_ALIGN/../w/a.c", "missing.c"):
        assert V.resolve(bad, tmp_path / "w", tmp_path / "al") is None, bad
    assert json.dumps(
        V.keys(G.parse_sentence("一旦 游戏 结束，此后一直 游戏 结束", snake_decls())[0]), ensure_ascii=False
    )


def _props():
    d = snake_decls()
    out = {}
    for pid, s in {
        "R4": "只要 蛇 吃到 食物，下一步 蛇的长度 等于 蛇的长度 加 1",
        "R7": "一旦 游戏 结束，此后一直 游戏 结束",
        "R8": "只要 游戏 没有 结束，最终 蛇 吃到 食物",
    }.items():
        tree = G.parse_sentence(s, d)[0]
        out[pid] = {"id": pid, "tree": tree, "formula": F.to_text(tree), "class": F.classify(tree)}
    return out


def test_verify_checks_in_checks_json(tmp_path):
    """细则 11.2 11.5 11.6 11.11 a verify check covers a temporal proposition; it must name W's code, fit the
    fragment and stay within the limits; files named cv_*.h in align/ are refused."""
    import cv_align as A

    limits = {"check_timeout": 120, "max_runs": 200, "verify_timeout": 300, "verify_max_steps": 50}
    good = {"proposition": "R4", "kind": "verify", "sources": ["$CV_ALIGN/h.c", "game.c"], "steps": 8, "unwind": 12}
    bad = [
        {**good, "proposition": "R8"},  # liveness
        {**good, "sources": ["$CV_ALIGN/h.c"]},  # a model, not the code
        {**good, "sources": ["$CV_ALIGN/../h.c", "game.c"]},
        {**good, "steps": 51},
        {**good, "unwind": 0},
        {**good, "timeout": 301},
        {**good, "unwindset": {"game_step": 3}},  # not a loop number
        {**good, "unwindset": {"game_step.0": 0}},
    ]
    tight = {**good, "proposition": "R7", "unwindset": {"game_step.0": 9, "main.0": 7}}
    out = A.validate({"checks": [good, *bad, tight]}, _props(), limits, {"蛇", "食物", "游戏"})
    assert out["checks"][1]["unwindset"] == {"game_step.0": 9, "main.0": 7}
    assert "--unwindset game_step.0:9,main.0:7" in V.command(
        "cbmc", [], Path("i"), 3, Path("o"), Path("w"), out["checks"][1]["unwindset"]
    )
    assert len(out["format_errors"]) == len(bad)
    assert "R4" not in out["align_failures"] and "R7" not in out["align_failures"]
    assert [c["kind"] for c in out["checks"]] == ["verify", "verify"]
    (tmp_path / "cv_R4.h").write_text("", encoding="utf-8")
    assert "cv_*.h" in A.load(tmp_path)[1]


@pytest.mark.skipif(CBMC is None, reason="needs cbmc")
def test_verify_in_the_checker_and_the_report(tmp_path):
    """细则 11.11 13.3 14.1 the checker runs verify checks once per harness; the report names the scope and the
    verifier's counterexample, and counts the propositions a verifier decided."""
    import cv_report as REPORT
    import cv_state as S
    from test_align_run import LIMITS

    _game(tmp_path, growth=2)  # writes w/game.c and align/h.c
    run = tmp_path / "run"
    (run / "align").mkdir(parents=True)
    for f in (tmp_path / "align").iterdir():
        shutil.copy2(f, run / "align" / f.name)
    props = {k: v for k, v in _props().items() if k != "R8"}
    checks = [
        {
            "index": i,
            "proposition": pid,
            "form": "有界模型检查",
            "kind": "verify",
            "command": "",
            "runs": 1,
            "sources": ["$CV_ALIGN/h.c", "game.c"],
            "steps": 8,
            "unwind": 12,
            "timeout": 120,
        }
        for i, pid in enumerate(props)
    ]
    frozen = {"bindings": {}, "checks": checks, "align_failures": {}}
    results = RUN.run_checks(
        run, tmp_path / "w", props, frozen, {"蛇", "食物", "游戏"}, {**LIMITS, "verify_engine": "cbmc"}
    )
    by = results["propositions"]
    assert by["R4"]["result"] == M.VIOLATED and by["R7"]["result"] == M.PRESUMED_TRUE
    assert any(n.startswith("验证：8 步以内") for n in by["R7"]["notes"])
    accepted = [
        {**p, "source": "推理", "basis": "〔不变式〕x", "sentence": p["formula"], "problems": []}
        for p in props.values()
    ]
    S.write_json(run / "propositions.json", {"propositions": accepted})
    S.write_json(run / "results.json", results)
    S.write_json(run / "align" / "checks.frozen.json", frozen)
    text = REPORT.render(REPORT.build(run, {}, {"max_discard_ratio": 0.5}))
    assert "反例（cbmc）" in text and "由验证器在给定步数内考察了全部输入的命题 2 条" in text
