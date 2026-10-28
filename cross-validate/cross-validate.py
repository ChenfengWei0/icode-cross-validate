# Cross validation (交叉验证) for C/C++ work done by an iCode agent, as an iCode workflow.

# /// script
# requires-python = ">=3.10"
# ///

r"""Cross validation · 交叉验证: certify the main agent's code against independently generated checks.

WHAT IT DOES
    Implements chapters 2-3 of 交叉验证与组合验证.md (draft v2). The main agent first fixes the
    interface (C headers); before any implementation exists, a generator that sees only the
    requirement items and the headers writes subtask artifacts (formal properties, reference models
    or concrete cases) from two different sources; the judge self-checks and freezes them. When the
    main agent declares completion, the checker compares the code with every artifact and the judge
    sends one of four signals: pass, block (with a counterexample, back to the main agent), pause
    (several readings remain; the user decides) or reject.

    Only cross validation. Compositional verification is out of scope; the one point of contact is
    the contract store `.cv/contracts/` that a pass writes.

HOW IT MEETS THE THREE HARNESS REQUIREMENTS (§3 对 harness 的要求)
    1. veto at completion ...... the `implement` loop: its exit node `decide` is the judge, and the
                                 loop repeats (block) until pass, pause or reject
    2. plan-phase entry ........ the `plan_*` nodes run after `interface` and before `implement`
    3. structured declaration .. the main agent writes `.cv/manifests/<n>.json`
                                 {"requirements_id": ..., "files": [...]}
    iCode hooks cannot do 1: no hook event may refuse the end of a turn.

INPUT
    The requirement item set: inline JSON, a path to a JSON file, or nothing (then
    `.cv/requirements.json`). Requirement processing is upstream of cross validation; free text is
    passed through unchanged as a single item.

        icode workflow run cross-validate --trust --input .cv/requirements.json

THE GRAPH
    intake --[items]--> interface_prompt --> interface (main agent: headers only)
       |                                          |
       +-------------------------join-------------+--> plan_* (generator A/B, self-check, freeze)
                                                                   |
    implement (loop): round_open --> round_prompt --> main (agent) |
                         |                              |          v
                         +-----------join---------------+--> verify --> replan_* --> decide
                                                                   |
                                                                report (output)

    plan_* / replan_*: prepare --[work]--> segment_prompt --> segment (generator)
                         +----join-----------------------------------+--> requests
                       requests --[A]--> prompt_a --> source_a ---+
                       requests --[B]--> prompt_b --> source_b ---+--join--> check
                       check --[rewrite A]--> reprompt_a --> rewrite_a ---+
                       check --[rewrite B]--> reprompt_b --> rewrite_b ---+--join--> freeze

VISIBILITY (§3 组件)
    The generator nodes use the `cv-generator` profile, which has no tools at all: what it reads is
    exactly its prompt, i.e. the requirement items and the headers. Agent nodes receive only text;
    the run state travels on side edges into joins and on disk outside the project.
"""

import os

from chrys.workflows import BuilderScope, NodeHandle, SourceValue, Workflow, WorkflowBuilder, WorkflowValue

import cv_protocol as P
import judge

CONFIG = judge.load_config()  # the plugin's own config.json: profiles and models shape the graph
GENERATOR = CONFIG["generator_profile"]
MAIN = CONFIG["main_profile"]

MAIN_SUFFIX = (
    " You are working inside a cross-validation workflow. Follow the step you are given exactly. Never"
    " read, write or delete anything under .cv/subtasks/ or .cv/contracts/."
)

wf = WorkflowBuilder(
    "Cross validation · 交叉验证",
    description="C/C++: interface first, independent checks frozen before implementation, judged at completion",
)


# ---------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------


def _state(value: WorkflowValue) -> dict:
    return dict(value.data) if isinstance(value.data, dict) else {}


def _value(state: dict) -> WorkflowValue:
    return WorkflowValue(text=state.get("feedback") or "", data=state)


def _flag(name: str):
    def predicate(value: WorkflowValue) -> bool:
        return bool(_state(value).get(name))

    predicate.__name__ = predicate.__qualname__ = f"has_{name}"
    return predicate


def _by_node(sources: list[SourceValue]) -> dict[str, WorkflowValue]:
    return {source.node_id: source.value for source in sources}


# ---------------------------------------------------------------------------------------------------
# Top-level node bodies
# ---------------------------------------------------------------------------------------------------


def intake(value: WorkflowValue) -> WorkflowValue:
    """Start node. The worker's current directory is the session's working directory."""
    return _value(judge.intake(value.text, os.getcwd()))


def wants_interface(value: WorkflowValue) -> bool:
    return not _state(value).get("empty")


