import type { GraphId, PlanStep } from "../replay/types";
import { Mono, Panel } from "./Text";

export function PlanPanel({ plans, graphId }: { plans: PlanStep[]; graphId: GraphId }) {
  if (graphId === "single") {
    return (
      <Panel title="Plan">
        <p className="m-0">The single-agent baseline has no planner.</p>
      </Panel>
    );
  }
  return (
    <Panel title="Plan">
      {plans.length === 0 && <p className="m-0 italic">No plan yet.</p>}
      {plans.map((p, k) => (
        <article key={p.i} className="space-y-1">
          {plans.length > 1 && <h3 className="text-xs m-0">{`plan ${k + 1} of ${plans.length}`}</h3>}
          <Mono>{p.value.summary}</Mono>
          <p className="m-0 text-xs">{p.value.actionable ? "Actionable" : "Declined as not actionable"}</p>
          {p.value.decline_reason !== null && <Mono>{p.value.decline_reason}</Mono>}
          {p.value.files_to_inspect.length > 0 && (
            <>
              <h4 className="text-xs m-0">Files to inspect</h4>
              <ul className="m-0">
                {p.value.files_to_inspect.map((f, j) => (
                  <li key={j} className="font-mono text-xs">
                    {f}
                  </li>
                ))}
              </ul>
            </>
          )}
          {p.value.steps.length > 0 && (
            <>
              <h4 className="text-xs m-0">Steps</h4>
              <ol className="m-0">
                {p.value.steps.map((s, j) => (
                  <li key={j}>
                    <Mono>{s}</Mono>
                  </li>
                ))}
              </ol>
            </>
          )}
          <h4 className="text-xs m-0">Test strategy</h4>
          <Mono>{p.value.test_strategy}</Mono>
        </article>
      ))}
    </Panel>
  );
}
