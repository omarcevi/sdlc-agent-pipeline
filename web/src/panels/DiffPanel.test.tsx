import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DiffPanel } from "./DiffPanel";
import type { DiffStep } from "../replay/types";

afterEach(cleanup);

const UNIFIED = [
  "diff --git a/src/a.py b/src/a.py",
  "index 111..222 100644",
  "--- a/src/a.py",
  "+++ b/src/a.py",
  "@@ -1,3 +1,4 @@",
  " keep",
  "-old line",
  "+new line",
  "+another new line",
  " tail",
  "diff --git a/src/b.py b/src/b.py",
  "index 333..444 100644",
  "--- a/src/b.py",
  "+++ b/src/b.py",
  "@@ -1,2 +1,1 @@",
  " same",
  "-gone",
  "",
].join("\n");

const step = (i: number, over: Partial<DiffStep["value"]> = {}): DiffStep => ({
  i,
  t: i,
  node: "diff",
  visit: 1,
  cost_usd: 0,
  tool_calls: 0,
  kind: "diff",
  value: {
    files: ["src/a.py", "src/b.py"],
    insertions: 2,
    deletions: 2,
    unified_diff: UNIFIED,
    cut: false,
    ...over,
  },
});

describe("DiffPanel", () => {
  it("renders file headers and line counts from a fixture", () => {
    render(<DiffPanel diffs={[step(1)]} />);
    const a = screen.getByText(/^src\/a\.py/, { selector: "[data-file]" });
    expect(a.textContent).toBe("src/a.py +2 −1");
    const b = screen.getByText(/^src\/b\.py/, { selector: "[data-file]" });
    expect(b.textContent).toBe("src/b.py +0 −1");
    expect(screen.getByText("new line")).toBeTruthy();
    expect(screen.queryByText(/This diff was shortened/)).toBeNull();
  });

  it("notes a cut diff", () => {
    const marker = "\n[... 40,000 characters cut ...]\n";
    render(<DiffPanel diffs={[step(1, { cut: true, unified_diff: UNIFIED + marker.slice(1) })]} />);
    expect(
      screen.getByText("This diff was shortened for the page; the counts are those of the full diff."),
    ).toBeTruthy();
    expect(screen.getByText(/40,000 characters cut/)).toBeTruthy();
    expect(screen.getByText("new line")).toBeTruthy();
  });

  it("falls back to plain text when the diff cannot be parsed", () => {
    render(<DiffPanel diffs={[step(1, { unified_diff: "not a diff at all <b>x</b>" })]} />);
    expect(screen.getByText("not a diff at all <b>x</b>")).toBeTruthy();
  });

  it("switches between the diffs of a loop", () => {
    const second = step(2, {
      unified_diff: UNIFIED.replace("new line", "second round line"),
    });
    render(<DiffPanel diffs={[step(1), second]} />);
    expect(screen.getByText("diff 2 of 2")).toBeTruthy();
    expect(screen.getByText("second round line")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Previous diff" }));
    expect(screen.getByText("diff 1 of 2")).toBeTruthy();
    expect(screen.getByText("new line")).toBeTruthy();
    expect(screen.queryByText("second round line")).toBeNull();
  });
});
