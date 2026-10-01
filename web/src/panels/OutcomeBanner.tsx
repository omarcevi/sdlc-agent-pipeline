import type { FailureKind, IndexEntry, OutcomeName, Replay } from "../replay/types";
import { Mono } from "./Text";
import { fmtClock } from "./format";

type Kind = "written" | "declined" | "cap" | "infra" | "failed";

function classify(outcome: OutcomeName, failure: FailureKind): Kind {
  if (outcome === "patch_written" || outcome === "pr_opened") return "written";
  if (outcome === "declined") return "declined";
  if (failure === "budget") return "cap";
  if (failure === "infra") return "infra";
  return "failed";
}

export function outcomeHeadline(replay: Replay): string {
  const o = replay.outcome;
  switch (classify(o.outcome, o.failure_kind)) {
    case "written":
      return o.resolved
        ? "Patch written · resolved: the hidden tests pass"
        : "Patch written · not resolved: the hidden tests fail";
    case "declined":
      return o.resolved
        ? "Declined · correct: this task is a trap"
        : "Declined · not resolved: the task could be done";
    case "cap":
      return `Stopped by a cap · ${o.reason}`;
    case "infra":
      return `Infrastructure failure · ${o.reason}`;
    default:
      return `Failed · ${o.reason}`;
  }
}

export function outcomeBadge(entry: IndexEntry): string {
  switch (classify(entry.outcome, entry.failure_kind)) {
    case "written":
      return entry.resolved ? "Patch written · resolved" : "Patch written · not resolved";
    case "declined":
      return entry.resolved ? "Declined · correct" : "Declined · not resolved";
    case "cap":
      return "Stopped by a cap";
    case "infra":
      return "Infrastructure failure";
    default:
      return "Failed";
  }
}

/** Renders nothing until `visible` (F16), so a hidden banner can never satisfy a test. */
export function OutcomeBanner({ replay, visible }: { replay: Replay; visible: boolean }) {
  if (!visible) return null;
  const o = replay.outcome;
  return (
    <section data-testid="outcome" className="rounded border p-3 space-y-1" aria-label="Outcome">
      <h2 className="text-base font-semibold m-0">{outcomeHeadline(replay)}</h2>
      <p className="m-0 text-sm">
        {`cost $${o.cost_usd.toFixed(4)} · ${o.tool_calls} tool calls · ${fmtClock(o.duration_s)} wall time · ${o.test_attempts} test attempts · ${o.review_rounds} review rounds`}
      </p>
      <p className="m-0 text-sm">Audit flags</p>
      {o.audit.length === 0 ? (
        <p className="m-0 text-sm">none</p>
      ) : (
        <ul className="m-0">
          {o.audit.map((a, i) => (
            <li key={i}>
              <Mono>{a}</Mono>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
