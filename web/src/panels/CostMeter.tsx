import type { Caps, ReplayState } from "../replay/types";
import { fmtClock } from "./format";

function Bar({ label, text, value, cap }: { label: string; text: string; value: number; cap: number }) {
  const ratio = cap > 0 ? value / cap : 0;
  const warn = ratio > 0.8;
  const pct = Math.min(100, Math.max(0, ratio * 100));
  return (
    <div>
      <div className="flex justify-between text-xs">
        <span>{label}</span>
        <span>{text}</span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={cap}
        aria-valuenow={Math.min(value, cap)}
        data-level={warn ? "warn" : "ok"}
        className="h-2 rounded bg-gray-200"
      >
        <div className={`h-2 rounded ${warn ? "bg-amber-500" : "bg-sky-600"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function CostMeter({ state, caps, t }: { state: ReplayState; caps: Caps; t: number }) {
  return (
    <section className="space-y-2" aria-label="Cost meter">
      <h2 className="text-sm font-semibold m-0">Budget</h2>
      <Bar
        label="Cost"
        text={`$${state.costUsd.toFixed(4)} of $${caps.cost_usd.toFixed(2)}`}
        value={state.costUsd}
        cap={caps.cost_usd}
      />
      <Bar
        label="Tool calls"
        text={`${state.toolCalls} of ${caps.tool_calls}`}
        value={state.toolCalls}
        cap={caps.tool_calls}
      />
      <Bar
        label="Elapsed time"
        text={`${fmtClock(t)} of ${fmtClock(caps.wall_clock_s)}`}
        value={t}
        cap={caps.wall_clock_s}
      />
      <p className="m-0 text-xs">{`tokens in ${state.tokensIn.toLocaleString("en-US")} · out ${state.tokensOut.toLocaleString("en-US")}`}</p>
    </section>
  );
}
