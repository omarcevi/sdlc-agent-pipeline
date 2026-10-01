import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { advance, DEFAULT_SPEED } from "./clock";
import { endTime, nextStepTime, prevStepTime, stateAt } from "./state";
import type { GraphDef, Replay, ReplayState } from "./types";

export interface Player {
  t: number;
  state: ReplayState;
  playing: boolean;
  speed: number;
  skipWaits: boolean;
  play(): void;
  pause(): void;
  seek(t: number): void;
  step(direction: -1 | 1): void;
  toStart(): void;
  toEnd(): void;
  setSpeed(s: number): void;
  setSkipWaits(on: boolean): void;
}

/** The player: `t` driven by requestAnimationFrame, everything else derived by `stateAt`. */
export function usePlayer(replay: Replay, graph: GraphDef, startT = 0): Player {
  const end = useMemo(() => endTime(replay), [replay]);
  const times = useMemo(() => replay.steps.map((s) => s.t), [replay]);
  const clamp = useCallback((x: number) => Math.min(end, Math.max(0, x)), [end]);

  const [t, setT] = useState(() => clamp(startT));
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedState] = useState<number>(DEFAULT_SPEED);
  const [skipWaits, setSkipWaitsState] = useState(true);

  // Live values for the frame loop, so changing speed does not restart it.
  const live = useRef({ t, speed, skipWaits, times, end });
  live.current = { ...live.current, speed, skipWaits, times, end };

  const move = useCallback((next: number) => {
    live.current.t = next;
    setT(next);
  }, []);

  useEffect(() => {
    if (!playing) return;
    let id = 0;
    let last: number | null = null;
    const frame = (ts: number) => {
      if (last !== null) {
        const cur = live.current;
        const next = advance(cur.t, (ts - last) / 1000, cur);
        move(next);
        if (next >= cur.end) {
          setPlaying(false);
          return;
        }
      }
      last = ts;
      id = requestAnimationFrame(frame);
    };
    id = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(id);
  }, [playing, move]);

  const play = useCallback(() => {
    if (live.current.t >= live.current.end) move(0);
    setPlaying(true);
  }, [move]);
  const pause = useCallback(() => setPlaying(false), []);
  const seek = useCallback(
    (to: number) => {
      const next = clamp(to);
      move(next);
      if (next >= end) setPlaying(false);
    },
    [clamp, end, move],
  );
  const step = useCallback(
    (direction: -1 | 1) => {
      setPlaying(false);
      move(
        direction === 1
          ? nextStepTime(replay, live.current.t)
          : prevStepTime(replay, live.current.t),
      );
    },
    [replay, move],
  );
  const toStart = useCallback(() => {
    setPlaying(false);
    move(0);
  }, [move]);
  const toEnd = useCallback(() => {
    setPlaying(false);
    move(end);
  }, [end, move]);

  const state = useMemo(() => stateAt(replay, graph, t), [replay, graph, t]);

  return {
    t,
    state,
    playing,
    speed,
    skipWaits,
    play,
    pause,
    seek,
    step,
    toStart,
    toEnd,
    setSpeed: setSpeedState,
    setSkipWaits: setSkipWaitsState,
  };
}
