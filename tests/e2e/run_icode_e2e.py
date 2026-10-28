"""Run the installed workflow through a real `icode workflow run`, with fake_llm.py as every model.

    python tests/e2e/run_icode_e2e.py <iCode checkout>

Everything happens under a temporary HOME: the plugin is installed with scripts/install.py, a model
profile points at the fake endpoint, and the run's JSON result is checked: the first completion
(wrong code) is blocked, the second passes, and the report says "修改后通过". Exit code 0 on success.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "discount"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(icode_src: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="cv-e2e-") as tmp:
        home, project = Path(tmp) / "home", Path(tmp) / "project"
        (home / ".chrys" / "models").mkdir(parents=True)
        (project / "src").mkdir(parents=True)
        port = _free_port()
        (home / ".chrys" / "models" / "fa4e0000000000000000000000000001.yaml").write_text(
            "id: fa4e0000000000000000000000000001\nname: fake\nprovider: openai\napi_style: chat_completions\n"
            f"model_id: fake-model\nbase_url: http://127.0.0.1:{port}/v1\napi_key: unused\nstream: false\n"
            "bypass_proxy: true\n",
            encoding="utf-8",
        )
        env = dict(
            os.environ, HOME=str(home), CHRYS_MODEL_PROFILE="fake", NO_PROXY="*", CV_STATE_DIR=str(Path(tmp) / "state")
        )
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "install.py"), "--project", str(project)], env=env, check=True
        )
        server = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("fake_llm.py")), str(port), str(EXAMPLE)]
        )
        try:
            time.sleep(0.5)
            requirements = (EXAMPLE / "requirements.json").read_text(encoding="utf-8")
            icode = ["uv", "run", "--project", str(icode_src), "--python", "3.14", "icode"]
            proc = subprocess.run(
                [*icode, "workflow", "run", "cross-validate", "--trust", "--json", "-q", "--timeout", "900",
                 "--input", requirements],
                cwd=project, env=env, capture_output=True, text=True, timeout=1200, check=False,
            )  # fmt: skip
        finally:
            server.terminate()
        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"ok": False, "error": f"no JSON result (exit {proc.returncode}): {proc.stderr[-3000:]}"}
        report_path = project / ".cv" / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        rounds = [r["signal"] for r in report.get("report", {}).get("rounds", [])]
        fn = report.get("report", {}).get("functions", {}).get("discount", {})
        ok = (
            proc.returncode == 0
            and result.get("outcome") == "completed"
            and report.get("signal") == "pass"
            and rounds == ["block", "pass"]
            and fn.get("pass") == "修改后通过"
        )
        return {
            "ok": ok,
            "exit": proc.returncode,
            "outcome": result.get("outcome"),
            "signal": report.get("signal"),
            "rounds": rounds,
            "discount": fn,
            "output": (result.get("outputs") or [{}])[0].get("text", "")[:4000],
            "stderr": proc.stderr[-2000:] if not ok else "",
        }


if __name__ == "__main__":
    outcome = run(Path(sys.argv[1]).resolve())
    sys.stdout.write(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
    sys.exit(0 if outcome["ok"] else 1)
