import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ClaimPanel } from "./ClaimPanel";
import type { ClaimStep } from "../replay/types";

afterEach(cleanup);

const claim: ClaimStep = {
  i: 1,
  t: 1,
  node: "coder",
  visit: 1,
  cost_usd: 0,
  tool_calls: 0,
  kind: "claim",
  agent: "coder",
  value: { summary: "Fixed it", files_changed: ["src/p.py"], tests_added: ["tests/test_p.py"], notes: "none" },
};

describe("ClaimPanel", () => {
  it("labels the claim as the agent's own account", () => {
    render(<ClaimPanel claims={[claim]} />);
    expect(
      screen.getByText(
        "The agent's own account. No route uses it; the diff and the test run are what the pipeline checks.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Fixed it")).toBeTruthy();
    expect(screen.getByText("test: tests/test_p.py")).toBeTruthy();
  });

  it("shows a solo decline", () => {
    const solo: ClaimStep = {
      ...claim,
      agent: "solo",
      value: { declined: true, decline_reason: "this is a trap", summary: "No change", files_changed: [] },
    };
    render(<ClaimPanel claims={[solo]} />);
    expect(screen.getByText("this is a trap")).toBeTruthy();
  });
});
