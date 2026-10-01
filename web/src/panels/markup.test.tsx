import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Feed } from "./Feed";
import { IssuePanel } from "./IssuePanel";
import { PlanPanel } from "./PlanPanel";
import { ClaimPanel } from "./ClaimPanel";
import { DiffPanel } from "./DiffPanel";
import { TestsPanel } from "./TestsPanel";
import { ReviewPanel } from "./ReviewPanel";
import { OutcomeBanner } from "./OutcomeBanner";
import { makeReplay } from "../test/factories";
import type { ClaimStep, DiffStep, FeedItem, PlanStep, ReviewStep, TestsStep } from "../replay/types";

afterEach(cleanup);

const HOSTILE = "<img src=x onerror=alert(1)> <script>alert(1)</script> [x](javascript:alert(1))";
const base = { i: 1, t: 1, node: "n", visit: 1, cost_usd: 0, tool_calls: 0 };

function assertPlain(container: HTMLElement, expected: string[]) {
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("a")).toBeNull();
  for (const e of expected) expect(container.textContent).toContain(e);
}

describe("replay text is plain text", () => {
  it("renders markup in replay text as plain text", () => {
    const feed: FeedItem[] = [
      {
        kind: "call",
        step: 1,
        t: 1,
        agent: "coder",
        call: { id: "c", tool: "run", label: "run", args: { cmd: HOSTILE } },
      },
      {
        kind: "result",
        step: 2,
        t: 2,
        result: { ...base, kind: "tool_result", call: "c", tool: "run", error: null, result: { out: HOSTILE } },
      },
    ];
    const plan: PlanStep = {
      ...base,
      kind: "plan",
      value: {
        actionable: false,
        decline_reason: HOSTILE,
        summary: HOSTILE,
        files_to_inspect: [],
        steps: [],
        test_strategy: HOSTILE,
      },
    };
    const claim: ClaimStep = {
      ...base,
      kind: "claim",
      agent: "coder",
      value: { summary: HOSTILE, files_changed: [], tests_added: [], notes: "" },
    };
    const diff: DiffStep = {
      ...base,
      kind: "diff",
      value: {
        files: ["a.txt"],
        insertions: 1,
        deletions: 0,
        cut: false,
        unified_diff: `diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,2 @@\n keep\n+${HOSTILE}\n`,
      },
    };
    const tests: TestsStep = {
      ...base,
      kind: "tests",
      value: { passed: false, exit_code: 1, failed_tests: [HOSTILE], output_tail: HOSTILE, duration_s: 1 },
    };
    const review: ReviewStep = {
      ...base,
      kind: "review",
      value: {
        verdict: "request_changes",
        comments: [{ file: "a.txt", line: 1, severity: "nit", issue: HOSTILE }],
        must_fix: [HOSTILE],
      },
    };
    const replay = makeReplay();
    replay.outcome = { ...replay.outcome, audit: [HOSTILE] };

    const { container } = render(
      <div>
        <IssuePanel issue={{ title: HOSTILE, body: HOSTILE }} />
        <Feed items={feed} follow onFollowChange={() => {}} onSeek={() => {}} />
        <PlanPanel plans={[plan]} graphId="multi" />
        <ClaimPanel claims={[claim]} />
        <DiffPanel diffs={[diff]} />
        <TestsPanel tests={[tests]} />
        <ReviewPanel reviews={[review]} graphId="multi" />
        <OutcomeBanner replay={replay} visible />
      </div>,
    );
    for (const b of screen.getAllByRole("button", { name: /^Show / })) fireEvent.click(b);

    assertPlain(container, [HOSTILE]);
    // every place the string was supplied shows it verbatim
    const occurrences = container.textContent!.split(HOSTILE).length - 1;
    expect(occurrences).toBeGreaterThanOrEqual(14);
  });

  it("renders a declined plan reason verbatim", () => {
    const plan: PlanStep = {
      ...base,
      kind: "plan",
      value: { actionable: false, decline_reason: HOSTILE, summary: "s", files_to_inspect: [], steps: [], test_strategy: "t" },
    };
    const { container } = render(<PlanPanel plans={[plan]} graphId="multi" />);
    assertPlain(container, [HOSTILE]);
  });
});
