"""Install the cross-validation plugin into iCode.

    python scripts/install.py --project <dir>   # the workflow into <dir>/.chrys/workflows/cross-validate/
    python scripts/install.py --global          # the workflow into ~/.chrys/workflows/cross-validate/

Both also install the generator profile `cv-generator` into iCode's user agent directory
(~/.chrys/agents/, %APPDATA%\\chrys\\agents\\ on Windows), where iCode loads agent profiles from.
Files are copied, never linked: iCode skips a workflow folder that contains a symbolic link.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "cross-validate"
PROFILE = ROOT / "agents" / "cv-generator.yaml"


def chrys_home() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "chrys"
    return Path.home() / ".chrys"


def install(target_workflows: Path) -> list[Path]:
    destination = target_workflows / "cross-validate"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(PLUGIN, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    agents = chrys_home() / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    profile = agents / PROFILE.name
    shutil.copy2(PROFILE, profile)
    return [destination, profile]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--project", type=Path, help="install as a project workflow of this directory")
    where.add_argument("--global", dest="global_", action="store_true", help="install as a global workflow")
    args = parser.parse_args(argv)
    workflows = chrys_home() / "workflows" if args.global_ else args.project.resolve() / ".chrys" / "workflows"
    for path in install(workflows):
        print(f"installed {path}")
    print("restart iCode so that it loads the cv-generator profile; then: icode workflow run cross-validate --trust")
    return 0


if __name__ == "__main__":
    sys.exit(main())
