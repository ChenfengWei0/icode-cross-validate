"""Check the plugin against every standard in STANDARDS.md and write CONFORMANCE.md.

    python scripts/check_standards.py [--icode <iCode checkout>] [--no-e2e]

Each standard gets one automated check: a static inspection of the code, a test of the suite, or a
run of an official tool (iCode's own validators, ruff). With --icode the iCode-side checks run
against that checkout (workflow validation, agent loading, the end-to-end run); without it they
are reported as SKIP, never as PASS. Exit code 0 when nothing FAILs.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "cross-validate"
PROFILE = ROOT / "agents" / "cv-generator.yaml"
STANDARDS = ROOT / "STANDARDS.md"
REPORT = ROOT / "CONFORMANCE.md"
DOC = ROOT / "交叉验证与组合验证.md"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Result:
    status: str
    method: str
    evidence: str


class Checker:
    def __init__(self, icode: Path | None, e2e: bool) -> None:
        self.icode = icode
        self.e2e = e2e
        self.tests: dict[str, str] = {}
        self.validate: dict | None = None
        self.icode_env: dict[str, str] | None = None
        self._tmp = tempfile.TemporaryDirectory(prefix="cv-standards-")

    # -- shared evidence --------------------------------------------------------------------

    def run_tests(self) -> None:
        junit = Path(self._tmp.name) / "junit.xml"
        env = dict(os.environ)
        if self.icode and self.e2e:
            env["ICODE_SRC"] = str(self.icode)
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=3600,
        )  # fmt: skip
        for case in ET.parse(junit).getroot().iter("testcase"):  # noqa: S314 - our own junit file
            name = f"{Path(case.get('classname', '').replace('.', '/')).name}::{case.get('name')}"
            outcome = PASS
            if case.find("failure") is not None or case.find("error") is not None:
                outcome = FAIL
            elif case.find("skipped") is not None:
                outcome = SKIP
            self.tests[name] = outcome

    def tests_result(self, *names: str, method: str = "pytest") -> Result:
        found = {n: self.tests.get(n, "MISSING") for n in names}
        bad = {n: o for n, o in found.items() if o != PASS}
        if any(o in (FAIL, "MISSING") for o in bad.values()):
            return Result(FAIL, method, "; ".join(f"{n}: {o}" for n, o in bad.items()))
        if bad:
            return Result(SKIP, method, "; ".join(f"{n}: {o}" for n, o in bad.items()))
        return Result(PASS, method, ", ".join(f"`{n}`" for n in names))

    def icode_cmd(self, *args: str, cwd: Path) -> subprocess.CompletedProcess:
        if self.icode is None:
            raise RuntimeError("no iCode checkout given")
        argv = ["uv", "run", "--project", str(self.icode), "--python", "3.14", "icode", *args]
        return subprocess.run(
            argv, cwd=cwd, env=self.icode_env, capture_output=True, text=True, check=False, timeout=600
        )

    def prepare_icode(self) -> None:
        """An isolated HOME with the plugin installed and a placeholder model (validation calls no model)."""
        if not self.icode:
            return
        home = Path(self._tmp.name) / "home"
        project = Path(self._tmp.name) / "project"
        (home / ".chrys" / "models").mkdir(parents=True)
        project.mkdir()
        (home / ".chrys" / "models" / "0123456789abcdef0123456789abcdef.yaml").write_text(
            "id: 0123456789abcdef0123456789abcdef\nname: placeholder\nprovider: openai\n"
            "model_id: placeholder\nbase_url: http://127.0.0.1:9\napi_key: unused\n",
            encoding="utf-8",
        )
        self.icode_env = dict(os.environ, HOME=str(home), CHRYS_MODEL_PROFILE="placeholder")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "install.py"), "--project", str(project)],
            env=self.icode_env, check=True, capture_output=True,
        )  # fmt: skip
        self.icode_project = project
        proc = self.icode_cmd("workflow", "validate", ".chrys/workflows/cross-validate", "--json", cwd=project)
        try:
            self.validate = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.validate = {"status": "error", "stderr": proc.stderr[-2000:]}


# --- helpers for static checks ---------------------------------------------------------------


def py_files() -> list[Path]:
    return sorted(p for p in PLUGIN.rglob("*.py") if "__pycache__" not in p.parts)


def source(name: str) -> str:
    return (PLUGIN / name).read_text(encoding="utf-8")


def ok(cond: bool, method: str, evidence: str, failure: str = "") -> Result:
    return Result(PASS if cond else FAIL, method, evidence if cond else (failure or evidence))


def function_source(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
    return ""


def profile_text() -> str:
    return PROFILE.read_text(encoding="utf-8")


# --- the checks ------------------------------------------------------------------------------


def check_scope_1(c: Checker) -> Result:
    banned = re.compile(
        r"\b(def|class)\s+\w*(rely|guarantee|premise|abduc|assumption_gen|compose_modules)\w*", re.IGNORECASE
    )
    hits = [f"{p.name}:{m.group(0)}" for p in py_files() for m in banned.finditer(p.read_text(encoding="utf-8"))]
    contract_writers = [p.name for p in py_files() if '"contracts"' in p.read_text(encoding="utf-8")]
    readers = [
        p.name for p in py_files() if re.search(r"contracts.*(read_text|glob|load)", p.read_text(encoding="utf-8"))
    ]
    good = not hits and contract_writers == ["judge.py"] and not readers
    return ok(
        good,
        "static: no composition/premise-inference definitions; the contract store is only written (judge._write_contracts)",
        "no rely/guarantee/premise/abduction code; `.cv/contracts` written only by judge.py, never read",
        f"definitions={hits} writers={contract_writers} readers={readers}",
    )


def check_scope_2(c: Checker) -> Result:
    return c.tests_result("test_protocol::test_free_text_is_passed_through_untouched")


def check_scope_3(c: Checker) -> Result:
    headers_only = '(".h", ".hpp", ".hh")' in source("cv_protocol.py")
    test = c.tests_result("test_regressions::test_cpp_delivery_is_rejected")
    return ok(
        headers_only and test.status == PASS,
        "static + test",
        f"validate_interface accepts only headers; C++ delivery rejected: {test.evidence}",
    )


def check_comp_1(c: Checker) -> Result:
    builtins_empty = re.search(r"^tools:\n  builtins: \[\]\s*$", profile_text(), re.MULTILINE) is not None
    no_mcp = "mcp:" not in profile_text()
    prompt_fns = ("segment_prompt", "generation_prompt", "rewrite_prompt")
    leaks = [
        f
        for f in prompt_fns
        if re.search(r"manifest|read_implementation|impl_files", function_source(PLUGIN / "judge.py", f))
    ]
    test = c.tests_result("test_visibility::test_generator_prompts_never_contain_the_implementation")
    good = builtins_empty and no_mcp and not leaks and test.status == PASS
    return ok(
        good,
        "profile has no tools; prompt builders read no implementation; test with an implementation present",
        f"cv-generator: tools.builtins=[] and no MCP; {', '.join(prompt_fns)} read only items/headers; {test.evidence}",
        f"builtins_empty={builtins_empty} no_mcp={no_mcp} leaks={leaks} test={test.status}",
    )


def check_comp_2(c: Checker) -> Result:
    return c.tests_result(
        "test_visibility::test_source_a_reads_the_item_only_and_source_b_adds_the_examples",
        "test_visibility::test_same_model_and_no_examples_is_reported",
        "test_visibility::test_prompt_examples_do_not_suggest_a_reading_of_the_design_examples",
    )


def _no_model_calls(paths: list[Path]) -> list[str]:
    banned = ("urllib", "http.client", "requests", "openai", "anthropic", "socket", "chrys")
    hits = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                hits += [f"{path.name}:{n}" for n in names if n.split(".")[0] in banned or n in banned]
    return hits


def check_comp_3(c: Checker) -> Result:
    hits = _no_model_calls(sorted((PLUGIN / "checker").glob("*.py")))
    return ok(
        not hits,
        "static: checker/ imports no network or model client",
        "checker/ imports only stdlib and local modules",
        str(hits),
    )


def check_comp_4(c: Checker) -> Result:
    hits = _no_model_calls(
        [PLUGIN / "judge.py", PLUGIN / "cv_protocol.py", PLUGIN / "cv_store.py", PLUGIN / "cv_clang.py"]
    )
    return ok(
        not hits, "static: the judge imports no network or model client", "judge.py, cv_*.py: no model calls", str(hits)
    )


def _graph_edges() -> list[tuple[str, str]]:
    """Edges of the top-level graph, read from the workflow file's wf.edge/wf.join calls."""
    tree = ast.parse(source("cross-validate.py"))
    edges = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
        ):
            if node.func.value.id == "wf" and node.func.attr == "edge" and len(node.args) >= 2:
                edges.append((ast.unparse(node.args[0]), ast.unparse(node.args[1])))
            if node.func.value.id == "wf" and node.func.attr == "join":
                target = ast.unparse(node.args[1])
                edges.extend((ast.unparse(src), target) for src in node.args[0].elts)  # type: ignore[attr-defined]
    return edges


