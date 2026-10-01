import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { GraphView, handlesFor } from "./GraphView";
import { LAYOUT } from "./layout";
import { fixtureGraphs, makeState } from "../test/factories";
import type { GraphId, NodeState } from "../replay/types";

afterEach(cleanup);

const models = { planner: "gemini-3.8-flash", coder: "gemini-3.8-pro", reviewer: "gemini-3.8-flash" };

async function show(graphId: GraphId, state = makeState(), onNodeClick = vi.fn()) {
  const graph = fixtureGraphs().graphs[graphId];
  render(
    <div style={{ width: 1200, height: 500 }}>
      <GraphView graph={graph} graphId={graphId} state={state} models={models} onNodeClick={onNodeClick} />
    </div>,
  );
  // React Flow draws edges after it has measured the nodes (the ResizeObserver mock reports them).
  for (const e of graph.edges) await screen.findByTestId(`edge-${e.from}-${e.to}`);
  return { graph, onNodeClick };
}

const dash = (el: HTMLElement) =>
  (el.querySelector("path.react-flow__edge-path") as SVGElement | null)?.style.strokeDasharray ?? null;
const attr = (el: HTMLElement, name: string) => el.getAttribute(name);

describe("GraphView", () => {
  for (const graphId of ["multi", "single"] as GraphId[]) {
    it(`renders the ${graphId} graph with every node and edge`, async () => {
      const { graph } = await show(graphId);
      for (const n of graph.nodes) expect(screen.queryByTestId(`node-${n.id}`), n.id).not.toBeNull();
      for (const e of graph.edges) {
        expect(screen.queryByTestId(`edge-${e.from}-${e.to}`), `${e.from}-${e.to}`).not.toBeNull();
      }
    });
  }

  it("shows each node state as text as well as colour", async () => {
    const states: Record<string, NodeState> = {
      planner: "done",
      coder: "active",
      run_tests: "stopped",
      deliver_patch: "end",
    };
    await show("multi", makeState({ nodeStates: states }));
    const expectState = (id: string, s: string) => {
      const el = screen.getByTestId(`node-${id}`);
      expect(attr(el, "data-state")).toBe(s);
      expect(within(el).queryByText(s)).not.toBeNull();
    };
    expectState("coder", "active");
    expectState("planner", "done");
    expectState("run_tests", "stopped");
    expectState("deliver_patch", "end");
    expectState("reviewer", "idle");
  });

  it("shows the model name on agent nodes", async () => {
    await show("multi");
    expect(within(screen.getByTestId("node-coder")).queryByText("gemini-3.8-pro")).not.toBeNull();
  });

  it("marks taken edges with their route and leaves the others dashed", async () => {
    await show(
      "multi",
      makeState({
        takenEdges: [
          { from: "route_plan", to: "coder", route: "actionable" },
          { from: "fetch_issue", to: "provision_sandbox", route: null },
        ],
      }),
    );
    const taken = screen.getByTestId("edge-route_plan-coder");
    expect(attr(taken, "data-taken")).toBe("true");
    expect(within(taken).queryByText("actionable")).not.toBeNull();
    expect(dash(taken)).toBe("");
    const idle = screen.getByTestId("edge-route_plan-report_failure");
    expect(attr(idle, "data-taken")).toBe("false");
    expect(dash(idle)).toBeTruthy();
    expect(attr(screen.getByTestId("edge-fetch_issue-provision_sandbox"), "data-taken")).toBe("true");
    expect(screen.queryByText("dashed: not taken in this run")).not.toBeNull();
  });

  it("shows the visit count from the second visit", async () => {
    await show("multi", makeState({ visits: { coder: 3, planner: 1 } }));
    expect(within(screen.getByTestId("node-coder")).queryByText("×3")).not.toBeNull();
    expect(within(screen.getByTestId("node-planner")).queryByText(/×/)).toBeNull();
  });

  it("calls back with the node id on click", async () => {
    const { onNodeClick } = await show("multi");
    fireEvent.click(screen.getByTestId("node-coder"));
    expect(onNodeClick).toHaveBeenCalledWith("coder");
  });

  it("labels START as issue", async () => {
    await show("single");
    expect(within(screen.getByTestId("node-START")).queryByText("issue")).not.toBeNull();
  });

  it("draws every edge into report_failure from the bottom handle, dropping below the main path", () => {
    for (const graphId of ["multi", "single"] as GraphId[]) {
      const edges = fixtureGraphs().graphs[graphId].edges.filter((e) => e.to === "report_failure");
      expect(edges.length).toBeGreaterThan(1);
      for (const e of edges) {
        const h = handlesFor(LAYOUT[graphId][e.from], LAYOUT[graphId].report_failure);
        expect(h, `${graphId} ${e.from}`).toEqual({ sourceHandle: "bs", targetHandle: "tt" });
      }
    }
  });

  it("keeps the fail and changes loops above the main path", () => {
    expect(handlesFor(LAYOUT.multi.run_tests, LAYOUT.multi.coder).sourceHandle).toBe("ts");
    expect(handlesFor(LAYOUT.multi.route_review, LAYOUT.multi.coder).sourceHandle).toBe("ts");
    expect(handlesFor(LAYOUT.single.run_tests, LAYOUT.single.solo).sourceHandle).toBe("ts");
  });

  it("renders START done with its edge taken (F9)", async () => {
    await show(
      "multi",
      makeState({
        nodeStates: { START: "done", fetch_issue: "active" },
        takenEdges: [{ from: "START", to: "fetch_issue", route: null }],
      }),
    );
    expect(attr(screen.getByTestId("node-START"), "data-state")).toBe("done");
    expect(attr(screen.getByTestId("edge-START-fetch_issue"), "data-taken")).toBe("true");
  });

  it("shows a node that is both stopped and the end node as stopped (F10)", async () => {
    await show("multi", makeState({ nodeStates: { coder: "stopped" } }));
    const el = screen.getByTestId("node-coder");
    expect(attr(el, "data-state")).toBe("stopped");
    expect(within(el).queryByText("end")).toBeNull();
  });
});
