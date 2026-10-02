import { describe, expect, it, vi } from "vitest";
import { loadIndex, loadReplay } from "./data";
import { ReplayError } from "./replay/parse";
import { fixtureGraphs, makeIndex, makeReplay } from "./test/factories";

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

describe("data", () => {
  it("never fetches a run id that is not in the index", async () => {
    const index = makeIndex();
    const fetchFn = vi.fn();
    const ids = [
      "../index",
      "x/../../etc",
      "https:%2F%2Fevil.example",
      "%2e%2e",
      "SYNTH-001-MULTI-FLASH-R1",
      "",
    ];
    for (const id of ids) {
      expect(await loadReplay(index, id, fixtureGraphs(), fetchFn as unknown as typeof fetch)).toBeNull();
    }
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("fetches the file named by the index entry", async () => {
    const index = makeIndex();
    const entry = index.replays[0];
    const fetchFn = vi.fn().mockResolvedValue(ok(makeReplay("multi")));
    const replay = await loadReplay(index, entry.run_id, fixtureGraphs(), fetchFn as unknown as typeof fetch);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn.mock.calls[0][0]).toBe(`replays/${entry.file}`);
    expect(replay?.run.run_id).toBe(entry.run_id);
  });

  it("refuses a replay whose run id differs from its index entry", async () => {
    const index = makeIndex();
    const entry = index.replays[0];
    const other = { ...makeReplay("multi"), run: { ...makeReplay("multi").run, run_id: "someone-else" } };
    const fetchFn = vi.fn().mockResolvedValue(ok(other));
    await expect(
      loadReplay(index, entry.run_id, fixtureGraphs(), fetchFn as unknown as typeof fetch),
    ).rejects.toThrow(/run id/);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("loads and parses the index from replays/index.json", async () => {
    const fetchFn = vi.fn().mockResolvedValue(ok(makeIndex()));
    const index = await loadIndex(fetchFn as unknown as typeof fetch);
    expect(fetchFn.mock.calls[0][0]).toBe("replays/index.json");
    expect(index.replays).toHaveLength(2);
  });

  it("reports a failed request and a newer schema", async () => {
    const bad = vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });
    await expect(loadIndex(bad as unknown as typeof fetch)).rejects.toThrow(/404/);
    const newer = vi.fn().mockResolvedValue(ok({ ...makeReplay("multi"), schema: 99 }));
    const index = makeIndex();
    await expect(
      loadReplay(index, index.replays[0].run_id, fixtureGraphs(), newer as unknown as typeof fetch),
    ).rejects.toThrow(ReplayError);
  });
});
