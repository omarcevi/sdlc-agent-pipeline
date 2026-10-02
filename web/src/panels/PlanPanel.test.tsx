import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { PlanPanel } from "./PlanPanel";
import type { PlanStep } from "../replay/types";

afterEach(cleanup);

const plan: PlanStep = {
  i: 1,
  t: 1,
  node: "planner",
  visit: 1,
  cost_usd: 0,
  tool_calls: 0,
  kind: "plan",
  value: {
    actionable: true,
    decline_reason: null,
    summary: "Fix the parser",
    files_to_inspect: ["src/p.py"],
    steps: ["read it", "fix it"],
    test_strategy: "run the suite",
  },
};

describe("PlanPanel", () => {
  it("single-agent graph shows the fixed text", () => {
    render(<PlanPanel plans={[]} graphId="single" />);
    expect(screen.getByText("The single-agent baseline has no planner.")).toBeTruthy();
  });

  it("shows the plan on the multi graph", () => {
    render(<PlanPanel plans={[plan]} graphId="multi" />);
    expect(screen.getByText("Fix the parser")).toBeTruthy();
    expect(screen.getByText("src/p.py")).toBeTruthy();
    expect(screen.getByText("fix it")).toBeTruthy();
    expect(screen.getByText("run the suite")).toBeTruthy();
  });
});
