import { fmtClock } from "./format";

export interface TransportProps {
  t: number;
  end: number;
  playing: boolean;
  speed: number;
  /** F14: Task 6 owns SPEEDS; the page passes it in. */
  speeds: readonly number[];
  skipWaits: boolean;
  ticks: { t: number; node: string }[];
  onPlay(): void;
  onPause(): void;
  onSeek(t: number): void;
  onStep(direction: -1 | 1): void;
  onStart(): void;
  onEnd(): void;
  onSpeed(s: number): void;
  onSkipWaits(on: boolean): void;
}

const btn = "rounded border px-2 py-1 text-sm";

export function Transport(p: TransportProps) {
  return (
    <div className="space-y-2" role="group" aria-label="Playback controls">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={btn} aria-label="Start" onClick={p.onStart}>
          ⏮
        </button>
        <button type="button" className={btn} aria-label="Previous step" onClick={() => p.onStep(-1)}>
          ◀
        </button>
        {p.playing ? (
          <button type="button" className={btn} aria-label="Pause" onClick={p.onPause}>
            ⏸
          </button>
        ) : (
          <button type="button" className={btn} aria-label="Play" onClick={p.onPlay}>
            ▶
          </button>
        )}
        <button type="button" className={btn} aria-label="Next step" onClick={() => p.onStep(1)}>
          ▶|
        </button>
        <button type="button" className={btn} aria-label="End" onClick={p.onEnd}>
          ⏭
        </button>
        <label className="text-sm">
          Speed{" "}
          <select aria-label="Speed" value={p.speed} onChange={(e) => p.onSpeed(Number(e.target.value))}>
            {p.speeds.map((s) => (
              <option key={s} value={s}>{`${s}×`}</option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <input type="checkbox" checked={p.skipWaits} onChange={(e) => p.onSkipWaits(e.target.checked)} /> skip long
          waits
        </label>
        <span className="ml-auto font-mono text-sm">{`${fmtClock(p.t)} / ${fmtClock(p.end)}`}</span>
      </div>
      <div className="relative">
        <input
          type="range"
          aria-label="Scrubber"
          className="w-full"
          min={0}
          max={p.end}
          step="any"
          value={Math.min(p.t, p.end)}
          onChange={(e) => p.onSeek(Number(e.target.value))}
        />
        <div aria-hidden="true" className="relative h-2">
          {p.end > 0 &&
            p.ticks.map((k, i) => (
              <span
                key={i}
                data-testid="tick"
                title={k.node}
                className="absolute top-0 h-2 w-px bg-gray-500"
                style={{ left: `${Math.min(100, (k.t / p.end) * 100)}%` }}
              />
            ))}
        </div>
      </div>
    </div>
  );
}