def interface_prompt(value: WorkflowValue) -> str:
    return judge.interface_prompt(_state(value))


def carry_intake(sources: list[SourceValue]) -> WorkflowValue:
    """The interface agent's work is on disk; the state comes from intake on the side edge."""
    return _by_node(sources)["intake"]


def report(value: WorkflowValue) -> str:
    state = _state(value)
    if state.get("signal") == P.SIGNAL_BLOCK:
        # The loop ran out of rounds (a harness bound, max_rounds) while the judge still blocked.
        state = dict(state, signal=P.SIGNAL_REJECT)
        state["report"] = dict(state.get("report") or {})
        state["report"]["notes"] = [
            *state["report"].get("notes", []),
            f"达到工作流轮数上限 max_rounds={CONFIG['max_rounds']}，按拒绝处理",
        ]
    return judge.render_report(state)


# ---------------------------------------------------------------------------------------------------
# The plan stage, used twice: before implementation (plan) and inside the loop (replan, 算法1 第17行)
# ---------------------------------------------------------------------------------------------------


def plan_stage(scope: BuilderScope, prefix: str, phase: str) -> tuple[NodeHandle, NodeHandle]:
    def prepare(value: WorkflowValue) -> WorkflowValue:
        state = _state(value)
        if state.get("signal"):  # the judge has already decided this round (算法1 第14-16行)
            return _value(dict(state, has_work=False, has_a=False, has_b=False, rewrite_a=False, rewrite_b=False))
        return _value(judge.plan_prepare(state, phase))

    def segment_prompt(value: WorkflowValue) -> str:
        return judge.segment_prompt(_state(value))

    def join_segments(sources: list[SourceValue]) -> WorkflowValue:
        by = _by_node(sources)
        answer = by[f"{prefix}_segment"].text if f"{prefix}_segment" in by else ""
        return WorkflowValue(text=answer, data=_state(by[f"{prefix}_prepare"]))

    def requests(value: WorkflowValue) -> WorkflowValue:
        state = _state(value)
        return _value(judge.plan_requests(state, value.text) if state.get("has_work") else state)

    def prompt_a(value: WorkflowValue) -> str:
        return judge.generation_prompt(_state(value), "A")

    def prompt_b(value: WorkflowValue) -> str:
        return judge.generation_prompt(_state(value), "B")

    def join_sources(sources: list[SourceValue]) -> WorkflowValue:
        by = _by_node(sources)
        answers = {s: by[f"{prefix}_source_{s.lower()}"].text for s in "AB" if f"{prefix}_source_{s.lower()}" in by}
        return WorkflowValue(text="", data={"state": _state(by[f"{prefix}_requests"]), "answers": answers})

    def check(value: WorkflowValue) -> WorkflowValue:
        state, answers = value.data["state"], value.data["answers"]
        if not (state.get("has_a") or state.get("has_b")):
            return _value(state)
        return _value(judge.plan_check(state, answers.get("A"), answers.get("B")))

    def reprompt_a(value: WorkflowValue) -> str:
        return judge.rewrite_prompt(_state(value), "A")

    def reprompt_b(value: WorkflowValue) -> str:
        return judge.rewrite_prompt(_state(value), "B")

    def join_rewrites(sources: list[SourceValue]) -> WorkflowValue:
        by = _by_node(sources)
        answers = {s: by[f"{prefix}_rewrite_{s.lower()}"].text for s in "AB" if f"{prefix}_rewrite_{s.lower()}" in by}
        return WorkflowValue(text="", data={"state": _state(by[f"{prefix}_check"]), "answers": answers})

    def freeze(value: WorkflowValue) -> WorkflowValue:
        state, answers = value.data["state"], value.data["answers"]
        if state.get("signal"):
            return _value(state)
        return _value(judge.plan_freeze(state, answers.get("A"), answers.get("B")))

    for fn in (prepare, segment_prompt, requests, prompt_a, prompt_b, check, reprompt_a, reprompt_b, freeze):
        fn.__qualname__ = f"{prefix}_{fn.__name__}"
    for fn in (join_segments, join_sources, join_rewrites):
        fn.__qualname__ = f"{prefix}_{fn.__name__}"

    def generator(name: str, model: str | None) -> NodeHandle:
        return scope.agent(f"{prefix}_{name}", profile=GENERATOR, model=model)

    model_a, model_b = CONFIG["generator_a_model"], CONFIG["generator_b_model"]
    n_prepare = scope.python(f"{prefix}_prepare", prepare, timeout=None)
    n_segment_prompt = scope.python(f"{prefix}_segment_prompt", segment_prompt)
    n_segment = generator("segment", model_a)
    n_requests = scope.python(f"{prefix}_requests", requests)
    n_prompt_a = scope.python(f"{prefix}_prompt_a", prompt_a)
    n_prompt_b = scope.python(f"{prefix}_prompt_b", prompt_b)
    n_source_a = generator("source_a", model_a)
    n_source_b = generator("source_b", model_b)
    # Self-check and freeze run the checker (CBMC, compilers) many times; every tool call has its own
    # safety timeout, so the node itself has no deadline.
    n_check = scope.python(f"{prefix}_check", check, timeout=None)
    n_reprompt_a = scope.python(f"{prefix}_reprompt_a", reprompt_a)
    n_reprompt_b = scope.python(f"{prefix}_reprompt_b", reprompt_b)
    n_rewrite_a = generator("rewrite_a", model_a)
    n_rewrite_b = generator("rewrite_b", model_b)
    n_freeze = scope.python(f"{prefix}_freeze", freeze, timeout=None)

    scope.edge(n_prepare, n_segment_prompt, when=_flag("has_work"))
    scope.edge(n_segment_prompt, n_segment)
    scope.join([n_prepare, n_segment], n_requests, combine=join_segments)
    scope.edge(n_requests, n_prompt_a, when=_flag("has_a"))
    scope.edge(n_requests, n_prompt_b, when=_flag("has_b"))
    scope.edge(n_prompt_a, n_source_a)
    scope.edge(n_prompt_b, n_source_b)
    scope.join([n_requests, n_source_a, n_source_b], n_check, combine=join_sources)
    scope.edge(n_check, n_reprompt_a, when=_flag("rewrite_a"))
    scope.edge(n_check, n_reprompt_b, when=_flag("rewrite_b"))
    scope.edge(n_reprompt_a, n_rewrite_a)
    scope.edge(n_reprompt_b, n_rewrite_b)
    scope.join([n_check, n_rewrite_a, n_rewrite_b], n_freeze, combine=join_rewrites)
    return n_prepare, n_freeze


