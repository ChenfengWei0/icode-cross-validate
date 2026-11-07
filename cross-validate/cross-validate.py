# Cross validation (交叉验证) for iCode: README.md is the specification, 交叉验证与组合验证.md (ch. 3) the design.

# /// script
# requires-python = ">=3.10"
# ///

r"""交叉验证 · Cross validation.

INPUT (细则 4.1)
    `@cv-run:<run dir>`   accumulation mode: started by `cv` on a snapshot, the requirement comes
                          from the ledger and is consolidated first.
    any other text        goal mode: the main agent implements the goal first; the goal is R.

GRAPH (细则 4.4)
    intake ─goal─▶ implement (main agent) ─▶ after_implement ─────────────┐
       └──accum──▶ consolidate stage ────────────────────────────────────┴▶ split stage
    split stage ▶ propose stage ▶ align stage ▶ run_checks ▶ report (output)

    A stage is: <name>_prompt ▶ <name> (agent) ▶ <name>_check ─revise?─▶ <name>_redo (agent) ▶ <name>_final.
    The subtask agent (cv-subtask) has no tools; the alignment agent (cv-align) reads W and writes
    align/. Everything else is a Python node; state lives in the run directory outside the project.
"""

import os
from pathlib import Path

from chrys.workflows import SourceValue, WorkflowBuilder, WorkflowValue

import cv_flow as FLOW

CONFIG = FLOW.load_config()
HERE = Path(__file__).resolve().parent

wf = WorkflowBuilder(
    "交叉验证 · Cross validation",
    description="子任务只读需求写出受控命题，对齐 agent 绑定到代码，checker 判定并写出可信度报告",
)


def _state(value: WorkflowValue) -> dict:
    return dict(value.data) if isinstance(value.data, dict) else {}


def _by_node(sources: list[SourceValue]) -> dict[str, WorkflowValue]:
    return {s.node_id: s.value for s in sources}


def _named(fn, name: str):
    fn.__name__ = fn.__qualname__ = name
    return fn


# ---------------------------------------------------------------------------------------------------
# Intake and goal mode
# ---------------------------------------------------------------------------------------------------


def intake(value: WorkflowValue) -> WorkflowValue:
    state = FLOW.intake(value.text, os.getcwd(), HERE)
    return WorkflowValue(text=value.text if state["mode"] == FLOW.MODE_GOAL else "", data=state)


def is_goal(value: WorkflowValue) -> bool:
    return _state(value).get("mode") == FLOW.MODE_GOAL


def is_accum(value: WorkflowValue) -> bool:
    return _state(value).get("mode") == FLOW.MODE_ACCUM


def carry_intake(sources: list[SourceValue]) -> WorkflowValue:
    by = _by_node(sources)
    value = by["intake"]
    if is_goal(value) and "implement" not in by:
        # 细则 4.2: the main agent failed or was interrupted; the goal is not reached.
        state = {**_state(value), "stopped": "主 agent 没有完成目标（失败或被中断），不算目标达成（细则 4.2）"}
        return WorkflowValue(text="", data=state)
    return value


def after_implement(value: WorkflowValue) -> WorkflowValue:
    state = _state(value)
    # The join also fires in accumulation mode, where `implement` is skipped; pass that state on unused.
    if is_goal(value) and not state.get("stopped"):
        state = FLOW.guarded("after_implement", FLOW.after_implement, state)
    return WorkflowValue(text="", data=state)


# ---------------------------------------------------------------------------------------------------
# Agent stages
# ---------------------------------------------------------------------------------------------------


def agent_step(src, src_name: str, runs, name: str, profile: str, model, timeout, gated: bool):
    """src ─runs?─▶ agent ▶ (subtask only) tools ─rerun?─▶ rerun (细则 5.3).

    Returns (exits, answer_of): the handles to join on, and a function giving (state, answer) from that
    join. The answer comes from the agent or, after a voided answer, from its rerun; the voided answer
    never leaves the tools node.
    """
    n_agent = wf.agent(name, profile=profile, model=model, timeout=timeout)
    wf.edge(src, n_agent, when=_named(runs, f"{name}_runs"))
    if not gated:

        def answer_plain(sources: list[SourceValue]) -> tuple[dict, str | None]:
            by = _by_node(sources)
            return _state(by[src_name]), (by[name].text if name in by else None)

        return [n_agent], answer_plain

    def join_tools(sources: list[SourceValue]) -> WorkflowValue:
        by = _by_node(sources)
        upstream = by[src_name]
        answer = by[name].text if name in by else None
        data = {"state": _state(upstream), "answer": answer, "task": upstream.text, "ran": runs(upstream)}
        return WorkflowValue(text="", data=data)

    def tools_node(value: WorkflowValue) -> WorkflowValue:
        state, answer = value.data["state"], value.data["answer"]
        rerun = None
        if value.data["ran"]:
            rerun = FLOW.guarded(f"{name}_tools", lambda s: FLOW.tool_gate(s, name, value.data["task"]), state)
            if isinstance(rerun, dict):  # the gate itself failed; guarded() recorded why and returned the state
                state, rerun = rerun, None
        if rerun is None:
            return WorkflowValue(text="", data={"state": state, "answer": answer, "rerun": False})
        return WorkflowValue(text=rerun, data={"state": state, "answer": None, "rerun": True})

    def needs_rerun(value: WorkflowValue) -> bool:
        return bool(isinstance(value.data, dict) and value.data.get("rerun"))

    def answer_gated(sources: list[SourceValue]) -> tuple[dict, str | None]:
        by = _by_node(sources)
        gate = by[f"{name}_tools"].data
        if gate["rerun"]:
            return dict(gate["state"]), (by[f"{name}_rerun"].text if f"{name}_rerun" in by else None)
        return dict(gate["state"]), gate["answer"]

    n_tools = wf.python(f"{name}_tools", _named(tools_node, f"{name}_tools"))
    n_rerun = wf.agent(f"{name}_rerun", profile=profile, model=model, timeout=timeout)
    wf.join([src, n_agent], n_tools, combine=_named(join_tools, f"{name}_join_tools"))
    wf.edge(n_tools, n_rerun, when=_named(needs_rerun, f"{name}_needs_rerun"))
    return [n_tools, n_rerun], answer_gated