def check_comp_5(c: Checker) -> Result:
    edges = _graph_edges()
    good = ("n_interface", "plan_entry") in edges and ("plan_exit", "n_implement") in edges
    return ok(
        good,
        "static: graph order interface → plan → implement",
        "edges interface→plan_entry, plan_exit→implement",
        str(edges),
    )


def check_harn_1(c: Checker) -> Result:
    text = source("cross-validate.py")
    good = 'wf.loop(\n    "implement"' in text and "until=settled" in text and "return entry, judge_node" in text
    test = c.tests_result("test_icode_e2e::test_block_then_pass_through_icode", method="end-to-end icode workflow run")
    status = PASS if good and test.status == PASS else FAIL if not good or test.status == FAIL else SKIP
    return Result(
        status,
        "graph: loop `implement` exits at the judge; end-to-end run: block then pass",
        f"loop until=settled; {test.evidence}",
    )


def check_harn_2(c: Checker) -> Result:
    return check_comp_5(c)


def check_harn_3(c: Checker) -> Result:
    return c.tests_result(
        "test_discount::test_invalid_declaration_is_sent_back",
        "test_protocol::test_manifest_must_name_the_item_set_and_files",
    )


def tests_of(*names: str) -> Callable[[Checker], Result]:
    return lambda c: c.tests_result(*names)


