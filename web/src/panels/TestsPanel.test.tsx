import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { TestsPanel } from "./TestsPanel";
import type { TestsStep } from "../replay/types";

afterEach(cleanup);

const tests: TestsStep = {
  i: 1,
  t: 1,
  node: "tests",
  visit: 1,
  cost_usd: 0,
  tool_calls: 0,
  kind: "tests",
  value: {
    passed: false,
    exit_code: 1,
    failed_tests: ["tests/test_a.py::test_x"],
    output_tail: "1 failed, 4 passed",
    duration_s: 2.5,
  },
};

describe("TestsPanel", () => {
  it("shows exit code, failed tests and the visible-tests note", () => {
    render(<TestsPanel tests={[tests]} />);
    expect(screen.getByText(/exit code 1/)).toBeTruthy();
    expect(screen.getByText("✗ tests/test_a.py::test_x")).toBeTruthy();
    expect(screen.getByText("1 failed, 4 passed")).toBeTruthy();
    expect(
      screen.getByText("Visible tests only. The hidden tests ran after the run, when it was scored."),
    ).toBeTruthy();
  });
});
