"""A stand-in for iCode's `chrys.workflows` that builds and runs a graph the way the reference
(docs/en/reference/workflows.md) describes: conditional edges, joins with combine, default merging,
skipping. Agent nodes call a function supplied by the test instead of a model."""

from __future__ import annotations

import inspect
import itertools
import sys
import types
from dataclasses import dataclass


@dataclass
class WorkflowValue:
    text: str
    data: object = None


@dataclass
class SourceValue:
    node_id: str
    activation_id: str
    value: WorkflowValue


@dataclass(frozen=True)
class NodeHandle:
    node_id: str


class BuildError(Exception):
    pass


class WorkflowBuilder:
    def __init__(self, title, *, description=None):
        if not title:
            raise BuildError("title")
        self.nodes: dict[str, dict] = {}
        self.edges: list[tuple[str, str, object]] = []
        self.start_node = None
        self.outputs: list[str] = []

    def _add(self, name, **spec):
        if not name or name.startswith("join:") or "->" in name or name in self.nodes:
            raise BuildError(f"bad or duplicate node name {name!r}")
        self.nodes[name] = spec
        return NodeHandle(name)

    def python(self, name, fn, *, timeout=300.0, retry=None):
        params = list(inspect.signature(fn).parameters.values())
        if len(params) not in (1, 2) or any(p.default is not p.empty for p in params):
            raise BuildError(f"python node {name} signature")
        if timeout is not None and not timeout > 0:
            raise BuildError("timeout")
        return self._add(name, kind="python", fn=fn, timeout=timeout)

    def agent(self, name, *, profile, model=None, instructions_suffix=None, timeout=None, retry=None):
        if not profile:
            raise BuildError("profile")
        return self._add(name, kind="agent", profile=profile, model=model, timeout=timeout)

    def edge(self, src, dst, *, when=None):
        if src.node_id == dst.node_id or any(e[0] == src.node_id and e[1] == dst.node_id for e in self.edges):
            raise BuildError("self or duplicate edge")
        if self.nodes.get(dst.node_id, {}).get("joined"):
            raise BuildError("join destination with another edge")
        self.edges.append((src.node_id, dst.node_id, when))

    def chain(self, *nodes):
        for a, b in itertools.pairwise(nodes):
            self.edge(a, b)

    def join(self, sources, dst, *, combine=None):
        if any(e[1] == dst.node_id for e in self.edges):
            raise BuildError("join destination with another edge")
        name = f"join:{dst.node_id}"
        self.nodes[name] = {"kind": "join", "combine": combine, "sources": [s.node_id for s in sources]}
        for s in sources:
            self.edges.append((s.node_id, name, None))
        self.edges.append((name, dst.node_id, None))
        self.nodes[dst.node_id]["joined"] = True

    def start(self, node):
        if self.start_node is not None:
            raise BuildError("start twice")
        self.start_node = node.node_id

    def output(self, node):
        if node.node_id in self.outputs:
            raise BuildError("output twice")
        self.outputs.append(node.node_id)

    def order(self) -> list[str]:
        incoming = {n: [e for e in self.edges if e[1] == n] for n in self.nodes}
        done, order = set(), []
        while len(order) < len(self.nodes):
            ready = [n for n in self.nodes if n not in done and all(e[0] in done for e in incoming[n])]
            if not ready:
                raise BuildError("cycle")
            for n in ready:
                done.add(n)
                order.append(n)
        return order

    def build(self):
        if self.start_node is None or not self.outputs:
            raise BuildError("start/output")
        order = self.order()
        reach = {self.start_node}
        for n in order:
            if any(e[0] in reach and e[1] == n for e in self.edges):
                reach.add(n)
        if reach != set(self.nodes):
            raise BuildError(f"unreachable: {set(self.nodes) - reach}")
        return Workflow(self)


class Workflow:
    def __init__(self, builder):
        self.b = builder

    def run(self, text: str, agents) -> list[WorkflowValue]:
        """Execute; `agents(node_id, profile, prompt)` answers agent nodes."""
        b = self.b
        out: dict[str, WorkflowValue | None] = {}
        passed: dict[tuple[str, str], WorkflowValue | None] = {}
        for n in b.order():
            spec = b.nodes[n]
            ins = [(e[0], passed[(e[0], e[1])]) for e in b.edges if e[1] == n]
            got = [(s, v) for s, v in ins if v is not None]
            if n == b.start_node:
                value = WorkflowValue(text)
            elif spec["kind"] == "join":
                value = None
                if got:
                    sources = [SourceValue(s, s, v) for s, v in got]
                    value = (
                        spec["combine"](sources)
                        if spec["combine"]
                        else WorkflowValue("\n\n".join(f"## {s}\n{v.text}" for s, v in got))
                    )
                    value = WorkflowValue(value) if isinstance(value, str) else value
            elif not got:
                value = None
            else:
                value = got[0][1] if len(got) == 1 else WorkflowValue("\n\n".join(f"## {s}\n{v.text}" for s, v in got))
            if value is not None and spec["kind"] == "python":
                result = spec["fn"](value)
                value = WorkflowValue(result) if isinstance(result, str) else result
                assert isinstance(value, WorkflowValue)
                _json_only(value.data)
            elif value is not None and spec["kind"] == "agent":
                value = WorkflowValue(agents(n, spec["profile"], value.text))
            out[n] = value
            for e in b.edges:
                if e[0] == n:
                    passed[(e[0], e[1])] = value if value is not None and (e[2] is None or e[2](value)) else None
        return [out[o] for o in b.outputs if out[o] is not None]


def _json_only(data):
    if data is None or isinstance(data, (str, int, float, bool)):
        return
    if isinstance(data, list):
        for x in data:
            _json_only(x)
        return
    if isinstance(data, dict):
        for k, v in data.items():
            assert isinstance(k, str)
            _json_only(v)
        return
    raise AssertionError(f"data holds a non-JSON value: {type(data)}")


def install():
    chrys = types.ModuleType("chrys")
    workflows = types.ModuleType("chrys.workflows")
    for name in ("WorkflowBuilder", "WorkflowValue", "SourceValue", "NodeHandle", "Workflow"):
        setattr(workflows, name, globals()[name])
    workflows.BuilderScope = WorkflowBuilder
    chrys.workflows = workflows
    sys.modules["chrys"] = chrys
    sys.modules["chrys.workflows"] = workflows
