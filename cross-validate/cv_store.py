"""Where the judge keeps what the main agent must not be able to change.

The design puts the hashes of frozen artifacts outside the project (§3 组件, 集成), so a main agent
that edits files in the workspace cannot also edit the record they are checked against. Retry
counters, the record of consumed completion manifests, the plan and both caches live there too.

Layout: ``<chrys home>/cross-validate/<project key>/`` where the chrys home is iCode's
configuration directory (``~/.chrys``, or ``%APPDATA%\\chrys`` on Windows). ``CV_STATE_DIR``
overrides the root, which the tests use.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def chrys_home() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "chrys"
    return Path.home() / ".chrys"


def state_root() -> Path:
    override = os.environ.get("CV_STATE_DIR")
    return Path(override) if override else chrys_home() / "cross-validate"


def project_key(project: Path) -> str:
    resolved = str(project.resolve())
    return f"{project.resolve().name or 'root'}-{hashlib.sha256(resolved.encode('utf-8')).hexdigest()[:16]}"


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Store:
    """The judge's out-of-project record for one project."""

    def __init__(self, project: Path) -> None:
        self.project = project.resolve()
        self.root = state_root() / project_key(self.project)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- JSON documents ---------------------------------------------------------------------

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def load(self, name: str, default: Any) -> Any:
        path = self._path(name)
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, name: str, value: Any) -> None:
        _atomic_write(self._path(name), json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))

    # -- caches -----------------------------------------------------------------------------

    def cache_get(self, kind: str, key: str) -> Any | None:
        path = self.root / "cache" / kind / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def cache_put(self, kind: str, key: str, value: Any) -> None:
        path = self.root / "cache" / kind / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(value, ensure_ascii=False, sort_keys=True))

    # -- session ----------------------------------------------------------------------------

    def begin_session(self, existing_manifests: list[str]) -> None:
        """A new run of the workflow: retry counters and history start over.

        Manifests already on disk belong to earlier runs, so they are recorded as consumed: only a
        manifest written after this point is a new completion declaration (算法1 第14行).
        """
        self.save("retries", {})
        self.save("history", {"rounds": [], "ever_failed": []})
        self.save("seen_manifests", sorted(existing_manifests))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
