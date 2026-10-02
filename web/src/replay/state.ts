import type {
  ClaimStep,
  DiffStep,
  FeedItem,
  GraphDef,
  GraphEdge,
  NodeState,
  PlanStep,
  Replay,
  ReplayState,
  ReviewStep,
  Step,
  StopStep,
  TestsStep,
} from "./types";

/** A silence of at least this many seconds gets a "without events" line in the feed. */
export const WAIT_NOTE_S = 15;

const START = "START";
const round2 = (x: number): number => Math.round(x * 100) / 100;

function takenEdge(graph: GraphDef, from: string, to: string, via: string | null): GraphEdge | null {
  const between = graph.edges.filter((e) => e.from === from && e.to === to);
  return between.find((e) => e.route === via) ?? between[0] ?? null;
}

/**
 * The whole replay state at time `t`, from the steps with `step.t <= t` only (index order on ties).
 * Pure: seeking and playing both render from this.
 */
export function stateAt(replay: Replay, graph: GraphDef, t: number): ReplayState {
  const nodeStates: Record<string, NodeState> = {};
  for (const n of graph.nodes) nodeStates[n.id] = "idle";

  const visits: Record<string, number> = {};
  const edges = new Map<string, GraphEdge>();
  const plans: PlanStep[] = [];
  const claims: ClaimStep[] = [];
  const diffs: DiffStep[] = [];
  const tests: TestsStep[] = [];
  const reviews: ReviewStep[] = [];
  const feed: FeedItem[] = [];
  const visited = new Set<string>();
  let stepIndex = -1;
  let last: Step | null = null;
  let activeNode: string | null = null;
  let activeVisit = 0;
  let tokensIn = 0;
  let tokensOut = 0;
  let stopped: StopStep | null = null;
  let endNode: string | null = null;
  let prevT: number | null = null;

  for (let idx = 0; idx < replay.steps.length; idx++) {
    const step = replay.steps[idx];
    if (step.t > t) break;
    stepIndex = idx;
    last = step;

    if (prevT !== null && step.t - prevT >= WAIT_NOTE_S - 1e-9) {
      feed.push({ kind: "wait", step: idx, t: step.t, seconds: round2(step.t - prevT) });
    }
    prevT = step.t;

    switch (step.kind) {
      case "node": {
        activeNode = step.node;
        activeVisit = step.visit;
        visits[step.node] = Math.max(visits[step.node] ?? 0, step.visit);
        visited.add(step.node);
        // F9: a null `from` is the graph's START.
        const from = step.from ?? (graph.edges.some((e) => e.from === START && e.to === step.node) ? START : null);
        if (from !== null) {
          if (from === START) visited.add(START);
          const e = takenEdge(graph, from, step.node, step.via);
          if (e) edges.set(`${e.from}>${e.to}>${e.route}`, e);
        }
        feed.push({ kind: "separator", step: idx, t: step.t, node: step.node, visit: step.visit, via: step.via });
        break;
      }
      case "model_call":
        tokensIn += step.tokens_in;
        tokensOut += step.tokens_out;
        for (const call of step.calls) {
          feed.push({ kind: "call", step: idx, t: step.t, agent: step.agent, call });
        }
        break;
      case "tool_result":
        feed.push({ kind: "result", step: idx, t: step.t, result: step });
        break;
      case "plan":
        plans.push(step);
        break;
      case "claim":
        claims.push(step);
        break;
      case "diff":
        diffs.push(step);
        break;
      case "tests":
        tests.push(step);
        break;
      case "review":
        reviews.push(step);
        break;
      case "stop":
        stopped = step;
        feed.push({ kind: "stop", step: idx, t: step.t, text: step.text });
        break;
      case "outcome":
        endNode = step.node;
        break;
      case "other":
        feed.push({ kind: "other", step: idx, t: step.t, label: step.name });
        break;
    }
  }

  const finished = endNode !== null;
  // Precedence: stopped > end > active > done > idle (F10).
  for (const id of visited) nodeStates[id] = "done";
  if (activeNode !== null && !finished) nodeStates[activeNode] = "active";
  if (endNode !== null) nodeStates[endNode] = "end";
  if (stopped !== null) nodeStates[(stopped as StopStep).node] = "stopped";

  return {
    t,
    stepIndex,
    activeNode,
    activeVisit,
    visits,
    nodeStates,
    takenEdges: [...edges.values()],
    plans,
    claims,
    diffs,
    tests,
    reviews,
    costUsd: last?.cost_usd ?? 0,
    toolCalls: last?.tool_calls ?? 0,
    tokensIn,
    tokensOut,
    feed,
    stopped,
    finished,
  };
}

/** The outcome step's time (the last step's, if a file somehow has no outcome step). */
export function endTime(replay: Replay): number {
  const outcome = replay.steps.find((s) => s.kind === "outcome");
  return outcome?.t ?? replay.steps.at(-1)?.t ?? 0;
}

/** The first step time after `t`, or the end. */
export function nextStepTime(replay: Replay, t: number): number {
  const next = replay.steps.find((s) => s.t > t);
  return next ? Math.min(next.t, endTime(replay)) : endTime(replay);
}

/** The last step time before `t`, or 0. */
export function prevStepTime(replay: Replay, t: number): number {
  for (let i = replay.steps.length - 1; i >= 0; i--) {
    if (replay.steps[i].t < t) return replay.steps[i].t;
  }
  return 0;
}

/** The first node step of `nodeId` after `t`, else its first, else null. */
export function nextVisitTime(replay: Replay, nodeId: string, t: number): number | null {
  const own = replay.steps.filter((s) => s.kind === "node" && s.node === nodeId);
  return (own.find((s) => s.t > t) ?? own[0])?.t ?? null;
}

/** One tick per node step, for the scrubber. */
export function nodeTicks(replay: Replay): { t: number; node: string }[] {
  return replay.steps.filter((s) => s.kind === "node").map((s) => ({ t: s.t, node: s.node }));
}
