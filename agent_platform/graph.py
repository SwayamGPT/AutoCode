"""The pipeline as a LangGraph state machine.

    recall -> context -> plan -> build -> implement -> verify -> (fix) -> review -> patch -> verify ...
                                     \\ (fast mode) -------------^             \\-> finish -> learn -> END

Each node is one of the Orchestrator's step methods; the graph owns the control flow (fast-mode skip, the
fix branch on failed validation, the review/patch loop, and stopping on pass or max iterations). Every
node emits a `node` event so the UI can show where the run is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from .learning import GENERAL, format_lessons
from .models import RunResult
from .personas import PERSONAS

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

NODES = ("recall", "context", "plan", "build", "implement", "verify", "fix", "review", "patch", "finish", "learn")
FIXED_STEPS = 12  # nodes outside the review loop, plus headroom


class PipelineState(TypedDict, total=False):
    requirement: str
    existing_files: dict[str, str]
    lessons: dict[str, list[str]]
    context: str
    plan: dict[str, Any]
    files: dict[str, str]
    iteration: int
    verified: bool
    reviews: list[dict[str, Any]]
    passed: bool
    best: Any  # Snapshot | None; LangGraph evaluates these hints at runtime
    result: RunResult


def build_pipeline(orchestrator: Orchestrator) -> Any:
    o = orchestrator
    config = o.config

    def node(name: str, fn: Callable[[PipelineState], dict[str, Any]]) -> Callable[[PipelineState], dict[str, Any]]:
        def run(state: PipelineState) -> dict[str, Any]:
            o.emit("node", {"node": name, "iteration": state.get("iteration", 0)})
            return fn(state)

        return run

    def recall(state: PipelineState) -> dict[str, Any]:
        lessons = o.learner.recall(state["requirement"], [p.name for p in PERSONAS]) if o.learner else {}
        return {"lessons": lessons}

    def context(state: PipelineState) -> dict[str, Any]:
        return {"context": o.context_agent.gather(state["requirement"], state["existing_files"])}

    def plan(state: PipelineState) -> dict[str, Any]:
        planner_context = state["context"]
        general = state["lessons"].get(GENERAL)
        if general:
            planner_context += f"\n\nLessons from similar past tasks:\n{format_lessons(general)}"
        result = o.make_plan(state["requirement"], state["existing_files"], planner_context, state["lessons"])
        o.trace.plans = result["dimensions"]
        return {"plan": result}

    def build(state: PipelineState) -> dict[str, Any]:
        return {"files": o.build_foundation(state["requirement"], state["plan"], state["existing_files"], state["context"])}

    def implement(state: PipelineState) -> dict[str, Any]:
        files = o.implement_dimensions(state["requirement"], state["plan"], state["files"], state["lessons"], o.trace)
        return {"files": files}

    def verify(state: PipelineState) -> dict[str, Any]:
        iteration = state.get("iteration", 0) + 1
        o.status(f"Review iteration {iteration}/{config.max_iterations}")
        verified = config.skip_verify or o.verify(state["plan"], state["files"], o.trace)
        return {"iteration": iteration, "verified": verified}

    def fix(state: PipelineState) -> dict[str, Any]:
        files, verified = o.fix(state["requirement"], state["plan"], state["files"], state["lessons"], o.trace)
        return {"files": files, "verified": verified}

    def review(state: PipelineState) -> dict[str, Any]:
        from .orchestrator import Snapshot, is_failing

        reviews = o.review_round(o.review_coordinator, state["requirement"], state["plan"], state["files"], state["iteration"])
        passed = state["verified"] and not any(is_failing(r, config.passing_score) for r in reviews)
        snapshot = Snapshot(state["iteration"], state["files"], reviews, state["verified"], passed)
        best = state.get("best")
        return {"reviews": reviews, "passed": passed, "best": snapshot if best is None or snapshot.rank > best.rank else best}

    def patch(state: PipelineState) -> dict[str, Any]:
        return {"files": o.apply_patches(state["files"], state["reviews"], o.trace)}

    def finish(state: PipelineState) -> dict[str, Any]:
        if state["passed"]:
            o.status("All quality gates passed.")
            return {"result": RunResult(state["files"], state["plan"], state["reviews"], True)}
        best = state["best"]
        assert best is not None  # review ran at least once
        if best.files is not state["files"]:
            o.emit("files", {"files": best.files, "stage": f"best:iteration {best.iteration}"})
        o.status(f"Max iterations reached. Returning the best reviewed version (iteration {best.iteration}).")
        return {"result": RunResult(best.files, state["plan"], best.reviews, False)}

    def learn(state: PipelineState) -> dict[str, Any]:
        if o.learner:
            o.learner.learn(o.trace, state["result"].reviews, state["result"].passed)
        return {}

    graph = StateGraph(PipelineState)
    for name, fn in {
        "recall": recall, "context": context, "plan": plan, "build": build, "implement": implement,
        "verify": verify, "fix": fix, "review": review, "patch": patch, "finish": finish, "learn": learn,
    }.items():
        graph.add_node(name, node(name, fn))

    graph.add_edge(START, "recall")
    graph.add_edge("recall", "context")
    graph.add_edge("context", "plan")
    graph.add_edge("plan", "build")
    graph.add_conditional_edges("build", lambda s: "verify" if config.fast_mode else "implement", ["verify", "implement"])
    graph.add_edge("implement", "verify")
    graph.add_conditional_edges("verify", lambda s: "review" if s["verified"] else "fix", ["review", "fix"])
    graph.add_edge("fix", "review")
    graph.add_conditional_edges(
        "review",
        lambda s: "finish" if s["passed"] or s["iteration"] >= config.max_iterations else "patch",
        ["finish", "patch"],
    )
    graph.add_edge("patch", "verify")
    graph.add_edge("finish", "learn")
    graph.add_edge("learn", END)
    return graph.compile()


def run_pipeline(orchestrator: Orchestrator, requirement: str, existing_files: dict[str, str]) -> RunResult:
    graph = build_pipeline(orchestrator)
    limit = FIXED_STEPS + 4 * orchestrator.config.max_iterations  # verify, fix, review, patch per round
    final = graph.invoke(
        {"requirement": requirement, "existing_files": existing_files, "best": None},
        {"recursion_limit": limit},
    )
    return final["result"]


def pipeline_diagram() -> str:
    """Mermaid source of the pipeline graph (shown in docs and available to the UI)."""
    from .config import config_from_env
    from .orchestrator import Orchestrator

    orchestrator = Orchestrator.__new__(Orchestrator)  # the graph's shape only needs config and emit
    orchestrator.config = config_from_env()
    orchestrator.emit = lambda *_: None
    return build_pipeline(orchestrator).get_graph().draw_mermaid()
