"""Check the plugin against STANDARDS.md and write CONFORMANCE.md.

    python3 scripts/check_standards.py [--no-tests]

Every README 细则 needs a passing test whose docstring starts with its number, or a static check
here; document items (D-) pass when their tests and the 细则 they map to pass; iCode (I-) and
project (P-) rules are checked statically, with iCode's own validators when iCode is installed.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / "cross-validate"
PASS, FAIL, SKIP, MANUAL = "通过", "未通过", "跳过", "人工"
BASE_COMMIT = "5cef5be"  # the last commit of the previous design; the rebuild starts after it
MANUAL_RULES = {"9.4": "“一条命题只说一件事”无法机械判断，只在提示中要求（README 细则 9.4、第 18 节）"}
DEVIATIONS = {"17.1": ["6.1"], "17.2": ["8.9"], "17.3": ["10.3"], "17.4": ["11.5"], "17.5": ["16.1", "16.6"],
              "17.6": ["15.4", "6.3"], "17.7": ["7.1"], "17.8": ["14.2"]}  # fmt: skip


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------
# Tests and their tags
# ---------------------------------------------------------------------------------------------------


def test_tags() -> dict[str, list[str]]:
    """Test function -> standard ids named at the start of its docstring."""
    tags: dict[str, list[str]] = {}
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for node in ast.parse(read(path)).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                doc = ast.get_docstring(node) or ""
                head = re.match(r"((?:(?:细则 )?(?:\d+\.\d+|[DIP]-[A-Z0-9-]+)\s+)+)", doc + " ")
                ids = re.findall(r"\d+\.\d+|[DIP]-[A-Z0-9-]*\d", head.group(1)) if head else []
                tags[f"{path.stem}::{node.name}"] = ids
    return tags


def run_tests() -> tuple[dict[str, bool], str]:
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "junit.xml"
        out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}", "tests"],
                             cwd=ROOT, capture_output=True, text=True, check=False)  # fmt: skip
        results: dict[str, bool] = {}
        if xml.exists():
            for case in ET.parse(xml).iter("testcase"):  # noqa: S314 -- pytest's own report, written just now
                key = f"{case.get('classname', '').split('.')[-1]}::{case.get('name', '').split('[')[0]}"
                ok = case.find("failure") is None and case.find("error") is None
                results[key] = results.get(key, True) and ok
        summary = (out.stdout.strip().splitlines() or ["(no output)"])[-1]
        return results, summary


# ---------------------------------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------------------------------


def stdlib_only(paths: list[Path], local: set[str], extra: set[str] = frozenset()) -> list[str]:
    bad = []
    for path in paths:
        for node in ast.walk(ast.parse(read(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    bad.append(f"{path.name}: 相对导入")
                names = [(node.module or "").split(".")[0]]
            allowed = sys.stdlib_module_names | local | extra | {"__future__"}
            bad.extend(f"{path.name}: {n}" for n in names if n not in allowed)
    return bad


def yaml_scalar(text: str, key: str) -> str | None:
    m = re.search(rf"^{key}:\s*(.*)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"') if m else None


def yaml_list(text: str, key: str) -> list[str]:
    m = re.search(rf"^\s*{key}:\s*(\[[^\]]*\]|\n(?:\s+- .*\n?)+)", text, re.MULTILINE)
    if not m:
        return []
    block = m.group(1)
    if block.startswith("["):
        return [x.strip() for x in block[1:-1].split(",") if x.strip()]
    return [line.split("- ", 1)[1].strip() for line in block.splitlines() if "- " in line]


def static_checks() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    graph = read(WF / "cross-validate.py")
    readme = read(ROOT / "README.md")
    config = json.loads(read(WF / "config.json"))

    # 细则 without a behavioural test
    compose = [p.name for p in ROOT.rglob("*.py") if re.search(r"compos|依赖.?保证|前提推断", p.name)]
    out["1.1"] = (
        PASS if not compose and ".loop(" not in graph else FAIL,
        "没有组合验证的模块" + (f"：{compose}" if compose else ""),
    )
    out["1.2"] = (PASS if ".loop(" not in graph and "instructions_suffix" not in graph else FAIL,
                  "工作流没有回到主 agent 的循环，也不向主 agent 追加指令")  # fmt: skip
    out["1.3"] = (
        PASS if "别的机制" in readme and (ROOT / "cross-validate" / "cv_ledger.py").exists() else FAIL,
        "需求积累在 cv_ledger.py 与 cv_cli.py",
    )
    out["1.4"] = (
        PASS if '["sh", "-c", command]' in read(WF / "cv_run.py") else FAIL,
        "检查一律以 sh -c 执行（cv_run.run_command）",
    )
    parts = [WF / "cross-validate.py", ROOT / "agents" / "cv-subtask.yaml", ROOT / "agents" / "cv-align.yaml",
             ROOT / "hooks" / "hooks.yaml", ROOT / "cv"]  # fmt: skip
    cli = read(ROOT / "cv_cli.py")
    subcommands = all(f'"{c}"' in cli for c in ("install", "init", "new", "check", "status", "uninstall"))
    out["2.1"] = (
        PASS if all(p.exists() for p in parts) and subcommands else FAIL,
        "工作流、两个 agent 配置、hooks、cv 的六个子命令齐全",
    )
    models = [k for k in ("main_model", "subtask_model", "align_model") if k in config]
    out["2.2"] = (PASS if len(models) == 3 else FAIL, f"config.json 有 {', '.join(models)}")
    timeouts = (
        "timeout=timeout" in graph
        and 'CONFIG["agent_timeout"]' in graph
        and 'wf.python("run_checks", run_checks, timeout=None)' in graph
    )
    out["4.7"] = (
        PASS if timeouts and "retry=" not in graph else FAIL,
        "agent 节点用 config 的超时，run_checks 无超时，不设自动重试",
    )
    return out


def icode_checks(home: Path) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    graph_path = WF / "cross-validate.py"
    tree = ast.parse(read(graph_path))
    top_ok = all(
        isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.Expr)) for n in tree.body[1:]
    )
    has_wf = any(
        isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "workflow" for t in n.targets) for n in tree.body
    )
    out["I-WF-1"] = (
        PASS if top_ok and has_wf else FAIL,
        "cross-validate/cross-validate.py 顶层只有导入、函数定义、赋值和构图调用，并定义 workflow",
    )
    clash = sorted(p.stem for p in WF.glob("*.py") if p.stem in sys.stdlib_module_names)
    rel = stdlib_only(list(WF.glob("*.py")), {p.stem for p in WF.glob("*.py")}, {"chrys"})
    reads = "HERE = Path(__file__).resolve().parent" in read(WF / "cv_flow.py")
    out["I-WF-2"] = (
        PASS if not clash and not rel and reads else FAIL,
        f"与标准库同名：{clash or '无'}；相对导入或第三方导入：{rel or '无'}",
    )
    icode = shutil.which("icode")
    if icode:
        v = subprocess.run(
            [icode, "workflow", "validate", str(WF), "--json"], capture_output=True, text=True, check=False
        )
        try:
            report = json.loads(v.stdout)
            errors = [d for d in report["diagnostics"] if d["severity"] == "error" and d["stage"] != "bindings"]
            out["I-WF-5"] = (
                PASS if not errors else FAIL,
                f"icode workflow validate：{report['status']}；bindings 之外的错误 {len(errors)} 个",
            )
        except (json.JSONDecodeError, KeyError):
            out["I-WF-5"] = (FAIL, (v.stdout + v.stderr).strip()[-300:])
    else:
        out["I-WF-5"] = (SKIP, "本机没有 icode 命令")
    subtask, align = read(ROOT / "agents" / "cv-subtask.yaml"), read(ROOT / "agents" / "cv-align.yaml")
    fields = all(yaml_scalar(t, k) for t in (subtask, align) for k in ("name", "display_name", "description")) and all(
        re.search(r"^instructions:", t, re.MULTILINE) and yaml_scalar(t, "sub_agent_only") == "true"
        for t in (subtask, align)
    )
    out["I-AG-1"] = (
        PASS if fields else FAIL,
        "两个配置都有 name、display_name、description、instructions，sub_agent_only: true",
    )
    no_tools = re.search(r"builtins:\s*\[\]", subtask) and "auto_load_user_agents_skills: false" in subtask
    no_tools = no_tools and "auto_load_cwd_agents_skills: false" in subtask and "memory" not in subtask
    out["I-AG-2"] = (PASS if no_tools else FAIL, "cv-subtask：builtins 为空，skills 不自动加载，没有 memory")
    want = ["filesystem.read", "filesystem.write", "search", "shell"]
    out["I-AG-3"] = (
        PASS if yaml_list(align, "builtins") == want else FAIL,
        f"cv-align builtins：{yaml_list(align, 'builtins')}",
    )
    hooks = json.loads(read(ROOT / "hooks" / "hooks.json"))
    events = {"session_start", "session_restored", "session_end", "before_turn", "after_turn", "user_prompt_submit",
              "user_interrupt", "before_tool_call", "after_tool_call", "tool_error"}  # fmt: skip
    problems = []
    ids = [h["id"] for h in hooks["hooks"]]
    if hooks.get("version") != 1 or len(set(ids)) != len(ids) or not all(i.startswith("cv-") for i in ids):
        problems.append("version 或 id")
    for h in hooks["hooks"]:
        ex = h.get("execution", {})
        if h["event"] not in events or ex.get("mode") not in ("blocking", "async", "fire_and_forget"):
            problems.append(f"{h['id']}: 事件或模式")
        if ex.get("detach") and ex.get("mode") != "fire_and_forget":
            problems.append(f"{h['id']}: detach")
        if h["event"] == "before_tool_call" and not h.get("match", {}).get("profile"):
            problems.append(f"{h['id']}: 缺少 match.profile")
    yaml_ids = re.findall(r"- id: (\S+)", read(ROOT / "hooks" / "hooks.yaml"))
    if yaml_ids != ids:
        problems.append("hooks.yaml 与 hooks.json 的条目不一致")
    out["I-HK-1"] = (PASS if not problems else FAIL, "；".join(problems) or f"{len(ids)} 个 hook：{'、'.join(ids)}")
    cli = read(ROOT / "cv_cli.py")
    io_ok = 'os.environ["CHRYS_HOOK_PAYLOAD_FILE"]' in cli and 'os.environ.get("CHRYS_HOOK_RESULT")' in cli
    out["I-HK-2"] = (
        PASS if io_ok else FAIL,
        "cv_cli.hook 从 CHRYS_HOOK_PAYLOAD_FILE 读输入，向 CHRYS_HOOK_RESULT 写决定",
    )
    if icode:
        sample = home / "hooks.yaml"
        sample.write_text(
            read(ROOT / "hooks" / "hooks.yaml")
            .replace("{{PYTHON}}", sys.executable)
            .replace("{{HOOK}}", str(ROOT / "hooks" / "cv_hook.py")),
            encoding="utf-8",
        )
        v = subprocess.run([icode, "hook", "validate", str(sample)], capture_output=True, text=True, check=False)
        out["I-HK-3"] = (
            PASS if v.returncode == 0 else FAIL,
            (v.stdout + v.stderr).strip().splitlines()[-1] if (v.stdout + v.stderr).strip() else "",
        )
    else:
        out["I-HK-3"] = (SKIP, "本机没有 icode 命令")
    return out


def project_checks(tests_summary: str | None, tests_ok: bool | None) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    code = [*WF.glob("*.py"), ROOT / "cv_cli.py", ROOT / "hooks" / "cv_hook.py"]
    local = {p.stem for p in WF.glob("*.py")} | {"cv_cli"}
    bad = stdlib_only([p for p in code if p.name != "cross-validate.py"], local) + stdlib_only(
        [WF / "cross-validate.py"], local, {"chrys"}
    )
    out["P-1"] = (PASS if not bad else FAIL, "只导入标准库" if not bad else "；".join(bad))
    target = re.search(r'target-version = "(py\d+)"', read(ROOT / "pyproject.toml"))
    out["P-2"] = (
        PASS if target and target.group(1) == "py310" else FAIL,
        "ruff target-version = py310，workflow 声明 requires-python >=3.10",
    )
    ruff = shutil.which("ruff") or str(ROOT / ".venv" / "bin" / "ruff")
    try:
        lint = subprocess.run([ruff, "check", "."], cwd=ROOT, capture_output=True, text=True, check=False)
        fmt = subprocess.run([ruff, "format", "--check", "."], cwd=ROOT, capture_output=True, text=True, check=False)
        out["P-3"] = (
            PASS if lint.returncode == 0 and fmt.returncode == 0 else FAIL,
            f"ruff check：{lint.stdout.strip().splitlines()[-1]}；ruff format：{fmt.stdout.strip().splitlines()[-1]}",
        )
    except OSError:
        out["P-3"] = (SKIP, "没有 ruff")
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.split()
    files = [ROOT / f for f in tracked if (ROOT / f).is_file()] or [
        p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts
    ]
    icode_src = [
        str(p.relative_to(ROOT))
        for p in files
        if "Huawei Technologies" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    plugin = [
        p
        for d in ("cross-validate", "agents", "hooks")
        for p in (ROOT / d).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    size = sum(p.stat().st_size for p in [*plugin, ROOT / "cv", ROOT / "cv_cli.py"])
    out["P-4"] = (
        PASS if not icode_src and size <= 400 * 1024 else FAIL,
        f"插件本体 {size / 1024:.0f} KB；含 iCode 版权声明的文件：{icode_src or '无'}",
    )
    if tests_ok is None:
        out["P-5"] = (SKIP, "以 --no-tests 运行")
    else:
        out["P-5"] = (PASS if tests_ok else FAIL, f"pytest：{tests_summary}")
    log = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x01", f"{BASE_COMMIT}..HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    commits = [c.strip() for c in log.stdout.split("\x01") if c.strip()]
    bad_commits = []
    for c in commits:
        sha, body = c.split("\x00", 1)
        subject = body.splitlines()[0]
        if not re.match(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?!?: \S", subject):
            bad_commits.append(f"{sha[:7]} 标题")
        if "Assisted-by: Claude-Opus4.6" not in body or "Claude-Session: https://claude.ai/code/session_" not in body:
            bad_commits.append(f"{sha[:7]} 结尾两行")
    out["P-6"] = (
        PASS if not bad_commits else FAIL,
        f"重建以来 {len(commits)} 个提交" + (f"；问题：{'、'.join(bad_commits)}" if bad_commits else ""),
    )
    secret = re.compile(
        r"github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    )
    leaks = [str(p.relative_to(ROOT)) for p in files if secret.search(p.read_text(encoding="utf-8", errors="ignore"))]
    out["P-7"] = (PASS if not leaks else FAIL, f"含令牌或密钥的文件：{leaks or '无'}")
    return out


# ---------------------------------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------------------------------


def readme_rules() -> list[str]:
    return re.findall(r"^细则 (\d+\.\d+)", read(ROOT / "README.md"), re.MULTILINE)


def doc_items() -> list[tuple[str, str, list[str]]]:
    rows = re.findall(r"^\| (D-\d+) \| [^|]+ \| ([^|]+) \| ([^|]+) \|$", read(ROOT / "STANDARDS.md"), re.MULTILINE)
    out = []
    for did, what, rules in rows:
        out.append((did, what.strip(), re.findall(r"\d+\.\d+", rules)))
    return out


def main() -> int:
    no_tests = "--no-tests" in sys.argv
    tags = test_tags()
    results, summary = ({}, None) if no_tests else run_tests()
    by_id: dict[str, list[str]] = {}
    for test, ids in tags.items():
        for i in ids:
            by_id.setdefault(i, []).append(test)
    static = static_checks()

    def tested(sid: str) -> tuple[str, str] | None:
        names = by_id.get(sid, [])
        if not names:
            return None
        if no_tests:
            return SKIP, f"{len(names)} 个测试（未运行）"
        failed = [n for n in names if not results.get(n, False)]
        return (FAIL if failed else PASS), (
            f"未通过：{'、'.join(failed)}"
            if failed
            else f"{len(names)} 个测试：{'、'.join(n.split('::')[1] for n in names[:4])}{' 等' if len(names) > 4 else ''}"
        )

    rules: dict[str, tuple[str, str]] = {}
    for rid in readme_rules():
        if rid in MANUAL_RULES:
            rules[rid] = (MANUAL, MANUAL_RULES[rid])
            continue
        if rid in DEVIATIONS:
            continue
        got = [r for r in (tested(rid), static.get(rid)) if r]
        if not got:
            rules[rid] = (FAIL, "没有测试，也没有静态核验")
        elif any(r[0] == FAIL for r in got):
            rules[rid] = (FAIL, "；".join(r[1] for r in got if r[0] == FAIL))
        else:
            rules[rid] = (got[0][0], "；".join(r[1] for r in got))
    for rid, impl in DEVIATIONS.items():
        states = [rules.get(r, (FAIL, ""))[0] for r in impl]
        rules[rid] = (FAIL if FAIL in states else PASS, f"偏离说明，核验其实现细则 {'、'.join(impl)}")

    docs: dict[str, tuple[str, str]] = {}
    for did, what, mapped in doc_items():
        parts = [rules.get(r, (FAIL, ""))[0] for r in mapped]
        own = tested(did)
        if own:
            parts.append(own[0])
        state = FAIL if FAIL in parts else PASS
        docs[did] = (state, f"{what}：细则 {'、'.join(mapped)}" + (f"；{own[1]}" if own else ""))

    with tempfile.TemporaryDirectory() as tmp:
        icode = icode_checks(Path(tmp))
    for iid in ("I-WF-3", "I-WF-4"):
        icode[iid] = tested(iid) or (FAIL, "没有测试")
    icode["I-HK-2"] = (icode["I-HK-2"][0] if (tested("I-HK-2") or (PASS,))[0] == PASS else FAIL, icode["I-HK-2"][1])
    proj = project_checks(summary, None if no_tests else all(results.values()) and bool(results))

    sections = [
        ("D：文档要求", docs),
        ("R：README 细则", rules),
        ("I：iCode 扩展规范", dict(sorted(icode.items()))),
        ("P：工程规范", proj),
    ]
    lines = ["# 标准核验结果", "", "由 `python3 scripts/check_standards.py` 生成，标准见 `STANDARDS.md`。", ""]
    total = {PASS: 0, FAIL: 0, SKIP: 0, MANUAL: 0}
    for title, items in sections:
        lines += [f"## {title}", "", "| 编号 | 结果 | 依据 |", "| --- | --- | --- |"]
        keys = sorted(items, key=lambda k: [int(x) if x.isdigit() else x for x in re.split(r"[.-]", k)])
        for k in keys:
            state, why = items[k]
            total[state] += 1
            lines.append(f"| {k if title[0] != 'R' else '细则 ' + k} | {state} | {why.replace('|', '/')} |")
        lines.append("")
    lines.insert(3, f"合计：通过 {total[PASS]}，未通过 {total[FAIL]}，跳过 {total[SKIP]}，人工 {total[MANUAL]}。\n")
    (ROOT / "CONFORMANCE.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"通过 {total[PASS]}，未通过 {total[FAIL]}，跳过 {total[SKIP]}，人工 {total[MANUAL]}")
    for _, items in sections:
        for k, (state, why) in items.items():
            if state == FAIL:
                print(f"未通过 {k}：{why}")
    return 1 if total[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
