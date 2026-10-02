import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ReviewPanel } from "./ReviewPanel";
import type { ReviewStep } from "../replay/types";

afterEach(cleanup);

const review: ReviewStep = {
  i: 1,
  t: 1,
  node: "reviewer",
  visit: 1,
  cost_usd: 0,
  tool_calls: 0,
  kind: "review",
  value: {
    verdict: "request_changes",
    comments: [{ file: "src/p.py", line: 12, severity: "major", issue: "off by one" }],
    must_fix: ["handle empty input"],
  },
};

describe("ReviewPanel", () => {
  it("single-agent graph shows the fixed text", () => {
    render(<ReviewPanel reviews={[]} graphId="single" />);
    expect(screen.getByText("The single-agent baseline has no reviewer.")).toBeTruthy();
  });

  it("shows verdict, comments and must-fix items", () => {
    render(<ReviewPanel reviews={[review]} graphId="multi" />);
    expect(screen.getByText("round 1: request changes")).toBeTruthy();
    expect(screen.getByText("major · src/p.py:12")).toBeTruthy();
    expect(screen.getByText("off by one")).toBeTruthy();
    expect(screen.getByText("handle empty input")).toBeTruthy();
  });
});