def check_dev(phrase: str, test: str) -> Callable[[Checker], Result]:
    """A deviation from the design passes when it is tested and stated among the README's limitations."""

    def check(c: Checker) -> Result:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        limits = readme.split("## 局限", 1)[-1].split("\n## ", 1)[0]
        result = c.tests_result(test)
        if phrase not in limits:
            return Result(FAIL, "README 局限 + test", f"README 局限 does not state it ({phrase!r})")
        return Result(result.status, "README 局限 + test", f"stated in README 局限; {result.evidence}")

    return check


def check_alg(c: Checker) -> Result:
    narrow: set[int] = set()
    for path in py_files():
        for m in re.finditer(r"算法1 第(\d+)(?:[-~～](\d+))?行", path.read_text(encoding="utf-8")):
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            if b - a <= 3:
                narrow.update(range(a, b + 1))
    missing = sorted(set(range(1, 34)) - narrow)
    return ok(
        not missing,
        "static: every line 1-33 of 算法 1 is annotated in the code (ranges of at most 4 lines)",
        "lines 1-33 all annotated",
        f"missing {missing}",
    )


def check_int_1(c: Checker) -> Result:
    needed = [
        "judge.py",
        "prompts",
        *(f"checker/{m}.py" for m in ("selfcheck", "prove", "fuzz", "difftest", "run_cases")),
    ]
    missing = [n for n in needed if not (PLUGIN / n).exists()]
    return ok(
        not missing,
        "static: plugin layout",
        "cross-validate/{judge, checker/{selfcheck,prove,fuzz,difftest,run_cases}, prompts/}",
        str(missing),
    )


def check_int_2(c: Checker) -> Result:
    intake = function_source(PLUGIN / "judge.py", "intake")
    good = all(f'"{d}"' in intake for d in ("subtasks", "manifests", "contracts"))
    test = c.tests_result("test_visibility::test_frozen_hashes_are_recorded_outside_the_project")
    return ok(
        good and test.status == PASS,
        "static + test",
        f".cv/{{subtasks,manifests,contracts}} created by intake; {test.evidence}",
    )


def check_int_3(c: Checker) -> Result:
    plan_key = function_source(PLUGIN / "judge.py", "_plan_key")
    compare_key = function_source(PLUGIN / "judge.py", "_compare_key")
    good = all(k in plan_key for k in ('"text"', '"examples"', '"headers"', '"types"', '"generator"')) and all(
        k in compare_key for k in ('"delivered"', '"interface"', '"checker"', '"limits"')
    )
    test = c.tests_result("test_regressions::test_changed_callee_is_not_served_from_cache")
    good = good and test.status == PASS
    return ok(
        good,
        "static: cache keys",
        f"plan key = item text+examples, headers, types, generator+prompt version; compare key = artifact + every delivered file (superset of checked bodies and callees) + headers, checker version, limits; {test.evidence}",
    )


def check_int_4(c: Checker) -> Result:
    config = json.loads(source("config.json"))
    needed = ("retry_limit", "bounded_unwind", "sat_conflicts", "fuzz_iterations", "fuzz_seed", "min_strength",
              "require_disambiguation", "unknown_blocks")  # fmt: skip
    missing = [k for k in needed if k not in config]
    return ok(not missing, "static: config.json keys", ", ".join(needed), str(missing))


