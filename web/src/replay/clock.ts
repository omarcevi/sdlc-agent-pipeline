/** Playback speeds offered by the transport; Transport receives them as a prop (F14). */
export const SPEEDS = [1, 2, 5, 10, 25, 50] as const;
export const DEFAULT_SPEED = 10;
/** With "skip long waits" on, a gap between two steps lasts at most this many real seconds. */
export const MAX_WAIT_S = 1.5;

export interface AdvanceOptions {
  speed: number;
  skipWaits: boolean;
  /** Ascending step times. */
  times: number[];
  end: number;
}

/**
 * The recorded time after `realElapsedS` seconds of playing from `t`, capped at `end`.
 * With skipWaits, inside a gap longer than MAX_WAIT_S x speed, time runs at gap / MAX_WAIT_S
 * per real second, so the gap lasts MAX_WAIT_S of real time.
 */
export function advance(t: number, realElapsedS: number, opts: AdvanceOptions): number {
  const { speed, skipWaits, times, end } = opts;
  if (!skipWaits) return Math.min(end, t + realElapsedS * speed);

  let cur = t;
  let left = realElapsedS;
  while (left > 0 && cur < end) {
    let before = -Infinity;
    let after = Infinity;
    for (const x of times) {
      if (x <= cur) before = x;
      else {
        after = x;
        break;
      }
    }
    const boundary = Math.min(after, end);
    const gap = after - before; // Infinity at either edge of the recording: normal speed
    const rate = gap > MAX_WAIT_S * speed && Number.isFinite(gap) ? gap / MAX_WAIT_S : speed;
    const needed = (boundary - cur) / rate;
    if (needed <= left) {
      cur = boundary;
      left -= needed;
    } else {
      cur += left * rate;
      left = 0;
    }
  }
  return Math.min(end, cur);
}
