import { describe, expect, it } from "vitest";
import { fixtureGraphs, makeReplay } from "../test/factories";
import type { GraphDef, Replay, Step } from "./types";
import {
  endTime,
  nextStepTime,
  nextVisitTime,
  nodeTicks,
  prevStepTime,
  stateAt,
  WAIT_NOTE_S,
} from "./state";

const multi = fixtureGraphs().graphs.multi;
const single = fixtureGraphs().graphs.single;

/** Shift every step from index `from` on by `by` seconds. */
function shifted(replay: Replay, from: number, by: number): Replay {
  const steps = replay.steps.map((s, i) =>
    i >= from ? { ...s, t: Math.round((s.t + by) * 100) / 100 } : s,
  );
  return { ...replay, steps: steps as Step[] };
}

describe("stateAt", () => {
  it("at time 0 only the first node is active", () => {
    const s = stateAt(makeReplay(), multi, 0);
    expect(s.activeNode).toBe("fetch_issue");
    expect(s.activeVisit).toBe(1);
    expect(s.stepIndex).toBe(0);
    expect(s.finished).toBe(false);
    const active = Object.entries(s.nodeStates).filter(([, v]) => v === "active");
    expect(active).toEqual([["fetch_issue", "active"]]);
    expect(s.nodeStates.planner).toBe("idle");
    expect(s.nodeStates.coder).toBe("idle");
  });

  it("marks visited nodes done and counts visits", () => {
    const s = stateAt(makeReplay(), multi, 16.7);
    expect(s.activeNode).toBe("run_tests");
    expect(s.nodeStates.run_tests).toBe("active");
    for (const n of ["fetch_issue", "provision_sandbox", "planner", "coder", "collect_diff"]) {
      expect(s.nodeStates[n]).toBe("done");
    }
    expect(s.nodeStates.reviewer).toBe("idle");
    expect(s.visits).toMatchObject({ planner: 1, coder: 1, run_tests: 1 });
    expect(s.visits.reviewer).toBeUndefined();
  });

  it("a revisit raises the visit count and takes the fail edge", () => {
    const replay = makeReplay();
    const steps = [...replay.steps];
    const extra = {
      ...steps[11],
      i: 99,
      t: 16.7,
      kind: "node",
      node: "coder",
      visit: 2,
      from: "run_tests",
      via: "fail",
      message: null,
    } as Step;
    const s = stateAt({ ...replay, steps: [...steps.slice(0, 14), extra] }, multi, 20);
    expect(s.visits.coder).toBe(2);
    expect(s.activeVisit).toBe(2);
    expect(
      s.takenEdges.some((e) => e.from === "run_tests" && e.to === "coder" && e.route === "fail"),
    ).toBe(true);
  });

  it("lists taken edges with their routes", () => {
    const s = stateAt(makeReplay(), multi, 5.1);
    const keys = s.takenEdges.map((e) => `${e.from}>${e.to}:${e.route}`);
    // F9: the first node step has from null, which means START.
    expect(keys).toContain("START>fetch_issue:null");
    expect(keys).toContain("fetch_issue>provision_sandbox:null");
    expect(keys).toContain("provision_sandbox>planner:null");
    expect(keys).toContain("route_plan>coder:actionable");
    expect(keys).not.toContain("route_plan>report_failure:declined");
    expect(s.nodeStates.START).toBe("done");
    expect(new Set(keys).size).toBe(keys.length);
    // Nothing is taken before its node step is reached.
    expect(stateAt(makeReplay(), multi, 0).takenEdges.map((e) => e.to)).toEqual(["fetch_issue"]);
  });

  it("keeps the latest and every plan, claim, diff, test run and review", () => {
    const replay = makeReplay();
    expect(stateAt(replay, multi, 4.9).plans).toHaveLength(0);
    const s = stateAt(replay, multi, endTime(replay));
    expect(s.plans).toHaveLength(1);
    expect(s.claims).toHaveLength(1);
    expect(s.diffs).toHaveLength(1);
    expect(s.tests).toHaveLength(1);
    expect(s.reviews).toHaveLength(1);
    expect(s.plans[0].kind).toBe("plan");
    // Two diffs: both are kept, in order.
    const diff = replay.steps[12];
    const two = {
      ...replay,
      steps: [
        ...replay.steps.slice(0, 13),
        { ...diff, i: 50, t: 16.55 },
        ...replay.steps.slice(13),
      ] as Step[],
    };
    expect(stateAt(two, multi, 17).diffs).toHaveLength(2);
  });

  it("takes the running numbers from the steps", () => {
    const replay = makeReplay();
    const s = stateAt(replay, multi, 14.1);
    expect(s.costUsd).toBe(0.009);
    expect(s.toolCalls).toBe(2);
    expect(s.tokensIn).toBe(900 + 1500 + 1800);
    expect(s.tokensOut).toBe(150 + 200 + 200);
    const none = stateAt(replay, multi, -1);
    expect([none.costUsd, none.toolCalls, none.tokensIn, none.stepIndex, none.activeNode]).toEqual([
      0, 0, 0, -1, null,
    ]);
  });

  it("marks the node of a stop as stopped", () => {
    const s = stateAt(makeReplay("single"), single, 15.2);
    expect(s.stopped?.kind).toBe("stop");
    expect(s.nodeStates.report_failure).toBe("stopped");
    expect(s.finished).toBe(false);
    expect(s.nodeStates.run_tests).toBe("done");
  });

  it("at the end the outcome node is the end node and finished is true", () => {
    const replay = makeReplay();
    const s = stateAt(replay, multi, endTime(replay));
    expect(s.finished).toBe(true);
    expect(s.nodeStates.deliver_patch).toBe("end");
    expect(s.nodeStates.coder).toBe("done");
    expect(Object.values(s.nodeStates)).not.toContain("active");
    expect(s.costUsd).toBe(replay.steps.at(-1)?.cost_usd);
  });

  it("stopped beats end when a cap stop shares its node and time with the outcome", () => {
    const replay = makeReplay("single");
    const stopStep = replay.steps.find((x) => x.kind === "stop") as Step;
    const steps = replay.steps.map((x) =>
      x.kind === "outcome" ? { ...x, t: stopStep.t } : x,
    ) as Step[];
    const capStop = { ...replay, steps };
    const s = stateAt(capStop, single, endTime(capStop));
    expect(s.finished).toBe(true);
    expect(s.nodeStates.report_failure).toBe("stopped");
    // Without a stop step the same node is `end`.
    const noStop = { ...replay, steps: replay.steps.filter((x) => x.kind !== "stop") };
    expect(stateAt(noStop, single, endTime(noStop)).nodeStates.report_failure).toBe("end");
  });

  it("uses step order when times are equal", () => {
    const s = stateAt(makeReplay(), multi, 5.0);
    expect(s.stepIndex).toBe(4);
    expect(s.plans).toHaveLength(1);
  });

  it("adds a wait item for a silence of 15 s or more", () => {
    expect(WAIT_NOTE_S).toBe(15);
    const replay = shifted(makeReplay(), 6, 20);
    const feed = stateAt(replay, multi, endTime(replay)).feed;
    const waits = feed.filter((f) => f.kind === "wait");
    expect(waits).toHaveLength(1);
    expect(waits[0]).toMatchObject({ kind: "wait", step: 6, seconds: 23.9 });
    // The wait comes before the step's own feed item.
    const at = feed.findIndex((f) => f.kind === "wait");
    expect(feed[at + 1]).toMatchObject({ kind: "call", step: 6 });
    // A gap of exactly 15 s counts; just under does not.
    const exact = shifted(makeReplay(), 6, 15 - 3.9);
    expect(stateAt(exact, multi, endTime(exact)).feed.filter((f) => f.kind === "wait")).toHaveLength(1);
    const under = shifted(makeReplay(), 6, 10.99);
    expect(stateAt(under, multi, endTime(under)).feed.filter((f) => f.kind === "wait")).toHaveLength(0);
  });

  it("builds feed items in order: separators, calls, results, stop", () => {
    const feed = stateAt(makeReplay("single"), single, 15.2).feed;
    expect(feed.map((f) => f.kind)).toEqual([
      "separator",
      "separator",
      "separator",
      "call",
      "result",
      "separator",
      "separator",
      "separator",
      "stop",
    ]);
    expect(feed[0]).toMatchObject({ node: "fetch_issue", visit: 1, via: null });
    expect(feed[3]).toMatchObject({ agent: expect.any(String) });
  });

  it("turns unknown step kinds into generic feed lines", () => {
    const replay = makeReplay();
    const steps = [...replay.steps];
    steps[4] = { ...steps[4], kind: "other", name: "mystery" } as Step;
    const s = stateAt({ ...replay, steps }, multi, 5.0);
    expect(s.feed.find((f) => f.kind === "other")).toMatchObject({
      kind: "other",
      step: 4,
      label: "mystery",
    });
    expect(s.plans).toHaveLength(0);
  });

  it("next and previous step times and next visit time", () => {
    const replay = makeReplay();
    expect(endTime(replay)).toBe(41.5);
    expect(nextStepTime(replay, 0)).toBe(0.4);
    expect(nextStepTime(replay, 5.0)).toBe(5.1);
    expect(nextStepTime(replay, 31)).toBe(41.5);
    expect(nextStepTime(replay, 41.5)).toBe(41.5);
    expect(prevStepTime(replay, 5.1)).toBe(5.0);
    expect(prevStepTime(replay, 5.05)).toBe(5.0);
    expect(prevStepTime(replay, 0)).toBe(0);
    expect(nextVisitTime(replay, "coder", 0)).toBe(5.1);
    expect(nextVisitTime(replay, "coder", 5.1)).toBe(5.1); // only one visit, so back to it
    expect(nextVisitTime(replay, "planner", 10)).toBe(3.1); // none after, so its first
    expect(nextVisitTime(replay, "route_plan", 0)).toBeNull();
    expect(nodeTicks(replay)).toHaveLength(replay.steps.filter((x) => x.kind === "node").length);
    expect(nodeTicks(replay)[0]).toEqual({ t: 0, node: "fetch_issue" });
  });

  it("ignores a null from when the graph has no START edge", () => {
    const g: GraphDef = { ...multi, edges: multi.edges.filter((e) => e.from !== "START") };
    const s = stateAt(makeReplay(), g, 0);
    expect(s.takenEdges).toEqual([]);
    expect(s.nodeStates.START).toBe("idle");
  });
});
