import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { RunList } from "./RunList";
import { makeIndex } from "../test/factories";

afterEach(cleanup);

describe("RunList", () => {
  it("lists replays in index order with outcome, cost, time and tool calls", () => {
    const index = makeIndex();
    render(<RunList index={index} />);
    expect(screen.getByText(index.note)).toBeTruthy();
    const cards = screen.getAllByRole("listitem");
    expect(cards).toHaveLength(2);
    const first = within(cards[0]);
    expect(first.getByText(/Multi-agent run that resolves/)).toBeTruthy();
    expect(first.getByText(/synth-001/)).toBeTruthy();
    expect(first.getByText(/Parser drops the last item/)).toBeTruthy();
    expect(first.getByText("three agents")).toBeTruthy();
    expect(first.getByText("Patch written · resolved")).toBeTruthy();
    expect(first.getByText(/\$0\.01/)).toBeTruthy();
    expect(first.getByText(/0:41/)).toBeTruthy();
    expect(first.getByText(/2 tool calls/)).toBeTruthy();
    expect(first.getByText(/2026-10-01/)).toBeTruthy();
    const second = within(cards[1]);
    expect(second.getByText("single agent")).toBeTruthy();
    expect(second.getByText("Failed")).toBeTruthy();
    expect(second.getByText(/0:20/)).toBeTruthy();
  });

  it("pairs link to each other", () => {
    render(<RunList index={makeIndex()} />);
    const links = screen.getAllByRole("link", { name: "compare" });
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "#/run/synth-001-single-flash-r1",
      "#/run/synth-001-multi-flash-r1",
    ]);
  });

  it("links each card to its run and omits compare without a pair", () => {
    const index = makeIndex();
    index.replays[1] = { ...index.replays[1], pair: null };
    render(<RunList index={index} />);
    expect(screen.getAllByRole("link", { name: "compare" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: /Single-agent baseline/ }).getAttribute("href")).toBe(
      "#/run/synth-001-single-flash-r1",
    );
  });
});
