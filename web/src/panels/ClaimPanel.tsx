import type { ClaimStep } from "../replay/types";
import { Mono, Panel } from "./Text";

export function ClaimPanel({ claims }: { claims: ClaimStep[] }) {
  return (
    <Panel title="Agent summary">
      <p className="m-0 text-xs">
        The agent's own account. No route uses it; the diff and the test run are what the pipeline checks.
      </p>
      {claims.map((c) => {
        const v = c.value;
        return (
          <article key={c.i} className="space-y-1">
            <h3 className="text-xs m-0">{c.agent}</h3>
            {"declined" in v && v.declined && <p className="m-0 text-xs">Declined</p>}
            {"declined" in v && v.decline_reason !== null && <Mono>{v.decline_reason}</Mono>}
            <Mono>{v.summary}</Mono>
            {v.files_changed.length > 0 && (
              <ul className="m-0">
                {v.files_changed.map((f, j) => (
                  <li key={j} className="font-mono text-xs">
                    {f}
                  </li>
                ))}
              </ul>
            )}
            {"tests_added" in v && v.tests_added.length > 0 && (
              <ul className="m-0">
                {v.tests_added.map((f, j) => (
                  <li key={j} className="font-mono text-xs">{`test: ${f}`}</li>
                ))}
              </ul>
            )}
            {"notes" in v && v.notes !== "" && <Mono>{v.notes}</Mono>}
          </article>
        );
      })}
    </Panel>
  );
}
