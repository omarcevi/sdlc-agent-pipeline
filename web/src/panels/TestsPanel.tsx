import type { TestsStep } from "../replay/types";
import { Mono, Panel } from "./Text";

export function TestsPanel({ tests }: { tests: TestsStep[] }) {
  return (
    <Panel title="Tests">
      <p className="m-0 text-xs">Visible tests only. The hidden tests ran after the run, when it was scored.</p>
      {tests.map((s, k) => (
        <article key={s.i} className="space-y-1">
          <h3 className="text-xs m-0">
            {`run ${k + 1}: ${s.value.passed ? "passed" : "failed"} · exit code ${s.value.exit_code} · ${s.value.duration_s.toFixed(1)} s`}
          </h3>
          {s.value.failed_tests.length > 0 && (
            <ul className="m-0">
              {s.value.failed_tests.map((f, j) => (
                <li key={j} className="font-mono text-xs">{`✗ ${f}`}</li>
              ))}
            </ul>
          )}
          <Mono>{s.value.output_tail}</Mono>
        </article>
      ))}
    </Panel>
  );
}
