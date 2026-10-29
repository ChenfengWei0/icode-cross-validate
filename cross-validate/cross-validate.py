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
    return _by_node(sources)["intake"]


def after_implement(value: WorkflowValue) -> WorkflowValue:
    state = _state(value)
    # The join also fires in accumulation mode, where `implement` is skipped; pass that state on unused.
    return WorkflowValue(text="", data=FLOW.after_implement(state) if is_goal(value) else state)


# ---------------------------------------------------------------------------------------------------
# Agent stages
# ---------------------------------------------------------------------------------------------------


def stage(name: str, profile: str, model, make_prompt, check):
    """prompt ▶ agent ▶ check ─revise?─▶ redo ▶ final. Returns (entry, exit) handles."""

    def prompt_node(value: WorkflowValue) -> WorkflowValue:
        state = _state(value)
        if state.get("stopped") or state.get("invalid"):
            return WorkflowValue(text="", data={**state, "skip": True})
        return WorkflowValue(text=make_prompt(state), data={**state, "skip": False})

    def runs_agent(value: WorkflowValue) -> bool:
        return not _state(value).get("skip")

    def join_first(sources: list[SourceValue]) -> WorkflowValue:
        by = _by_node(sources)
        answer = by[name].text if name in by else None
        return WorkflowValue(
            text=answer or "", data={"state": _state(by[f"{name}_prompt"]), "answered": answer is not None}
        )

    def check_node(value: WorkflowValue) -> WorkflowValue:
        state, answered = value.data["state"], value.data["answered"]
        if state.pop("skip", False) or not answered:
            return WorkflowValue(text="", data=state)
        state, revise = check(state, value.text, 1)
        return WorkflowValue(text=revise or "", data={**state, "revise": revise is not None})

    def needs_redo(value: WorkflowValue) -> bool:
        return bool(_state(value).get("revise"))

    def join_redo(sources: list[SourceValue]) -> WorkflowValue:
        by = _by_node(sources)
        answer = by[f"{name}_redo"].text if f"{name}_redo" in by else None
        return WorkflowValue(
            text=answer or "", data={"state": _state(by[f"{name}_check"]), "answered": answer is not None}
        )

    def final_node(value: WorkflowValue) -> WorkflowValue:
        state, answered = value.data["state"], value.data["answered"]
        if state.pop("revise", False) and answered:
            state, _ = check(state, value.text, 2)
        state.pop("revising", None)
        return WorkflowValue(text="", data=state)

    timeout = CONFIG["agent_timeout"]
    n_prompt = wf.python(f"{name}_prompt", _named(prompt_node, f"{name}_prompt"))
    n_agent = wf.agent(name, profile=profile, model=model, timeout=timeout)
    n_check = wf.python(f"{name}_check", _named(check_node, f"{name}_check"), timeout=None)
    n_redo = wf.agent(f"{name}_redo", profile=profile, model=model, timeout=timeout)
    n_final = wf.python(f"{name}_final", _named(final_node, f"{name}_final"), timeout=None)
    wf.edge(n_prompt, n_agent, when=_named(runs_agent, f"{name}_runs_agent"))
    wf.join([n_prompt, n_agent], n_check, combine=_named(join_first, f"{name}_join"))
    wf.edge(n_check, n_redo, when=_named(needs_redo, f"{name}_needs_redo"))
    wf.join([n_check, n_redo], n_final, combine=_named(join_redo, f"{name}_join_redo"))
    return n_prompt, n_final


def align_check(state: dict, answer: str, attempt: int):
    return FLOW.align_check(state, answer, attempt, CONFIG)


def run_checks(value: WorkflowValue) -> WorkflowValue:  # 算法1 第7行
    return WorkflowValue(text="", data=FLOW.run_checks(_state(value), CONFIG))


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
    "consolidate", SUBTASK, SUBTASK_MODEL, FLOW.consolidate_prompt, FLOW.consolidate_check
)
wf.edge(n_intake, consolidate_in, when=is_accum)

split_in, split_out = stage("split", SUBTASK, SUBTASK_MODEL, FLOW.split_prompt, FLOW.split_check)
wf.edge(n_after, split_in, when=is_goal)
wf.edge(consolidate_out, split_in)

propose_in, propose_out = stage("propose", SUBTASK, SUBTASK_MODEL, FLOW.propose_prompt, FLOW.propose_check)
wf.edge(split_out, propose_in)

align_in, align_out = stage("align", CONFIG["align_profile"], CONFIG["align_model"], FLOW.align_prompt, align_check)
wf.edge(propose_out, align_in)

n_run = wf.python("run_checks", run_checks, timeout=None)
n_report = wf.python("report", report, timeout=None)
wf.chain(align_out, n_run, n_report)

wf.start(n_intake)
wf.output(n_report)
workflow = wf.build()
