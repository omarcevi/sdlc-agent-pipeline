import { parseIndex, parseReplay } from "./replay/parse";
import type { Graphs, Replay, ReplayIndex } from "./replay/types";

async function getJson(path: string, fetchFn: typeof fetch): Promise<unknown> {
  const response = await fetchFn(path);
  if (!response.ok) throw new Error(`request failed: ${path} (${response.status})`);
  return response.json();
}

export async function loadIndex(fetchFn: typeof fetch = fetch): Promise<ReplayIndex> {
  return parseIndex(await getJson("replays/index.json", fetchFn));
}

/**
 * The replay for `runId`, or null (with no request) when the id is not an index entry.
 * The id is only compared with the index; the path fetched is always the entry's own `file`.
 */
export async function loadReplay(
  index: ReplayIndex,
  runId: string,
  graphs: Graphs,
  fetchFn: typeof fetch = fetch,
): Promise<Replay | null> {
  const entry = index.replays.find((e) => e.run_id === runId);
  if (!entry) return null;
  return parseReplay(await getJson(`replays/${entry.file}`, fetchFn), graphs);
}
