"""A stand-in model service (OpenAI Chat Completions, no streaming) that replays the snake example.

It lets a real iCode run the whole plugin without an API key: the main agent writes the snake game,
the subtask agent answers with `split.json` / `subtask.txt`, the alignment agent writes the checks
from `align/`. Which agent is asking is read from its instructions.

    python3 mock_model.py PORT VARIANT [LOG]  # VARIANT: correct | buggy

With MOCK_SUBTASK_TOOL=1 the subtask agent tries one tool call first, to exercise the guard hook.
"""

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SNAKE = Path(__file__).resolve().parents[1]
VARIANT = sys.argv[2] if len(sys.argv) > 2 else "buggy"
LOG = Path(sys.argv[3]) if len(sys.argv) > 3 else None


def text(content):
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def tool_call(tools, name, **args):
    """A call of `name`, with our argument names mapped onto the tool's own parameter names."""
    spec = next((t["function"] for t in tools if t.get("function", {}).get("name") == name), None)
    if spec is None:
        raise KeyError(f"tool {name} not offered; offered: {[t['function']['name'] for t in tools]}")
    props = spec.get("parameters", {}).get("properties", {})
    mapped = {}
    for key, value in args.items():
        target = key if key in props else next((p for p in props if key in p), key)
        mapped[target] = value
    return {
        "id": f"call_{time.monotonic_ns()}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(mapped, ensure_ascii=False)},
    }


def answer(body):
    messages = body.get("messages", [])
    system = "\n".join(text(m.get("content")) for m in messages if m.get("role") == "system")
    # iCode appends system reminders (time, working directory, skills) after the task itself.
    user = [text(m.get("content")) for m in messages if m.get("role") == "user"]
    tasks = [u for u in user if not u.lstrip().startswith("<system-reminder>")]
    last_user = tasks[-1] if tasks else ""
    used_tools = any(m.get("role") == "tool" for m in messages)
    tools = body.get("tools", [])
    if "交叉验证的子任务 agent" in system:
        if os.environ.get("MOCK_SUBTASK_TOOL") and not used_tools:
            # Misbehave on purpose: the guard hook must refuse this and the run must become invalid (细则 5.3).
            return "", [tool_call(tools, "sleep", seconds=1)]
        if "整理出此刻的最终需求" in last_user:
            lines = re.findall(r"^(M\d+)：(.*)$", last_user.split("消息：", 1)[1], re.MULTILINE)
            reqs = [{"text": t, "quotes": [{"from": m, "text": t}]} for m, t in lines if t.strip() not in ("继续", "")]
            disp = {m: ("非需求" if t.strip() in ("继续", "") else "需求") for m, t in lines}
            return json.dumps({"requirement": reqs, "messages": disp}, ensure_ascii=False), []
        if "把需求切成单句" in last_user:
            req = last_user.split("需求：\n\n", 1)[1].strip()
            return json.dumps([{"id": "S1", "text": req, "labels": ["无"]}], ensure_ascii=False), []
        if "写出正确的输出应当满足的逻辑命题" in last_user:
            return (SNAKE / "subtask.txt").read_text(encoding="utf-8"), []
        return "无法识别的请求", []
    if "交叉验证的对齐 agent" in system:
        if used_tools:
            return "已完成", []
        align = re.search(r"你的目录：`([^`]+)`", last_user).group(1)
        checks = json.loads((SNAKE / "align" / "checks.json").read_text(encoding="utf-8"))
        for c in checks["checks"]:
            if c["kind"] == "trace":
                c["runs"] = 10
        calls = [
            tool_call(
                tools,
                "write_file",
                path=f"{align}/checks.json",
                content=json.dumps(checks, ensure_ascii=False, indent=2),
            )
        ]
        calls.extend(
            tool_call(
                tools,
                "write_file",
                path=f"{align}/{name}",
                content=(SNAKE / "align" / name).read_text(encoding="utf-8"),
            )
            for name in ("run_snake.py", "r9_structure.py")
        )
        return "", calls
    # The main agent (Code): write the game once, then report.
    if "实现贪吃蛇" in last_user and not used_tools:
        return "", [
            tool_call(
                tools, "write_file", path="snake.py", content=(SNAKE / VARIANT / "snake.py").read_text(encoding="utf-8")
            )
        ]
    return "好的。", []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        try:
            content, calls = answer(body)
        except Exception as e:  # report every failure to the caller as a reply
            content, calls = f"mock error: {e}", []
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f:
                system = "\n".join(
                    text(m.get("content")) for m in body.get("messages", []) if m.get("role") == "system"
                )
                who = "subtask" if "子任务 agent" in system else "align" if "对齐 agent" in system else "main"
                f.write(
                    json.dumps(
                        {
                            "who": who,
                            "tools": [t["function"]["name"] for t in body.get("tools", [])],
                            "reply": content[:80],
                            "calls": len(calls),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        message = {"role": "assistant", "content": content or None}
        if calls:
            message["tool_calls"] = calls
        reply = {
            "id": f"mock-{time.monotonic_ns()}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "mock"),
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(reply, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
