import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fixtureGraphs, makeReplay } from "../test/factories";
import { advance, DEFAULT_SPEED, MAX_WAIT_S, SPEEDS } from "./clock";
import { endTime } from "./state";
import { usePlayer } from "./usePlayer";

const base = { speed: 10, skipWaits: false, times: [0, 5, 100, 101], end: 101 };

describe("advance", () => {
  it("exposes the speeds", () => {
    expect([...SPEEDS]).toEqual([1, 2, 5, 10, 25, 50]);
    expect(DEFAULT_SPEED).toBe(10);
    expect(MAX_WAIT_S).toBe(1.5);
  });

  it("advances by real time times speed", () => {
    expect(advance(0, 0.5, base)).toBeCloseTo(5);
    expect(advance(2, 1, { ...base, speed: 2 })).toBeCloseTo(4);
  });

  it("a long wait lasts at most 1.5 s when skipping", () => {
    const o = { ...base, skipWaits: true };
    // 5 -> 100 is a 95 s gap; at 10x it would take 9.5 s, so it runs at 95/1.5 per second.
    expect(advance(5, 0.75, o)).toBeCloseTo(52.5);
    expect(advance(5, 1.5, o)).toBeCloseTo(100);
    // Leaving the gap continues at normal speed: 1 s of recording takes 0.1 s.
    expect(advance(5, 1.6, o)).toBeCloseTo(101);
    // Before the gap: 5 s at 10x takes 0.5 s, then 0.5 s inside the gap.
    expect(advance(0, 1, o)).toBeCloseTo(5 + (0.5 * 95) / 1.5);
  });

  it("a gap that fits in the cap runs at the normal speed", () => {
    const o = { ...base, skipWaits: true, times: [0, 15], end: 15 };
    expect(advance(0, 1, o)).toBeCloseTo(10);
    expect(advance(0, 1.5, o)).toBeCloseTo(15);
  });

  it("without skipping playback is proportional", () => {
    expect(advance(5, 1.5, base)).toBeCloseTo(20);
  });

  it("stops at the end", () => {
    expect(advance(100, 10, base)).toBe(101);
    expect(advance(101, 1, { ...base, skipWaits: true })).toBe(101);
    expect(advance(5, 1000, { ...base, skipWaits: true })).toBe(101);
  });
});

/** A manual requestAnimationFrame: frames only run when the test flushes them. */
function fakeRaf() {
  let id = 0;
  const queue = new Map<number, FrameRequestCallback>();
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    queue.set(++id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (n: number) => void queue.delete(n));
  return {
    pending: () => queue.size,
    flush(ts: number) {
      const cbs = [...queue.values()];
      queue.clear();
      for (const cb of cbs) cb(ts);
    },
  };
}

describe("usePlayer", () => {
  const graph = fixtureGraphs().graphs.multi;
  const replay = makeReplay();
  const end = endTime(replay);
  let raf: ReturnType<typeof fakeRaf>;
  beforeEach(() => {
    raf = fakeRaf();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("usePlayer plays, pauses, seeks, steps and changes speed", () => {
    const { result } = renderHook(() => usePlayer(replay, graph));
    expect(result.current).toMatchObject({ t: 0, playing: false, speed: 10, skipWaits: true });

    act(() => result.current.setSkipWaits(false));
    act(() => result.current.play());
    expect(result.current.playing).toBe(true);
    act(() => raf.flush(1000)); // the first frame only sets the reference time
    expect(result.current.t).toBe(0);
    act(() => raf.flush(1500)); // 0.5 s at 10x
    expect(result.current.t).toBeCloseTo(5);
    act(() => result.current.setSpeed(2));
    expect(result.current.speed).toBe(2);
    act(() => raf.flush(2500)); // 1 s at 2x
    expect(result.current.t).toBeCloseTo(7);

    act(() => result.current.pause());
    expect(result.current.playing).toBe(false);
    expect(raf.pending()).toBe(0);
    const paused = result.current.t;
    act(() => raf.flush(9000));
    expect(result.current.t).toBe(paused);

    act(() => result.current.seek(20));
    expect(result.current.t).toBe(20);
    expect(result.current.state.stepIndex).toBe(13);
    act(() => result.current.seek(-5));
    expect(result.current.t).toBe(0);
    act(() => result.current.seek(1e6));
    expect(result.current.t).toBe(end);

    act(() => result.current.step(-1));
    expect(result.current.t).toBe(30.1);
    act(() => result.current.step(1));
    expect(result.current.t).toBe(end);
    act(() => result.current.toStart());
    expect(result.current.t).toBe(0);
    act(() => result.current.toEnd());
    expect(result.current.t).toBe(end);
    expect(result.current.state.finished).toBe(true);
  });

  it("stops at the end, and play at the end restarts from 0", () => {
    const { result } = renderHook(() => usePlayer(replay, graph, 40));
    expect(result.current.playing).toBe(false);
    expect(result.current.t).toBe(40);
    act(() => result.current.setSkipWaits(false));
    act(() => result.current.play());
    act(() => raf.flush(0));
    act(() => raf.flush(1000)); // 10 s at 10x, past the end
    expect(result.current.t).toBe(end);
    expect(result.current.playing).toBe(false);
    expect(raf.pending()).toBe(0);
    act(() => result.current.play());
    expect(result.current.t).toBe(0);
    expect(result.current.playing).toBe(true);
  });

  it("seeking to a time gives the same state as playing to that time", () => {
    const played = renderHook(() => usePlayer(replay, graph));
    act(() => played.result.current.setSkipWaits(false));
    act(() => played.result.current.play());
    act(() => raf.flush(0));
    act(() => raf.flush(2000)); // 20 s at 10x
    expect(played.result.current.t).toBeCloseTo(20);
    act(() => played.result.current.pause());

    const sought = renderHook(() => usePlayer(replay, graph));
    act(() => sought.result.current.seek(played.result.current.t));
    expect(sought.result.current.state).toEqual(played.result.current.state);

    // Backwards: seek the played hook to 9 s and compare with a fresh hook that plays to 9 s.
    act(() => played.result.current.seek(9));
    const fresh = renderHook(() => usePlayer(replay, graph));
    act(() => fresh.result.current.setSkipWaits(false));
    act(() => fresh.result.current.play());
    act(() => raf.flush(5000));
    act(() => raf.flush(5900)); // 0.9 s at 10x
    expect(fresh.result.current.t).toBeCloseTo(9);
    act(() => fresh.result.current.pause());
    act(() => fresh.result.current.seek(9)); // removes the float noise of 0.9 * 10
    expect(played.result.current.state).toEqual(fresh.result.current.state);
  });
});
