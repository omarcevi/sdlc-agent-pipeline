export type Route =
  | { name: "list" }
  | { name: "run"; runId: string; t: number | null }
  | { name: "notFound" };

const RUN_ID = /^[A-Za-z0-9-]+$/;
const NUMBER = /^\d+(\.\d+)?$/;

export function parseHash(hash: string): Route {
  if (hash === "" || hash === "#" || hash === "#/") return { name: "list" };
  const m = /^#\/run\/([^?]*)(?:\?(.*))?$/.exec(hash);
  if (!m || !RUN_ID.test(m[1])) return { name: "notFound" };
  const runId = m[1];
  if (m[2] === undefined) return { name: "run", runId, t: null };
  const q = /^t=(.*)$/.exec(m[2]);
  if (!q || !NUMBER.test(q[1])) return { name: "notFound" };
  const t = Number(q[1]);
  if (!Number.isFinite(t) || t < 0) return { name: "notFound" };
  return { name: "run", runId, t };
}

export function runHash(runId: string, t?: number): string {
  return t === undefined ? `#/run/${runId}` : `#/run/${runId}?t=${t.toFixed(2)}`;
}