# ---------------------------------------------------------------------------------------------------
# The implement loop: main agent, then the judge (veto at completion)
# ---------------------------------------------------------------------------------------------------


def round_open(value: WorkflowValue) -> WorkflowValue:
    """Loop entry: round 1 gets the frozen plan's state, later rounds the judge's last decision."""
    return _value(_state(value))


def round_prompt(value: WorkflowValue) -> str:
    return judge.implement_prompt(_state(value))


def carry_round(sources: list[SourceValue]) -> WorkflowValue:
    return _by_node(sources)["round_open"]


def verify(value: WorkflowValue) -> WorkflowValue:
    return _value(judge.verify(_state(value)))


def decide(value: WorkflowValue) -> WorkflowValue:
    return _value(judge.compare(_state(value)))


def settled(value: WorkflowValue) -> bool:
    """The loop's `until`: only a block sends the work back to the main agent."""
    return _state(value).get("signal") != P.SIGNAL_BLOCK


def implement_round(scope: BuilderScope) -> tuple[NodeHandle, NodeHandle]:
    entry = scope.python("round_open", round_open)
    prompt = scope.python("round_prompt", round_prompt)
    main = scope.agent("main", profile=MAIN, model=CONFIG["main_model"], instructions_suffix=MAIN_SUFFIX)
    check_in = scope.python("verify", verify, timeout=None)
    replan_entry, replan_exit = plan_stage(scope, "replan", "replan")
    judge_node = scope.python("decide", decide, timeout=None)
    scope.chain(entry, prompt, main)
    scope.join([entry, main], check_in, combine=carry_round)
    scope.edge(check_in, replan_entry)
    scope.edge(replan_exit, judge_node)
    return entry, judge_node


# ---------------------------------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------------------------------

n_intake = wf.python("intake", intake)
n_interface_prompt = wf.python("interface_prompt", interface_prompt)
n_interface = wf.agent("interface", profile=MAIN, model=CONFIG["main_model"], instructions_suffix=MAIN_SUFFIX)
plan_entry, plan_exit = plan_stage(wf, "plan", "plan")
n_implement = wf.loop(
    "implement",
    body=implement_round,
    until=settled,
    max_iterations=CONFIG["max_rounds"],
    on_exhausted="continue",  # `report` turns a block that ran out of rounds into a reject
)
n_report = wf.python("report", report)

wf.start(n_intake)
wf.edge(n_intake, n_interface_prompt, when=wants_interface)
wf.edge(n_interface_prompt, n_interface)
wf.join([n_intake, n_interface], plan_entry, combine=carry_intake)
wf.edge(plan_exit, n_implement)
wf.edge(n_implement, n_report)
wf.output(n_report)

workflow: Workflow = wf.build()
