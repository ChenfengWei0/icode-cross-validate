"""The state directory outside the project, run directories and digests (README 第 3 节, 细则 5.2, 5.4)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path

WORKFLOW_ID = "cross-validate"


def chrys_home() -> Path:
    """iCode's configuration directory; CV_CHRYS_HOME overrides it for tests."""
    return Path(os.environ.get("CV_CHRYS_HOME") or Path.home() / ".chrys")


def cv_home() -> Path:
    return chrys_home() / "cross-validate"


def global_workflow_dir() -> Path:
    return chrys_home() / "workflows" / WORKFLOW_ID


def ensure_private(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def project_key(project: str | Path) -> str:
    real = os.path.realpath(project)
    return hashlib.sha256(real.encode()).hexdigest()[:16] + "-" + (Path(real).name or "root")


def project_dir(project: str | Path) -> Path:
    """细则 3.1: everything about a project lives here, never in the project itself."""
    base = ensure_private(cv_home())
    ensure_private(base / "projects")
    return base / "projects" / project_key(project)


def new_run_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


def create_run(project: str | Path) -> Path:
    runs = ensure_private(project_dir(project)) / "runs"
    ensure_private(runs)
    while True:
        run = runs / new_run_id()
        try:
            run.mkdir(mode=0o700)
            return run
        except FileExistsError:
            continue


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _tmp(path: Path) -> Path:
    return path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def write_json(path: Path, data) -> None:
    path = Path(path)
    tmp = _tmp(path)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    tmp = _tmp(path)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digests(root: Path, skip: tuple[str, ...] = (".git",)) -> dict[str, str]:
    """Digest of every regular file under root, by relative path. Links are recorded by target."""
    out: dict[str, str] = {}
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip and d != "__pycache__")
        # Links to directories are recorded by target too; os.walk does not descend into them.
        filenames = [*filenames, *(d for d in dirnames if (Path(dirpath) / d).is_symlink())]
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                out[rel] = "link:" + os.readlink(path)
            elif path.is_file():
                out[rel] = sha256_file(path)
    return out


def folder_digest(root: Path) -> str:
    """One digest for a folder (the installed workflow, 细则 5.1)."""
    h = hashlib.sha256()
    for rel, digest in sorted(tree_digests(root).items()):
        h.update(f"{rel}\0{digest}\n".encode())
    return h.hexdigest()


def compare_trees(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    """细则 5.4: original files must not change; new files are only listed."""
    return {
        "modified": sorted(p for p in before if p in after and after[p] != before[p]),
        "deleted": sorted(p for p in before if p not in after),
        "added": sorted(p for p in after if p not in before),
    }


# 细则 5.2: what is frozen after each stage. The digests travel in the workflow state; digests.json
# in the run directory is only a copy for reading, since the alignment agent can write there.
FROZEN = {
    "requirement": ["requirement.json", "requirement.txt", "run.json"],
    "propositions": ["propositions.json"],
    "align_start": ["w-before.json"],
    "checks": ["align/checks.frozen.json"],
    "results": ["results.json"],
}
ALIGN_TREE = "align/"
WORKFLOW = "workflow"


def record_digests(run: Path, stage: str, digests: dict, extra: dict | None = None) -> dict:
    """Add the digests of `stage` to `digests` (the copy in the workflow state) and return it."""
    digests = dict(digests or {})
    for rel in FROZEN[stage]:
        digests[rel] = sha256_file(run / rel) if (run / rel).is_file() else None
    if stage == "checks":
        tree = tree_digests(run / "align")
        digests[ALIGN_TREE] = {rel: d for rel, d in tree.items() if rel != "checks.frozen.json"}
    digests.update(extra or {})
    write_json(run / "digests.json", digests)
    return digests


def verify_digests(run: Path, digests: dict, workflow_dir: Path | None) -> tuple[list[str], list[str]]:
    """Frozen files that changed or vanished (fail closed), and files added to align/ (only listed)."""
    changed: list[str] = []
    added: list[str] = []
    for rel, digest in (digests or {}).items():
        if rel == WORKFLOW:
            if workflow_dir is None or folder_digest(workflow_dir) != digest:
                changed.append("工作流文件夹")
        elif rel == ALIGN_TREE:
            diff = compare_trees(digest, tree_digests(run / "align"))
            changed.extend("align/" + f for f in diff["modified"] + diff["deleted"])
            added.extend("align/" + f for f in diff["added"] if f != "checks.frozen.json")
        else:
            path = run / rel
            if digest is None or not path.is_file() or path.is_symlink() or sha256_file(path) != digest:
                changed.append(rel)
    return changed, added
