import type { Graphs, Replay, ReplayIndex, ReplayState } from "../replay/types";
import multi from "./fixtures/replay-multi.json";
import single from "./fixtures/replay-single.json";
import graphsFixture from "./fixtures/graphs.json";
import indexFixture from "./fixtures/index.json";

const clone = <T>(v: T): T => structuredClone(v);

/** F35: switch this import to ../graph/graphs.json once Task 2 lands. */
export const fixtureGraphs = (): Graphs => clone(graphsFixture) as Graphs;

export function makeReplay(
  which: "multi" | "single" = "multi",
  overrides: Partial<Replay> = {},
): Replay {
  const base = clone(which === "multi" ? multi : single) as unknown as Replay;
  return { ...base, ...overrides };
}

export function makeState(overrides: Partial<ReplayState> = {}): ReplayState {
  return {
    t: 0,
    stepIndex: -1,
    activeNode: null,
    activeVisit: 0,
    visits: {},
    nodeStates: {},
    takenEdges: [],
    plans: [],
    claims: [],
    diffs: [],
    tests: [],
    reviews: [],
    costUsd: 0,
    toolCalls: 0,
    tokensIn: 0,
    tokensOut: 0,
    feed: [],
    stopped: null,
    finished: false,
    ...overrides,
  };
}

export function makeIndex(overrides: Partial<ReplayIndex> = {}): ReplayIndex {
  return { ...(clone(indexFixture) as ReplayIndex), ...overrides };
}