def check_lim(c: Checker) -> Result:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    phrases = (
        "两边会一起错",
        "未覆盖的部分不受检查",
        "加强不变量",
        "恰好落在某种合理读法上的误读会被放过",
        "关系性质（P3），仍是开放问题",
    )
    missing = [p for p in phrases if p not in readme]
    return ok(
        not missing, "static: README lists the design's limitations", "all five limitations of §3 局限", str(missing)
    )


def check_wf_1(c: Checker) -> Result:
    tree = ast.parse(source("cross-validate.py"))
    workflow_assigned = any(
        isinstance(n, (ast.Assign, ast.AnnAssign))
        and "workflow"
        in [t.id for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)]
        and "build()" in ast.unparse(n.value)
        for n in tree.body
    )
    side_effects = [
        ast.unparse(n)[:60]
        for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and not ast.unparse(n.value).startswith("wf.")
    ]
    entry_ok = (PLUGIN / f"{PLUGIN.name}.py").is_file()
    return ok(
        workflow_assigned and not side_effects and entry_ok,
        "static: entry file named after the folder; module-level `workflow = wf.build()`; top level only builds the graph",
        "cross-validate/cross-validate.py; `workflow: Workflow = wf.build()`",
        f"workflow={workflow_assigned} side_effects={side_effects} entry={entry_ok}",
    )


def check_wf_2(c: Checker) -> Result:
    stdlib = set(sys.stdlib_module_names)
    names = {p.stem for p in py_files()} | {p.name for p in PLUGIN.rglob("*") if p.is_dir()}
    shadow = sorted(n for n in names if n in stdlib)
    relative = [p.name for p in py_files() if re.search(r"^\s*from \.", p.read_text(encoding="utf-8"), re.MULTILINE)]
    links = [str(p) for p in PLUGIN.rglob("*") if p.is_symlink()]
    count = sum(1 for p in PLUGIN.rglob("*") if "__pycache__" not in p.parts)
    good = not shadow and not relative and not links and count <= 1000
    return ok(
        good,
        "static: names vs sys.stdlib_module_names; relative imports; symlinks; file count",
        f"{count} files, no stdlib names, no relative imports, no links",
        f"shadow={shadow} relative={relative} links={links} count={count}",
    )


def check_wf_3(c: Checker) -> Result:
    text = source("cross-validate.py")
    return ok(
        re.search(r"^# /// script\n# requires-python = \">=3\.\d+\"\n# ///", text, re.MULTILINE) is not None,
        "static: script metadata block",
        "requires-python declared",
    )


def check_wf_4(c: Checker) -> Result:
    if c.validate is None:
        return Result(SKIP, "icode workflow validate (load stage checks node signatures)", "no --icode")
    stages = {s["name"]: s["status"] for s in c.validate.get("stages", [])}
    return ok(
        stages.get("load") == "pass", "icode workflow validate: load stage (SDK checks callables)", f"stages={stages}"
    )


def check_wf_5(c: Checker) -> Result:
    tree = ast.parse(source("cross-validate.py"))
    feeding_agents = {
        "interface_prompt",
        "segment_prompt",
        "prompt_a",
        "prompt_b",
        "reprompt_a",
        "reprompt_b",
        "round_prompt",
    }
    bad = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name in feeding_agents
        and (n.returns is None or ast.unparse(n.returns) != "str")
    ]
    return ok(
        not bad, "static: every node in front of an agent returns `str`", ", ".join(sorted(feeding_agents)), str(bad)
    )


def check_wf_6(c: Checker) -> Result:
    if c.validate is None:
        return Result(SKIP, "icode workflow validate (graph stage)", "no --icode")
    codes = [d["code"] for d in c.validate.get("diagnostics", [])]
    return ok(
        "loop_exit_all_conditional" not in codes and c.validate.get("status") == "pass",
        "icode workflow validate: graph stage, no loop warnings",
        f"diagnostics={codes}",
    )


def check_wf_7(c: Checker) -> Result:
    hits = [p.name for p in py_files() if re.search(r"\bctx\.ask\(|\.ask\(", p.read_text(encoding="utf-8"))]
    return ok(
        not hits,
        "static: no ctx.ask",
        "the workflow never asks; a pause ends the run with the distinguishing input",
        str(hits),
    )


