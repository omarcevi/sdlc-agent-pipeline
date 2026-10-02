import type { ReactNode } from "react";

/** Long replay text: always a React text child, monospace, wrapped. Never a link, never HTML. */
export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <pre
      className={`font-mono text-xs break-words m-0 ${className}`}
      style={{ whiteSpace: "pre-wrap" }}
    >
      {children}
    </pre>
  );
}

/** Key/value fields of a tool argument or result: strings verbatim, other values as JSON. */
export function Fields({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return <p className="text-xs italic m-0">(empty)</p>;
  return (
    <dl className="m-0 space-y-1">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt className="text-xs font-semibold">{k}</dt>
          <dd className="m-0">
            <Mono>{typeof v === "string" ? v : JSON.stringify(v, null, 2)}</Mono>
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2" aria-label={title}>
      <h2 className="text-sm font-semibold m-0">{title}</h2>
      {children}
    </section>
  );
}
