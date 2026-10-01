import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { makeIndex, makeReplay } from "./test/factories";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

function serve(files: Record<string, unknown>) {
  const spy = vi.fn(async (url: string) => {
    if (url in files) return ok(files[url]);
    return { ok: false, status: 404, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}
const goto = (hash: string) => window.history.replaceState(null, "", hash || "#");

describe("App", () => {
  beforeEach(() => goto("#"));

  it("lists the replays at the root", async () => {
    serve({ "replays/index.json": makeIndex() });
    render(<App />);
    expect(await screen.findAllByRole("listitem")).toHaveLength(2);
  });

  it("shows not found for a run that is not in the index", async () => {
    const spy = serve({ "replays/index.json": makeIndex() });
    goto("#/run/nope-123");
    render(<App />);
    expect(await screen.findByText("Replay not found")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to all replays" })).toBeTruthy();
    expect(spy.mock.calls.map((c) => c[0])).toEqual(["replays/index.json"]);
  });

  it("shows not found for a hash that is not a route", async () => {
    serve({ "replays/index.json": makeIndex() });
    goto("#/elsewhere");
    render(<App />);
    expect(await screen.findByText("Replay not found")).toBeTruthy();
  });

  it("shows the newer-viewer message for an unsupported schema", async () => {
    const index = makeIndex();
    serve({
      "replays/index.json": index,
      [`replays/${index.replays[0].file}`]: { ...makeReplay("multi"), schema: 99 },
    });
    goto(`#/run/${index.replays[0].run_id}`);
    render(<App />);
    expect(await screen.findByText("This replay needs a newer viewer")).toBeTruthy();
  });

  it("opens a run from the index entry's file and follows hash changes", async () => {
    const index = makeIndex();
    const spy = serve({
      "replays/index.json": index,
      [`replays/${index.replays[0].file}`]: makeReplay("multi"),
      [`replays/${index.replays[1].file}`]: makeReplay("single"),
    });
    goto(`#/run/${index.replays[0].run_id}?t=16.5`);
    render(<App />);
    expect(await screen.findByText("00:16 / 00:41")).toBeTruthy();
    expect(screen.getByText("three agents")).toBeTruthy();
    act(() => {
      window.location.hash = `#/run/${index.replays[1].run_id}`;
    });
    await waitFor(() => expect(screen.getByText("single agent")).toBeTruthy());
    // the player state is reset for the new run
    expect(screen.getByText("00:00 / 00:20")).toBeTruthy();
    expect(spy.mock.calls.map((c) => c[0])).toEqual([
      "replays/index.json",
      `replays/${index.replays[0].file}`,
      `replays/${index.replays[1].file}`,
    ]);
  });

  it("says so when the index cannot be loaded", async () => {
    serve({});
    render(<App />);
    expect(await screen.findByText("Could not load the replays")).toBeTruthy();
  });
});
