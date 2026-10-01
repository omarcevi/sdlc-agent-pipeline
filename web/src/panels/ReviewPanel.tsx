import type { GraphId, ReviewStep } from "../replay/types";
import { Mono, Panel } from "./Text";

export function ReviewPanel({ reviews, graphId }: { reviews: ReviewStep[]; graphId: GraphId }) {
  if (graphId === "single") {
    return (
      <Panel title="Review">
        <p className="m-0">The single-agent baseline has no reviewer.</p>
      </Panel>
    );
  }
  return (
    <Panel title="Review">
      {reviews.length === 0 && <p className="m-0 italic">No review yet.</p>}
      {reviews.map((r, k) => (
        <article key={r.i} className="space-y-1">
          <h3 className="text-xs m-0">{`round ${k + 1}: ${r.value.verdict === "approve" ? "approve" : "request changes"}`}</h3>
          {r.value.comments.map((c, j) => (
            <div key={j}>
              <p className="m-0 text-xs font-mono">{`${c.severity} · ${c.file}${c.line !== null ? `:${c.line}` : ""}`}</p>
              <Mono>{c.issue}</Mono>
            </div>
          ))}
          {r.value.must_fix.length > 0 && (
            <>
              <h4 className="text-xs m-0">Must fix</h4>
              <ul className="m-0">
                {r.value.must_fix.map((m, j) => (
                  <li key={j}>
                    <Mono>{m}</Mono>
                  </li>
                ))}
              </ul>
            </>
          )}
        </article>
      ))}
    </Panel>
  );
}
