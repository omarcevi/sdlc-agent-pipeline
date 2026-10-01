import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { RunPage } from "./RunPage";
import { fixtureGraphs, makeIndex, makeReplay } from "../test/factories";

afterEach(cleanup);
beforeEach(() => window.history.replaceState(null, "", "#"));

function show(which: "multi" | "single" = "multi", startT: number | null = null) {
  const replay = makeReplay(which);
  const entry = makeIndex().replays.find((e) => e.run_id === replay.run.run_id)!;
  return render(<RunPage entry={entry} replay={replay} graphs={fixtureGraphs()} startT={startT} />);
}
const key = (k: string) => fireEvent.keyDown(document.body, { key: k });
const MULTI = "#/run/synth-001-multi-flash-r1";

describe("RunPage", () => {
  it("opens paused at t from the hash", () => {
    show("multi", 16.5);
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    expect(screen.getByText("00:16 / 00:41")).toBeTruthy();
  });

  it("writes t to the hash when paused", () => {
    show("multi", 5);
    expect(window.location.hash).toBe(`${MULTI}?t=5.00`);
    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(window.location.hash).toBe(`${MULTI}?t=5.10`);
  });

  it("shows the header with system, models, prompt version and caption", () => {
    show("multi");
    expect(screen.getByText("three agents")).toBeTruthy();
    expect(screen.getByText(/prompt 2c/)).toBeTruthy();
    expect(screen.getByText(/Multi-agent run that resolves/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "compare" }).getAttribute("href")).toBe(
      "#/run/synth-001-single-flash-r1",
    );
    expect(screen.getByText("How to read this")).toBeTruthy();
  });

  it("a single-agent replay shows the no-planner and no-reviewer texts", () => {
    show("single");
    expect(screen.getByText("single agent")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Plan" }));
    expect(screen.getByText("The single-agent baseline has no planner.")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Review" }));
    expect(screen.getByText("The single-agent baseline has no reviewer.")).toBeTruthy();
  });

  it("has the six tabs", () => {
    show("multi");
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Issue",
      "Plan",
      "Agent summary",
      "Diff",
      "Tests",
      "Review",
    ]);
  });

  it("clicking a graph node seeks to its next visit", async () => {
    show("multi", 0);
    fireEvent.click(await screen.findByTestId("node-coder"));
    expect(window.location.hash).toBe(`${MULTI}?t=5.10`);
  });

  it("keyboard controls play, step and jump", () => {
    show("multi", 0);
    key(" ");
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
    key(" ");
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    key("ArrowRight");
    expect(window.location.hash).toBe(`${MULTI}?t=0.40`);
    key("ArrowLeft");
    expect(window.location.hash).toBe(`${MULTI}?t=0.00`);
    key("End");
    expect(window.location.hash).toBe(`${MULTI}?t=41.50`);
    key("Home");
    expect(window.location.hash).toBe(`${MULTI}?t=0.00`);
  });

  it("ignores keys while focus is in a form control", () => {
    show("multi", 0);
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Speed" }), { key: "End" });
    expect(screen.getByText("00:00 / 00:41")).toBeTruthy();
  });

  it("Space on a focused summary, link or role=button does not toggle play", () => {
    show("multi", 0);
    for (const el of [
      document.querySelector("summary")!,
      screen.getAllByRole("link")[0],
    ]) {
      fireEvent.keyDown(el, { key: " " });
      expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    }
    const fake = document.createElement("span");
    fake.setAttribute("role", "button");
    document.body.appendChild(fake);
    fireEvent.keyDown(fake, { key: " " });
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    fake.remove();
  });

  it("ignores an auto-repeated Space", () => {
    show("multi", 0);
    fireEvent.keyDown(document.body, { key: " ", repeat: true });
    expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
    fireEvent.keyDown(document.body, { key: " " });
    expect(screen.getByRole("button", { name: "Pause" })).toBeTruthy();
  });

  it("shows the outcome banner only at the end", () => {
    show("multi", 16.5);
    expect(screen.queryByTestId("outcome")).toBeNull();
    key("End");
    expect(screen.getByTestId("outcome")).toBeTruthy();
    key("Home");
    expect(screen.queryByTestId("outcome")).toBeNull();
  });

  it("clamps a startT past the end and offers the six speeds", () => {
    show("multi", 9999);
    expect(screen.getByText("00:41 / 00:41")).toBeTruthy();
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "1×",
      "2×",
      "5×",
      "10×",
      "25×",
      "50×",
    ]);
  });
});
