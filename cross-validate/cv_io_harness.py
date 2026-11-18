"""Runs inside the sandbox next to the alignment agent's module (细则 11.12); never imported by the plugin.

Reads one JSON request on standard input and writes one JSON answer. Every call into the module is
caught on its own, so one bad item does not hide the others.
"""

import importlib.util
import json
import random
import sys

NAMES = ("examples", "valid_input", "generate", "derive", "trace", "skip", "EXAMPLE_OF")


def safe(fn, *args):
    try:
        value = fn(*args)
        json.dumps(value, allow_nan=False)
        return {"ok": value}
    except BaseException as e:  # the module is untrusted; report, never crash
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}


def main() -> None:
    sys.set_int_max_str_digits(0)
    req = json.loads(sys.stdin.read())
    try:
        spec = importlib.util.spec_from_file_location("io_module", sys.argv[1])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except BaseException as e:
        print(json.dumps({"load_error": f"{type(e).__name__}: {str(e)[:300]}"}))
        return
    missing = [n for n in NAMES if not hasattr(mod, n)]
    if missing:
        print(json.dumps({"load_error": f"缺少 {'、'.join(missing)}"}, ensure_ascii=False))
        return
    op = req["op"]
    if op == "examples":
        out = {
            "examples": safe(lambda: [[str(a), str(b)] for a, b in mod.examples()]),
            "example_of": safe(lambda: {str(k): int(v) for k, v in dict(mod.EXAMPLE_OF).items()}),
        }
    elif op == "valid":
        out = {"items": [safe(lambda t=t: list(mod.valid_input(t))) for t in req["items"]]}
    elif op == "generate":
        out = {"items": [safe(lambda s=s, k=k: mod.generate(random.Random(s), k)) for s, k in req["items"]]}
    elif op == "observe":
        items = []
        for it in req["items"]:
            item = {"skip": safe(lambda i=it: list(mod.skip(i["input"]))), "derive": safe(mod.derive, it["input"])}
            if it.get("output") is not None:
                item["trace"] = safe(mod.trace, it["input"], it["output"])
            items.append(item)
        out = {"items": items}
    elif op == "skip":  # 细则 11.12 trial item 8: only skip; derive is checked input by input in the pre-check
        out = {"items": [{"skip": safe(lambda t=t: list(mod.skip(t)))} for t in req["items"]]}
    elif op == "trace":  # 细则 11.12: skip and derive were frozen with the inputs; only the output is read
        out = {"items": [{"trace": safe(mod.trace, it["input"], it["output"])} for it in req["items"]]}
    elif op == "call":
        if not (hasattr(mod, "call_input") and hasattr(mod, "call_output")):
            out = {"load_error": "缺少 call_input 或 call_output"}
        else:
            items = []
            for it in req["items"]:
                item = {"input": safe(mod.call_input, it["name"], it["args"])}
                if it.get("output") is not None:
                    item["value"] = safe(mod.call_output, it["name"], it["output"])
                items.append(item)
            out = {"items": items}
    else:
        out = {"load_error": f"unknown op {op}"}
    sys.stdout.write(json.dumps(out, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
