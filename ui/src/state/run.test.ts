import { describe, expect, it } from "vitest";
import type { RunEvent } from "../types";
import { initialState, reducer, type RunState } from "./run";
import { buildTree, diffLineClass, sortedEntries } from "./tree";

const event = (kind: string, data: Record<string, unknown>, id = 0): RunEvent => ({ id, kind, data, ts: 100 });
const started = (): RunState =>
  reducer(
    reducer(initialState, { type: "config", personas: ["Security", "Testing"], passingScore: 80 }),
    { type: "runStarted", id: "r1", startedAt: 90, requirement: "build it", passingScore: 80 },
  );

describe("run reducer", () => {
  it("selects the first changed file and switches to the diff view", () => {
    const state = reducer(started(), {
      type: "event",
      event: event("files", { files: { "a.py": "1", "b.py": "2" }, diffs: { "b.py": "+2" }, stage: "generated" }),
    });
    expect(state.selected).toBe("b.py");
    expect(state.view).toBe("diff");
    expect(state.log.at(-1)?.text).toBe("Wrote 1 changed files");
  });

  it("adds unknown reviewers (A2A peers) as new gauge columns", () => {
    const state = reducer(started(), {
      type: "event",
      event: event("review", { review: { persona: "Peer 127.0.0.1:8001", score: 70, status: "needs_work" } }),
    });
    expect(state.personas).toEqual(["Security", "Testing", "Peer 127.0.0.1:8001"]);
  });

  it("hides reviewer LLM calls from the timeline but keeps agent calls", () => {
    let state = reducer(started(), { type: "event", event: event("llm_call", { label: "Security" }) });
    expect(state.log).toHaveLength(1);
    state = reducer(state, { type: "event", event: event("llm_call", { label: "Planner" }) });
    expect(state.log.at(-1)?.text).toBe("Asking model: Planner");
  });

  it("counts passing reviewers against the run's passing score", () => {
    const reviews = [
      { persona: "Security", score: 85, status: "pass" },
      { persona: "Testing", score: 79, status: "pass" },
    ];
    const state = reducer(started(), { type: "event", event: event("scorecard", { iteration: 1, reviews }) });
    expect(state.log.at(-1)?.text).toBe("Iteration 1: 1 of 2 reviewers passed");
  });

  it("marks the run finished", () => {
    const state = reducer(started(), {
      type: "event",
      event: event("finished", { status: "error", passed: false, error: "Ollama down" }),
    });
    expect(state.run?.status).toBe("error");
    expect(state.log.at(-1)).toMatchObject({ text: "Failed: Ollama down", tone: "bad" });
  });
});

describe("ten-step dimension pipeline", () => {
  const plan = (dimension: string, applicable = true) => ({
    dimension,
    applicable,
    reason: applicable ? "" : "No data",
    requirements: ["r"],
    decisions: [],
    files: [],
    acceptance_checks: ["c"],
  });

  it("starts runs on the plan view and collects dimension plans in order", () => {
    let state = started();
    expect(state.view).toBe("plan");
    state = reducer(state, { type: "event", event: event("dimension_plan", { step: 1, total: 10, plan: plan("Security") }) });
    state = reducer(state, { type: "event", event: event("dimension_plan", { step: 2, total: 10, plan: plan("Testing", false) }) });
    expect(state.dimensionPlans.map((p) => p.dimension)).toEqual(["Security", "Testing"]);
    expect(state.log.at(-1)?.text).toBe("Planned Testing (2/10): not applicable");
  });

  it("records dimension changes and hides their model calls from the timeline", () => {
    let state = reducer(started(), { type: "event", event: event("llm_call", { label: "Implement: Security" }) });
    expect(state.log).toHaveLength(1);
    const change = { dimension: "Security", step: 2, total: 10, changed: ["auth.py"], skipped: false, summary: "Hashed passwords." };
    state = reducer(state, { type: "event", event: event("dimension_change", change) });
    expect(state.dimensionChanges.Security.changed).toEqual(["auth.py"]);
    expect(state.log.at(-1)?.text).toBe("Security (2/10): Hashed passwords.");
  });

  it("tracks run metrics from llm_done totals", () => {
    const totals = { llm_calls: 3, prompt_tokens: 900, output_tokens: 100, llm_seconds: 4 };
    const state = reducer(started(), { type: "event", event: event("llm_done", { label: "Planner", totals }) });
    expect(state.metrics).toEqual(totals);
  });
});

describe("memory and learning events", () => {
  it("records recalled lessons, chosen strategies, fixes and new lessons", () => {
    let state = reducer(started(), {
      type: "event",
      event: event("memory_recall", { backend: "postgres", total: 1, lessons: { Security: ["[plan] Rate-limit login."] } }),
    });
    expect(state.recalled.Security).toEqual(["[plan] Rate-limit login."]);
    expect(state.log.at(-1)?.text).toBe("Recalled 1 lessons from similar past tasks");

    state = reducer(state, { type: "event", event: event("strategy", { dimension: "Security", strategy: "thorough" }) });
    expect(state.strategies).toEqual({ Security: "thorough" });

    state = reducer(state, {
      type: "event",
      event: event("fix", { diagnosis: "Missing import.", changed: ["app.py"], verified: true }),
    });
    expect(state.log.at(-1)).toMatchObject({ text: "Fixer: Missing import. (fixed)", tone: "ok" });

    const lessons = [{ dimension: "Security", step: "plan", lesson: "Plan rate limits." }];
    state = reducer(state, { type: "event", event: event("reflection", { summary: "ok", lessons, stored: 2 }) });
    expect(state.learned).toEqual(lessons);
    expect(state.log.at(-1)?.text).toBe("Learned 1 lessons for next time");
  });
});

describe("pipeline graph events", () => {
  it("tracks the current node, visited nodes and the review round", () => {
    let state = started();
    for (const [node, iteration] of [["recall", 0], ["plan", 0], ["verify", 0], ["review", 1], ["patch", 1], ["verify", 1]] as const) {
      state = reducer(state, { type: "event", event: event("node", { node, iteration }) });
    }
    expect(state.node).toBe("verify");
    expect(state.visited).toEqual(["recall", "plan", "verify", "review", "patch"]);
    expect(state.iteration).toBe(2);
  });
});

describe("tree helpers", () => {
  it("nests paths with directories first", () => {
    const tree = buildTree(["z.py", "src/b.py", "src/a.py"]);
    expect(sortedEntries(tree).map(([name]) => name)).toEqual(["src", "z.py"]);
    expect(sortedEntries(tree.src as Record<string, string>).map(([, path]) => path)).toEqual(["src/a.py", "src/b.py"]);
  });

  it("classifies diff lines", () => {
    expect(["@@ -1 +1 @@", "+x", "-y", "+++ b/f", " ctx"].map(diffLineClass)).toEqual(["hunk", "add", "del", "", ""]);
  });
});
