import type { RunInfo } from "../replay/types";
import { Mono } from "./Text";

export function IssuePanel({ issue }: { issue: RunInfo["issue"] }) {
  return (
    <section className="space-y-2" aria-label="Issue">
      <h2 className="text-sm font-semibold m-0">What the agents were given</h2>
      <h3 className="text-base m-0">{issue.title}</h3>
      <Mono>{issue.body}</Mono>
    </section>
  );
}