def check_wf_8(c: Checker) -> Result:
    tree = ast.parse(source("cross-validate.py"))
    python_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "python"
    ]  # fmt: skip
    retries = [ast.unparse(n)[:60] for n in python_calls if any(k.arg == "retry" for k in n.keywords)]
    heavy = {"_check", "_freeze", "_prepare", "verify", "decide"}
    untimed = [
        ast.unparse(n.args[0])
        for n in python_calls
        if any(h in ast.unparse(n.args[0]) for h in heavy) and not any(k.arg == "timeout" for k in n.keywords)
    ]
    return ok(
        not retries and not untimed,
        "static: checker-running python nodes set timeout; no Retry on python nodes",
        "check/freeze/prepare/verify/decide: timeout=None (each tool call has its own safety timeout)",
        f"retries={retries} untimed={untimed}",
    )


def check_wf_9(c: Checker) -> Result:
    if c.validate is None:
        return Result(SKIP, "icode workflow validate", "no --icode")
    wf = c.validate.get("workflow") or {}
    warnings = [d["code"] for d in c.validate.get("diagnostics", [])]
    return ok(
        c.validate.get("status") == "pass" and not warnings,
        "icode workflow validate --json",
        f"PASS · {wf.get('node_count')} nodes · {wf.get('edge_count')} edges · 0 diagnostics",
        json.dumps(c.validate, ensure_ascii=False)[:1500],
    )


def check_ag_1(c: Checker) -> Result:
    text = profile_text()
    keys = set(re.findall(r"^([a-z_]+):", text, re.MULTILINE))
    name = re.search(r"^name: (.+)$", text, re.MULTILINE)
    valid_name = bool(name) and re.fullmatch(r"[^/\\:*?\"<>|]+", name.group(1).strip()) is not None
    good = valid_name and "id" not in keys and {"display_name", "description", "instructions"} <= keys
    return ok(
        good,
        "static: profile fields",
        f"name={name.group(1) if name else None}; no id; display_name, description, instructions present",
        str(keys),
    )


def check_ag_2(c: Checker) -> Result:
    text = profile_text()
    good = "builtins: []" in text and "mcp" not in text and "script_extensions: []" in text
    good = good and "auto_load_user_agents_skills: false" in text and "auto_load_cwd_agents_skills: false" in text
    return ok(
        good,
        "static: generator profile has no built-in tools, no MCP, no skill scripts",
        "tools.builtins=[], skills.script_extensions=[], auto_load_* false",
    )


def check_ag_3(c: Checker) -> Result:
    if not c.icode:
        return Result(SKIP, "icode agent list --json", "no --icode")
    proc = c.icode_cmd("agent", "list", "--json", cwd=c.icode_project)
    return ok(
        "cv-generator" in proc.stdout,
        "icode agent list --json (isolated HOME)",
        "cv-generator loaded from ~/.chrys/agents",
        proc.stdout[-800:] + proc.stderr[-800:],
    )


def check_hk_1(c: Checker) -> Result:
    hooks = [str(p) for p in ROOT.rglob("hooks.y*ml") if ".git" not in p.parts]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    return ok(
        not hooks and "after_turn" in readme and "插件不使用 hooks" in readme,
        "static: no hook files; README explains why",
        "README §为什么是一个工作流",
        str(hooks),
    )


def check_sv_1(c: Checker) -> Result:
    conventions = source("prompts/conventions.md")
    runtime = source("checker/runtime/cv_fuzz_runtime.c")
    common = source("checker/common.py")
    good = all(s in conventions for s in ("__VERIFIER_nondet_int", "__VERIFIER_assume", "assert(")) and (
        "void __VERIFIER_assume(int cond)" in runtime and "__VERIFIER_nondet_" in common
    )
    return ok(
        good,
        "static: prompt conventions, checker prelude and random-testing runtime use the SV-COMP interface",
        "__VERIFIER_nondet_<type>, __VERIFIER_assume, assert",
    )


def check_cbmc_1(c: Checker) -> Result:
    text = source("checker/prove.py") + source("checker/common.py")
    good = "--unwinding-assertions" in text and "--base-case" in text and "--step-case" in text
    tests = c.tests_result(
        "test_ledger::test_correct_implementation_passes_with_proof",
        "test_discount::test_any_reading_passing_passes_and_records_the_reading",
    )
    return ok(
        good and tests.status == PASS,
        "static + tests",
        f"unwinding assertions and k-induction base/step cases; {tests.evidence}",
    )


def check_det_1(c: Checker) -> Result:
    sat = source("checker/runtime/sat.sh")
    good = "sat.max_conflicts" in sat
    tests = c.tests_result("test_discount::test_results_are_deterministic")
    return ok(
        good and tests.status == PASS, "static + test", f"z3 sat.max_conflicts; seeded random testing; {tests.evidence}"
    )


