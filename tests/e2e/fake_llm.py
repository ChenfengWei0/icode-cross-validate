"""A scripted OpenAI-compatible Chat Completions endpoint for the end-to-end run of the workflow.

It stands in for every model the workflow calls. It answers from the prompt it is given, the way
the workflow's agents are addressed: the generator's segmentation and sources A/B get the recorded
answers of examples/discount; the main agent gets tool calls that write the header and the
interface declaration, then a wrong implementation with its completion manifest, then (after the
judge's feedback) a correct one. Anything else gets a short text.

    python fake_llm.py <port> <examples/discount dir>
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EXAMPLE = Path(sys.argv[2]) if len(sys.argv) > 2 else Path()
SET_ID = "Q-discount"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _write(path: str, content: str) -> tuple[str, dict]:
    return "write_file", {"path": path, "content": content, "overwrite": True}


def _script(prompt: str) -> list:
    """The turns of one agent conversation: a list of tool-call batches, then a final text."""
    read = lambda name: (EXAMPLE / name).read_text(encoding="utf-8")  # noqa: E731
    if "交叉验证：划分需求条目" in prompt:
        return [read("generator/segments.txt")]
    if "生成子任务产物（来源 A）" in prompt:
        return [read("generator/source_a.txt")]
    if "生成子任务产物（来源 B）" in prompt:
        return [read("generator/source_b.txt")]
    if "第一步：只确定接口" in prompt:
        interface = json.dumps({"headers": ["include/discount.h"], "include_dirs": ["include"]})
        return [[_write("include/discount.h", read("include/discount.h")), _write(".cv/interface.json", interface)],
                "声明了 include/discount.h。"]  # fmt: skip
    if "第二步：依据需求实现" in prompt:
        manifest = json.dumps({"requirements_id": SET_ID, "files": ["src/discount.c"]})
        return [[_write("src/discount.c", read("src/wrong.c")), _write(".cv/manifests/001.json", manifest)], "完成。"]
    if "交叉验证的反馈" in prompt:
        manifest = json.dumps({"requirements_id": SET_ID, "files": ["src/discount.c"]})
        return [[_write("src/discount.c", read("src/once.c")), _write(".cv/manifests/002.json", manifest)], "已修改。"]
    return ["ok"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        return

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        messages = body.get("messages", [])
        users = [_text(m.get("content")) for m in messages if m.get("role") == "user"]
        prompt = users[0] if users else ""
        step = sum(1 for m in messages if m.get("role") == "assistant")
        turns = _script(prompt)
        turn = turns[min(step, len(turns) - 1)]
        message: dict = {"role": "assistant", "content": None}
        if isinstance(turn, list):
            message["tool_calls"] = [
                {"id": f"call_{step}_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                for i, (n, a) in enumerate(turn)
            ]
            finish = "tool_calls"
        else:
            message["content"] = turn
            finish = "stop"
        reply = {
            "id": "fake",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "fake"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(reply, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
