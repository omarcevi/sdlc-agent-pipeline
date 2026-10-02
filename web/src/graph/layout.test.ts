import { describe, expect, it } from "vitest";
import graphsJson from "./graphs.json";
import { LAYOUT } from "./layout";
import type { GraphId, Graphs } from "../replay/types";

const graphs = graphsJson as unknown as Graphs;

describe("layout", () => {
  for (const id of ["multi", "single"] as GraphId[]) {
    it(`every node of the ${id} graph has a position`, () => {
      for (const node of graphs.graphs[id].nodes) {
        const p = LAYOUT[id][node.id];
        expect(p, node.id).toBeDefined();
        expect(Number.isFinite(p.x)).toBe(true);
        expect(Number.isFinite(p.y)).toBe(true);
      }
    });

    it(`the ${id} layout places nothing that the graph lacks`, () => {
      const ids = new Set(graphs.graphs[id].nodes.map((n) => n.id));
      for (const key of Object.keys(LAYOUT[id])) expect(ids.has(key), key).toBe(true);
    });

    it(`the ${id} main path runs left to right with report_failure below it`, () => {
      expect(LAYOUT[id].report_failure.y).toBeGreaterThan(LAYOUT[id].fetch_issue.y);
      expect(LAYOUT[id].START.x).toBeLessThan(LAYOUT[id].fetch_issue.x);
      expect(LAYOUT[id].fetch_issue.x).toBeLessThan(LAYOUT[id].deliver_patch.x);
    });
  }
});

describe("fitting the graph", () => {
  // React Flow's default minimum zoom (0.5) could not fit the multi-agent graph
  // (about 1,870 px wide) into the run page's graph panel (about 660 px on a
  // laptop), so `fitView` left `route_review` and `deliver_patch` off screen.
  it("every graph fits a 400 px wide panel at the minimum zoom", async () => {
    const { MIN_ZOOM } = await import("./GraphView");
    const NODE_WIDTH = 200; // generous: the widest card plus margin
    for (const id of ["multi", "single"] as GraphId[]) {
      const xs = Object.values(LAYOUT[id]).map((p) => p.x);
      const width = Math.max(...xs) - Math.min(...xs) + NODE_WIDTH;
      expect(width * MIN_ZOOM, id).toBeLessThanOrEqual(400);
    }
  });
});
