import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { OutcomeBanner, outcomeBadge, outcomeHeadline } from "./OutcomeBanner";
import { makeIndex, makeReplay } from "../test/factories";
import type { FailureKind, IndexEntry, OutcomeName, Replay } from "../replay/types";

afterEach(cleanup);

interface Case {
  name: string;
  outcome: OutcomeName;
  failure_kind: FailureKind;
  resolved: boolean;
  reason: string;
  headline: string;
  badge: string;
}

const CASES: Case[] = [
  {
    name: "resolved patch",
    outcome: "patch_written",
    failure_kind: "none",
    resolved: true,
    reason: "",
    headline: "Patch written · resolved: the hidden tests pass",
    badge: "Patch written · resolved",
  },
  {
    name: "unresolved patch",
    outcome: "patch_written",
    failure_kind: "none",
    resolved: false,
    reason: "",
    headline: "Patch written · not resolved: the hidden tests fail",
    badge: "Patch written · not resolved",
  },
  {
    name: "correct decline",
    outcome: "declined",
    failure_kind: "none",
    resolved: true,
    reason: "",
    headline: "Declined · correct: this task is a trap",
    badge: "Declined · correct",
  },
  {
    name: "wrong decline",
    outcome: "declined",
    failure_kind: "none",
    resolved: false,
    reason: "",
    headline: "Declined · not resolved: the task could be done",
    badge: "Declined · not resolved",
  },
  {
    name: "cap",
    outcome: "failed",
    failure_kind: "budget",
    resolved: false,
    reason: "tool-call cap reached",
    headline: "Stopped by a cap · tool-call cap reached",
    badge: "Stopped by a cap",
  },
  {
    name: "failure",
    outcome: "failed",
    failure_kind: "agent",
    resolved: false,
    reason: "no diff produced",
    headline: "Failed · no diff produced",
    badge: "Failed",
  },
  {
    name: "infra",
    outcome: "failed",
    failure_kind: "infra",
    resolved: false,
    reason: "sandbox lost",
    headline: "Infrastructure failure · sandbox lost",
    badge: "Infrastructure failure",
  },
];

function replayFor(c: Case): Replay {
  const r = makeReplay();
  r.outcome = { ...r.outcome, outcome: c.outcome, failure_kind: c.failure_kind, resolved: c.resolved, reason: c.reason };
  return r;
}

describe("OutcomeBanner", () => {
  it("headline for each outcome case", () => {
    for (const c of CASES) expect(outcomeHeadline(replayFor(c)), c.name).toBe(c.headline);
  });

  it("badge for each outcome case", () => {
    for (const c of CASES) {
      const entry: IndexEntry = {
        ...makeIndex().replays[0],
        outcome: c.outcome,
        failure_kind: c.failure_kind,
        resolved: c.resolved,
      };
      expect(outcomeBadge(entry), c.name).toBe(c.badge);
    }
  });

  it("renders only an empty live region until visible", () => {
    const { rerender } = render(<OutcomeBanner replay={makeReplay()} visible={false} />);
    const region = screen.getByRole("status");
    expect(region.childElementCount).toBe(0);
    expect(region.textContent).toBe("");
    rerender(<OutcomeBanner replay={makeReplay()} visible />);
    expect(region.contains(screen.getByTestId("outcome"))).toBe(true);
  });

  it("shows the final numbers and audit flags", () => {
    const r = makeReplay();
    r.outcome = {
      ...r.outcome,
      cost_usd: 0.1234,
      tool_calls: 31,
      duration_s: 125,
      test_attempts: 3,
      review_rounds: 2,
      audit: ["edited a test file"],
    };
    render(<OutcomeBanner replay={r} visible />);
    const box = screen.getByTestId("outcome");
    expect(box.textContent).toContain("$0.1234");
    expect(box.textContent).toContain("31 tool calls");
    expect(box.textContent).toContain("02:05 wall time");
    expect(box.textContent).toContain("3 test attempts");
    expect(box.textContent).toContain("2 review rounds");
    expect(box.textContent).toContain("edited a test file");
  });
});
