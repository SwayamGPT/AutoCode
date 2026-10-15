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