def check_py_1(c: Checker) -> Result:
    proc = subprocess.run(
        ["uvx", "ruff", "check", "--no-fix", "cross-validate", "tests", "scripts"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    fmt = subprocess.run(
        ["uvx", "ruff", "format", "--check", "cross-validate", "tests", "scripts"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    same_rules = True
    if c.icode:
        mine = re.search(r"select = \[(.*?)\]", (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.DOTALL)
        theirs = re.search(
            r"\[tool\.ruff\.lint\]\nselect = \[(.*?)\]",
            (c.icode / "pyproject.toml").read_text(encoding="utf-8"),
            re.DOTALL,
        )
        rules = lambda m: set(re.findall(r'"([A-Z0-9]+)"', m.group(1))) if m else set()  # noqa: E731
        same_rules = rules(mine) == rules(theirs)
    good = proc.returncode == 0 and fmt.returncode == 0 and same_rules
    return ok(
        good,
        "ruff check + ruff format --check with iCode's rule set",
        f"ruff: {proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else 'ok'}; format clean; rule set equal to iCode's: {same_rules if c.icode else 'not compared (no --icode)'}",
        proc.stdout[-1500:] + fmt.stdout[-500:],
    )


def check_py_2(c: Checker) -> Result:
    local = {p.stem for p in py_files()} | {"checker", "chrys"}
    stdlib = set(sys.stdlib_module_names)
    foreign = set()
    for path in py_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                foreign |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                foreign.add(node.module.split(".")[0])
    foreign -= stdlib | local | {"__future__"}
    return ok(
        not foreign,
        "static: imports of the plugin",
        "standard library and local modules only (plus chrys.workflows, supplied by iCode)",
        str(sorted(foreign)),
    )


def check_py_3(c: Checker) -> Result:
    failed = [n for n, o in c.tests.items() if o == FAIL]
    passed = sum(1 for o in c.tests.values() if o == PASS)
    skipped = sum(1 for o in c.tests.values() if o == SKIP)
    examples = any(n.startswith("test_ledger::") for n in c.tests) and any(
        n.startswith("test_discount::") for n in c.tests
    )
    return ok(
        not failed and examples and passed > 0,
        "pytest",
        f"{passed} passed, {skipped} skipped, 0 failed; ledger and discount examples covered",
        f"failed={failed}",
    )


def check_git_1(c: Checker) -> Result:
    proc = subprocess.run(["git", "log", "--format=%s"], cwd=ROOT, capture_output=True, text=True, check=False)
    subjects = [s for s in proc.stdout.splitlines() if s]
    if not subjects:
        return Result(SKIP, "git log", "no commits yet")
    pattern = re.compile(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([\w\-./]+\))?!?: .+")
    bad = [s for s in subjects if not pattern.match(s)]
    return ok(not bad, "git log subjects vs Conventional Commits 1.0.0", f"{len(subjects)} commits", str(bad))


def check_lic_1(c: Checker) -> Result:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    good = (
        "Apache License" in text
        and "Version 2.0" in text
        and 'license = "Apache-2.0"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return ok(good, "static: LICENSE and pyproject", "Apache-2.0")


CHECKS: list[tuple[str, Callable[[Checker], Result]]] = [
    ("D-SCOPE-1", check_scope_1),
    ("D-SCOPE-2", check_scope_2),
    ("D-SCOPE-3", check_scope_3),
    ("D-COMP-1", check_comp_1),
    ("D-COMP-2", check_comp_2),
    ("D-COMP-3", check_comp_3),
    ("D-COMP-4", check_comp_4),
    ("D-COMP-5", check_comp_5),
    ("D-HARN-1", check_harn_1),
    ("D-HARN-2", check_harn_2),
    ("D-HARN-3", check_harn_3),
    (
        "D-PROT-1",
        tests_of(
            "test_protocol::test_item_set_fields_and_default_id",
            "test_protocol::test_empty_item_set_passes_with_a_note",
        ),
    ),
    (
        "D-PROT-2",
        tests_of(
            "test_protocol::test_missing_interface_leaves_everything_uncovered",
            "test_discount::test_header_change_replans_the_affected_items",
        ),
    ),
    (
        "D-PROT-3",
        tests_of(
            "test_visibility::test_generator_prompts_never_contain_the_implementation",
        ),
    ),
    (
        "D-PROT-4",
        tests_of(
            "test_visibility::test_frozen_hashes_are_recorded_outside_the_project",
            "test_discount::test_tampered_artifact_is_rejected",
        ),
    ),
    (
        "D-PROT-5",
        tests_of(
            "test_discount::test_no_new_declaration_passes_without_checking",
            "test_discount::test_invalid_declaration_is_sent_back",
        ),
    ),
    (
        "D-PROT-6",
        tests_of(
            "test_discount::test_results_are_deterministic", "test_selfcheck::test_good_property_passes_all_four_checks"
        ),
    ),
    (
        "D-PROT-7",
        tests_of(
            "test_discount::test_code_matching_no_reading_is_blocked_with_counterexample_only",
            "test_discount::test_report_tells_first_pass_from_pass_after_modification",
            "test_discount::test_first_pass_is_reported_as_such",
            "test_forms::test_concrete_cases_run_directly",
        ),
    ),
    (
        "D-SIG-1",
        tests_of(
            "test_discount::test_any_reading_passing_passes_and_records_the_reading",
            "test_discount::test_code_matching_no_reading_is_blocked_with_counterexample_only",
            "test_discount::test_pause_when_disambiguation_is_required",
            "test_discount::test_retry_limit_rejects",
        ),
    ),
    (
        "D-SM-1",
        tests_of(
            "test_selfcheck::test_compile_error_is_rewritten_once_and_kept",
            "test_selfcheck::test_rewrite_that_still_fails_is_dropped",
        ),
    ),
    (
        "D-SM-2",
        tests_of(
            "test_protocol::test_empty_item_set_passes_with_a_note",
            "test_discount::test_tampered_artifact_is_rejected",
            "test_discount::test_header_change_replans_the_affected_items",
            "test_discount::test_retry_limit_rejects",
        ),
    ),
    (
        "D-FLOW-1",
        tests_of(
            "test_protocol::test_empty_item_set_passes_with_a_note",
            "test_discount::test_no_new_declaration_passes_without_checking",
        ),
    ),
    (
        "D-FLOW-2",
        tests_of(
            "test_selfcheck::test_segment_span_must_come_from_the_item",
            "test_selfcheck::test_uncovered_segment_keeps_the_generators_reason",
            "test_regressions::test_segment_without_functions_is_uncovered",
            "test_forms::test_reference_model_by_differential_testing",
            "test_forms::test_concrete_cases_run_directly",
        ),
    ),
    (
        "D-FLOW-3",
        tests_of(
            "test_ledger::test_plan_freezes_structure_and_invariant",
            "test_visibility::test_source_a_reads_the_item_only_and_source_b_adds_the_examples",
        ),
    ),
    (
        "D-FLOW-4",
        tests_of(
            "test_discount::test_two_readings_are_kept_with_a_distinguishing_input",
            "test_discount::test_example_removes_the_contradicting_reading",
            "test_discount::test_any_reading_passing_passes_and_records_the_reading",
        ),
    ),
    (
        "D-FLOW-5",
        tests_of(
            "test_selfcheck::test_good_property_passes_all_four_checks",
            "test_selfcheck::test_vacuous_property_is_discarded",
            "test_selfcheck::test_contradictory_premises_are_discarded",
            "test_selfcheck::test_missing_reach_branch_is_a_failure",
            "test_regressions::test_harness_must_assert_the_spec_it_defines",
            "test_regressions::test_inconclusive_self_check_is_not_a_pass",
            "test_forms::test_cases_need_a_constant_expectation",
            "test_forms::test_contradicting_requirements_are_marked_as_conflict",
            "test_regressions::test_equal_predicates_under_different_premises_stay_two_readings",
        ),
    ),
    (
        "D-FLOW-6",
        tests_of(
            "test_discount::test_tampered_artifact_is_rejected",
            "test_ledger::test_correct_implementation_passes_with_proof",
            "test_ledger::test_bypassing_the_payment_module_is_blocked",
            "test_forms::test_random_testing_finds_the_counterexample",
            "test_forms::test_reference_model_by_differential_testing",
            "test_forms::test_concrete_cases_run_directly",
            "test_discount::test_results_are_deterministic",
            "test_regressions::test_code_that_does_not_compile_is_blocked",
            "test_regressions::test_code_that_never_returns_is_blocked",
            "test_regressions::test_undefined_function_is_blocked",
            "test_regressions::test_write_through_a_cast_is_blocked",
        ),
    ),
    (
        "D-FLOW-7",
        tests_of(
            "test_discount::test_code_matching_no_reading_is_blocked_with_counterexample_only",
            "test_discount::test_retry_limit_rejects",
            "test_discount::test_pause_when_disambiguation_is_required",
            "test_forms::test_min_strength_turns_weak_passes_into_unknown",
            "test_forms::test_unknown_blocks_when_configured",
            "test_selfcheck::test_rewrite_that_still_fails_is_dropped",
            "test_visibility::test_feedback_and_contracts_on_pass",
        ),
    ),
    ("D-ALG-1", check_alg),
    ("D-INT-1", check_int_1),
    ("D-INT-2", check_int_2),
    ("D-INT-3", check_int_3),
    ("D-INT-4", check_int_4),
    ("D-LIM-1", check_lim),
    ("I-WF-1", check_wf_1),
    ("I-WF-2", check_wf_2),
    ("I-WF-3", check_wf_3),
    ("I-WF-4", check_wf_4),
    ("I-WF-5", check_wf_5),
    ("I-WF-6", check_wf_6),
    ("I-WF-7", check_wf_7),
    ("I-WF-8", check_wf_8),
    ("I-WF-9", check_wf_9),
    ("I-AG-1", check_ag_1),
    ("I-AG-2", check_ag_2),
    ("I-AG-3", check_ag_3),
    ("I-HK-1", check_hk_1),
    ("V-SV-1", check_sv_1),
    ("V-CBMC-1", check_cbmc_1),
    ("V-DET-1", check_det_1),
    ("P-PY-1", check_py_1),
    ("P-PY-2", check_py_2),
    ("P-PY-3", check_py_3),
    ("G-GIT-1", check_git_1),
    ("G-LIC-1", check_lic_1),
    ("X-DEV-1", check_dev("C++ 或 Objective-C", "test_regressions::test_cpp_delivery_is_rejected")),
    (
        "X-DEV-2",
        check_dev(
            "没有新的完成清单", "test_regressions::test_stopping_without_a_declaration_is_blocked_in_the_workflow"
        ),
    ),
    ("X-DEV-3", check_dev("先读取接口", "test_regressions::test_interface_declared_late_is_planned_before_comparison")),
    ("X-DEV-4", check_dev("只能通过指令要求主 agent 不读", "test_discount::test_tampered_artifact_is_rejected")),
]


def standard_ids() -> list[str]:
    return re.findall(r"^\| ([A-Z]-[A-Z]+-\d+) \|", STANDARDS.read_text(encoding="utf-8"), re.MULTILINE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--icode", type=Path, help="iCode checkout for the iCode-side checks")
    parser.add_argument("--no-e2e", action="store_true", help="skip the end-to-end icode workflow run")
    args = parser.parse_args(argv)
    checker = Checker(args.icode.resolve() if args.icode else None, e2e=not args.no_e2e)
    checker.prepare_icode()
    checker.run_tests()
    declared = standard_ids()
    implemented = [sid for sid, _ in CHECKS]
    rows = []
    for sid, fn in CHECKS:
        try:
            result = fn(checker)
        except Exception as exc:  # a broken check is a failed check, never a silent pass
            result = Result(FAIL, "check raised", repr(exc))
        rows.append((sid, result))
    unchecked = sorted(set(declared) - set(implemented))
    unknown = sorted(set(implemented) - set(declared))
    counts = {s: sum(1 for _, r in rows if r.status == s) for s in (PASS, FAIL, SKIP)}
    lines = [
        "# 标准符合性报告（CONFORMANCE）",
        "",
        "由 `scripts/check_standards.py` 生成，逐条对应 `STANDARDS.md`。",
        "",
        f"- 结果：{counts[PASS]} PASS · {counts[FAIL]} FAIL · {counts[SKIP]} SKIP（共 {len(rows)} 条）",
        f"- iCode 校验：{'基于 ' + str(checker.icode.name) + '（' + _icode_commit(checker.icode) + '）' if checker.icode else '未提供 --icode，相关条目为 SKIP'}",
        f"- 未被检查的标准：{unchecked or '无'}；清单外的检查：{unknown or '无'}",
        "",
        "| 编号 | 结果 | 检查方式 | 证据 |",
        "| --- | --- | --- | --- |",
    ]
    for sid, r in rows:
        evidence = r.evidence.replace("|", "\\|").replace("\n", " ")[:600]
        lines.append(f"| {sid} | {r.status} | {r.method.replace('|', '/')} | {evidence} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[4:7]))
    for sid, r in rows:
        if r.status != PASS:
            print(f"{r.status} {sid}: {r.evidence[:300]}")
    return 1 if counts[FAIL] or unchecked else 0


def _icode_commit(path: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=path, capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() or "unknown"


if __name__ == "__main__":
    sys.exit(main())
