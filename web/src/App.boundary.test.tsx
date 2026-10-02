import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { makeIndex, makeReplay } from "./test/factories";

vi.mock("./pages/RunPage", () => ({
  RunPage: () => {
    throw new Error("boom");
  },
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("App error boundary", () => {
  it("a run page that throws shows the message instead of a blank page", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const replay = makeReplay("multi");
    const index = makeIndex();
    const file = index.replays.find((e) => e.run_id === replay.run.run_id)!.file;
    const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
    vi.stubGlobal("fetch", async (url: string) =>
      url === "replays/index.json" ? ok(index) : url === `replays/${file}` ? ok(replay) : ({ ok: false, status: 404 } as Response),
    );
    window.history.replaceState(null, "", `#/run/${replay.run.run_id}`);
    render(<App />);
    expect(await screen.findByText("Could not show this replay")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to all replays" })).toBeTruthy();
  });
});