def stage(name: str, profile: str, model, make_prompt, check, gated: bool = False):
    """prompt ▶ agent ▶ check ─revise?─▶ redo ▶ final. Returns (entry, exit) handles.

    gated: the stage runs the subtask agent; each of its agent nodes gets a tools gate and one rerun (细则 5.3).
    """

    def prompt_node(value: WorkflowValue) -> WorkflowValue:
        state = _state(value)
        if state.get("stopped") or state.get("invalid"):
            return WorkflowValue(text="", data={**state, "skip": True})
        try:
            text = make_prompt(state)
        except Exception as e:  # 细则 4.8: untrusted input must not crash the node
            return WorkflowValue(text="", data={**state, "stopped": f"{name}_prompt 节点出错：{e}", "skip": True})
        return WorkflowValue(text=text, data={**state, "skip": False})

    def runs_agent(value: WorkflowValue) -> bool:
        return not _state(value).get("skip")

    def check_node_of(join_answer):
        def join_first(sources: list[SourceValue]) -> WorkflowValue:
            state, answer = join_answer(sources)
            return WorkflowValue(text=answer or "", data={"state": state, "answered": answer is not None})

        return join_first

    def check_node(value: WorkflowValue) -> WorkflowValue:
        state, answered = value.data["state"], value.data["answered"]
        if state.pop("skip", False):
            return WorkflowValue(text="", data=state)
        # 细则 4.8: no answer counts as still failing after a rewrite; there is no rewrite.
        state, revise = FLOW.guarded(f"{name}_check", check, state, value.text if answered else None, 1)
        return WorkflowValue(text=revise or "", data={**state, "revise": revise is not None})

    def needs_redo(value: WorkflowValue) -> bool:
        return bool(_state(value).get("revise"))

    def final_node(value: WorkflowValue) -> WorkflowValue:
        state, answered = value.data["state"], value.data["answered"]
        if state.pop("revise", False):
            state, _ = FLOW.guarded(f"{name}_final", check, state, value.text if answered else None, 2)
        state.pop("revising", None)
        return WorkflowValue(text="", data=state)

    timeout = CONFIG["agent_timeout"]
    n_prompt = wf.python(f"{name}_prompt", _named(prompt_node, f"{name}_prompt"))
    first, first_answer = agent_step(n_prompt, f"{name}_prompt", runs_agent, name, profile, model, timeout, gated)
    n_check = wf.python(f"{name}_check", _named(check_node, f"{name}_check"), timeout=None)
    redo, redo_answer = agent_step(n_check, f"{name}_check", needs_redo, f"{name}_redo", profile, model, timeout, gated)
    n_final = wf.python(f"{name}_final", _named(final_node, f"{name}_final"), timeout=None)
    wf.join([n_prompt, *first], n_check, combine=_named(check_node_of(first_answer), f"{name}_join"))
    wf.join([n_check, *redo], n_final, combine=_named(check_node_of(redo_answer), f"{name}_join_redo"))
    return n_prompt, n_final


def align_check(state: dict, answer: str, attempt: int):
    return FLOW.align_check(state, answer, attempt, CONFIG)


def run_checks(value: WorkflowValue) -> WorkflowValue:  # 算法1 第7行
    state = FLOW.guarded("run_checks", lambda s: FLOW.run_checks(s, CONFIG), _state(value))
    return WorkflowValue(text="", data=state)


def report(value: WorkflowValue) -> str:  # 算法1 第8行
    return FLOW.report(_state(value), CONFIG)


# ---------------------------------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------------------------------

SUBTASK, SUBTASK_MODEL = CONFIG["subtask_profile"], CONFIG["subtask_model"]

n_intake = wf.python("intake", intake)
n_implement = wf.agent("implement", profile=CONFIG["main_profile"], model=CONFIG["main_model"], timeout=None)
n_after = wf.python("after_implement", after_implement)
wf.edge(n_intake, n_implement, when=is_goal)
wf.join([n_intake, n_implement], n_after, combine=carry_intake)

consolidate_in, consolidate_out = stage(
    "consolidate", SUBTASK, SUBTASK_MODEL, FLOW.consolidate_prompt, FLOW.consolidate_check, gated=True
)
wf.edge(n_intake, consolidate_in, when=is_accum)

split_in, split_out = stage("split", SUBTASK, SUBTASK_MODEL, FLOW.split_prompt, FLOW.split_check, gated=True)
wf.edge(n_after, split_in, when=is_goal)
wf.edge(consolidate_out, split_in)

propose_in, propose_out = stage("propose", SUBTASK, SUBTASK_MODEL, FLOW.propose_prompt, FLOW.propose_check, gated=True)
wf.edge(split_out, propose_in)

align_in, align_out = stage("align", CONFIG["align_profile"], CONFIG["align_model"], FLOW.align_prompt, align_check)
wf.edge(propose_out, align_in)

n_run = wf.python("run_checks", run_checks, timeout=None)
n_report = wf.python("report", report, timeout=None)
wf.chain(align_out, n_run, n_report)

wf.start(n_intake)
wf.output(n_report)
workflow = wf.build()
