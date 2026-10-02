import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { CostMeter } from "./CostMeter";
import { makeState } from "../test/factories";

afterEach(cleanup);

const caps = { cost_usd: 1, tool_calls: 75, wall_clock_s: 3000 };

describe("CostMeter", () => {
  it("bars against the caps change colour past 80 percent", () => {
    const { rerender } = render(
      <CostMeter state={makeState({ costUsd: 0.5, toolCalls: 10, tokensIn: 1200, tokensOut: 30 })} caps={caps} t={100} />,
    );
    for (const name of ["Cost", "Tool calls", "Elapsed time"])
      expect(screen.getByRole("progressbar", { name }).getAttribute("data-level")).toBe("ok");
    expect(screen.getByText("$0.5000 of $1.00")).toBeTruthy();
    expect(screen.getByText("10 of 75")).toBeTruthy();
    expect(screen.getByText("tokens in 1,200 · out 30")).toBeTruthy();

    rerender(<CostMeter state={makeState({ costUsd: 0.81, toolCalls: 61 })} caps={caps} t={2500} />);
    for (const name of ["Cost", "Tool calls", "Elapsed time"])
      expect(screen.getByRole("progressbar", { name }).getAttribute("data-level")).toBe("warn");

    rerender(<CostMeter state={makeState({ costUsd: 0.8, toolCalls: 60 })} caps={caps} t={100} />);
    expect(screen.getByRole("progressbar", { name: "Cost" }).getAttribute("data-level")).toBe("ok");
    expect(screen.getByRole("progressbar", { name: "Tool calls" }).getAttribute("data-level")).toBe("ok");
  });
});
