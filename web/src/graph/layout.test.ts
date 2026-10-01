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
