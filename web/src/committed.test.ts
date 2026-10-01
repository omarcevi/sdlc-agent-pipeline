import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import graphsJson from "./graph/graphs.json";
import { parseGraphs, parseIndex, parseReplay } from "./replay/parse";
import { endTime, stateAt } from "./replay/state";
import type { Replay } from "./replay/types";

const graphs = parseGraphs(graphsJson);
const read = (name: string): unknown => JSON.parse(readFileSync(`public/replays/${name}`, "utf8"));
const index = parseIndex(read("index.json"));
const replays: [string, Replay][] = index.replays.map((e) => [e.file, parseReplay(read(e.file), graphs)]);

describe("committed replays", () => {
  it("parses every committed replay against the real graphs", () => {
    expect(index.replays.length).toBeGreaterThan(0);
    for (const [, replay] of replays) expect(replay.steps.length).toBeGreaterThan(0);
  });

  it("at the end the running numbers equal the outcome for every replay", () => {
    for (const [file, replay] of replays) {
      const s = stateAt(replay, graphs.graphs[replay.run.graph], endTime(replay));
      expect(s.finished, file).toBe(true);
      expect(s.costUsd, file).toBeCloseTo(replay.outcome.cost_usd, 4);
      expect(s.toolCalls, file).toBe(replay.outcome.tool_calls);
    }
  });

  it("every node and taken edge exists in its graph", () => {
    for (const [file, replay] of replays) {
      const graph = graphs.graphs[replay.run.graph];
      const ids = new Set(graph.nodes.map((n) => n.id));
      for (const step of replay.steps) expect(ids.has(step.node), `${file}: ${step.node}`).toBe(true);
      const s = stateAt(replay, graph, endTime(replay));
      for (const e of s.takenEdges) {
        expect(
          graph.edges.some((g) => g.from === e.from && g.to === e.to && g.route === e.route),
          `${file}: ${e.from} -> ${e.to}`,
        ).toBe(true);
      }
    }
  });

  it("index entries match their replay files", () => {
    for (const [i, [file, replay]] of replays.entries()) {
      const e = index.replays[i];
      expect(e.file).toBe(file);
      expect(e.run_id).toBe(replay.run.run_id);
      expect(e.task_id).toBe(replay.run.task_id);
      expect(e.system).toBe(replay.run.system);
      expect(e.outcome).toBe(replay.outcome.outcome);
      expect(e.resolved).toBe(replay.outcome.resolved);
      expect(e.cost_usd).toBe(replay.outcome.cost_usd);
      expect(e.tool_calls).toBe(replay.outcome.tool_calls);
      if (e.pair !== null) expect(index.replays.some((o) => o.run_id === e.pair)).toBe(true);
    }
  });
});
