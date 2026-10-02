import { outcomeBadge } from "../panels/OutcomeBanner";
import { runHash } from "../routes";
import type { GraphId, ReplayIndex } from "../replay/types";

export const SYSTEM_LABEL: Record<GraphId, string> = { multi: "three agents", single: "single agent" };

/** m:ss for a wall time in seconds. */
export function fmtWall(s: number): string {
  const whole = Math.max(0, Math.floor(s));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function RunList({ index }: { index: ReplayIndex }) {
  return (
    <main className="mx-auto max-w-3xl space-y-4 p-6">
      <h1 className="text-xl font-semibold">Agent run replays</h1>
      <p className="m-0 text-sm">{index.note}</p>
      <ul className="m-0 list-none space-y-3 p-0">
        {index.replays.map((e) => (
          <li key={e.run_id} className="space-y-1 rounded border p-3">
            <a className="font-medium underline" href={runHash(e.run_id)}>
              {e.caption}
            </a>
            <p className="m-0 text-sm">{`${e.task_id} · ${e.issue_title}`}</p>
            <p className="m-0 text-sm">
              <span>{SYSTEM_LABEL[e.system]}</span> · <span>{outcomeBadge(e)}</span>
            </p>
            <p className="m-0 text-sm">
              {`$${e.cost_usd.toFixed(2)} · ${fmtWall(e.duration_s)} · ${e.tool_calls} tool calls · ${e.recorded_at.slice(0, 10)}`}
            </p>
            {e.pair !== null && (
              <a className="text-sm underline" href={runHash(e.pair)}>
                compare
              </a>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
