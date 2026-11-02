"""The wrapper every check command runs under (细则 5.5).

    python3 cv_exec.py TIMEOUT -- sh -c COMMAND

It runs the command in a new process group, keeps only the tails of its output, and after the command
ends or times out kills every process the command left behind. On Linux it makes itself a child
subreaper, so descendants that left the process group (setsid, double fork) are reparented to it and
killed too. It prints one JSON object: exit code, timeout, the output tails, seconds.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import selectors
import signal
import subprocess
import sys
import time

TAIL = 2000
PR_SET_CHILD_SUBREAPER = 36


def _subreaper() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def _children() -> list[int]:
    """Direct children of this process, from /proc (Linux)."""
    me, out = os.getpid(), []
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                stat = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2 :].split()
        if len(fields) > 1 and fields[1] == str(me):
            out.append(int(name))
    return out


def _reap() -> None:
    with contextlib.suppress(ChildProcessError):
        while os.waitpid(-1, os.WNOHANG)[0] > 0:
            pass


def _kill_all(pgid: int, subreaper: bool) -> None:
    with contextlib.suppress(OSError):
        os.killpg(pgid, signal.SIGKILL)
    if not subreaper:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _reap()
        kids = _children()
        if not kids:
            return
        for pid in kids:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.02)


def main(argv: list[str]) -> int:
    timeout = float(argv[0])
    command = argv[argv.index("--") + 1 :]
    subreaper = _subreaper()
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as e:
        print(json.dumps({"exit": None, "timeout": False, "error": f"无法启动：{e}", "stdout": "", "stderr": ""}))
        return 0
    tails = {"stdout": b"", "stderr": b""}
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, "stderr")
    deadline = start + timeout
    timed_out = False
    # Read until the command exits (its pipes may stay open in leftover processes, so do not wait for EOF).
    while True:
        if proc.poll() is not None:
            break
        left = deadline - time.monotonic()
        if left <= 0:
            timed_out = True
            break
        for key, _ in sel.select(timeout=min(left, 0.2)):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                sel.unregister(key.fileobj)
                continue
            tails[key.data] = (tails[key.data] + chunk)[-TAIL * 4 :]
    _kill_all(proc.pid, subreaper)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
    # Whatever is still buffered in the pipes; their writers are gone now.
    end = time.monotonic() + 1
    while sel.get_map() and time.monotonic() < end:
        for key, _ in sel.select(timeout=0.1):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                sel.unregister(key.fileobj)
                continue
            tails[key.data] = (tails[key.data] + chunk)[-TAIL * 4 :]
    _reap()
    print(
        json.dumps(
            {
                "exit": None if timed_out else proc.returncode,
                "timeout": timed_out,
                "error": "超时" if timed_out else None,
                "stdout": tails["stdout"].decode("utf-8", "replace")[-TAIL:],
                "stderr": tails["stderr"].decode("utf-8", "replace")[-TAIL:],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
