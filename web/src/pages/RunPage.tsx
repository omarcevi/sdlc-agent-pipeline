import { useEffect, useMemo, useRef, useState } from "react";
import { GraphView } from "../graph/GraphView";
import { ClaimPanel } from "../panels/ClaimPanel";
import { CostMeter } from "../panels/CostMeter";
import { DiffPanel } from "../panels/DiffPanel";
import { Feed } from "../panels/Feed";
import { HowToRead } from "../panels/HowToRead";
import { IssuePanel } from "../panels/IssuePanel";
import { OutcomeBanner } from "../panels/OutcomeBanner";
import { PlanPanel } from "../panels/PlanPanel";
import { ReviewPanel } from "../panels/ReviewPanel";
import { TestsPanel } from "../panels/TestsPanel";
import { Transport } from "../panels/Transport";
import { SPEEDS } from "../replay/clock";
import { endTime, nextVisitTime, nodeTicks } from "../replay/state";
import type { Graphs, IndexEntry, Replay } from "../replay/types";
import { usePlayer, type Player } from "../replay/usePlayer";
import { runHash } from "../routes";
import { SYSTEM_LABEL } from "./RunList";

const TABS = ["Issue", "Plan", "Agent summary", "Diff", "Tests", "Review"] as const;
type Tab = (typeof TABS)[number];

export interface RunPageProps {
  entry: IndexEntry;
  replay: Replay;
  graphs: Graphs;
  startT: number | null;
}

/** Focus in one of these keeps the key for the control itself. */
function inFormControl(target: EventTarget | null, key: string): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return true;
  return key === " " && target.closest("button,summary,a,[role=button]") !== null;
}

/** Mount this keyed by run id: the player state starts fresh for each run. */
export function RunPage({ entry, replay, graphs, startT }: RunPageProps) {
  const graph = graphs.graphs[replay.run.graph];
  const player = usePlayer(replay, graph, Number.isFinite(startT) ? (startT ?? 0) : 0);
  const { state, t } = player;
  const end = useMemo(() => endTime(replay), [replay]);
  const ticks = useMemo(() => nodeTicks(replay), [replay]);
  const [tab, setTab] = useState<Tab>("Issue");
  const [follow, setFollow] = useState(true);

  const live = useRef<Player>(player);
  live.current = player;

  const seek = (to: number) => {
    if (Number.isFinite(to)) live.current.seek(to);
  };
  const setSpeed = (s: number) => {
    if (Number.isFinite(s) && (SPEEDS as readonly number[]).includes(s)) live.current.setSpeed(s);
  };

  // While paused the address bar carries the time, so the link shares this moment.
  useEffect(() => {
    if (!player.playing) window.history.replaceState(null, "", runHash(entry.run_id, t));
  }, [player.playing, t, entry.run_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || inFormControl(e.target, e.key)) return;
      const p = live.current;
      switch (e.key) {
        case " ":
          if (e.repeat) break;
          if (p.playing) p.pause();
          else p.play();
          break;
        case "ArrowLeft":
          p.step(-1);
          break;
        case "ArrowRight":
          p.step(1);
          break;
        case "Home":
          p.toStart();
          break;
        case "End":
          p.toEnd();
          break;
        default:
          return;
      }
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const models = [...new Set(Object.values(replay.run.models))].join(", ");

  return (
    <main className="mx-auto max-w-6xl space-y-4 p-4">
      <header className="space-y-1">
        <a className="text-sm underline" href="#/">
          All replays
        </a>
        <h1 className="m-0 text-xl font-semibold">{`${replay.run.task_id} · ${replay.run.issue.title}`}</h1>
        <p className="m-0 text-sm">
          <span>{SYSTEM_LABEL[replay.run.graph]}</span>
          {` · ${models} · prompt ${replay.run.prompt_version}`}
        </p>
        <p className="m-0 text-sm">{entry.caption}</p>
        {entry.pair !== null && (
          <a className="text-sm underline" href={runHash(entry.pair)}>
            compare
          </a>
        )}
      </header>

      <CostMeter state={state} caps={replay.caps} t={t} />

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <div className="h-[420px] min-w-0 rounded border">
          <GraphView
            graph={graph}
            graphId={replay.run.graph}
            state={state}
            models={replay.run.models}
            onNodeClick={(id) => {
              const to = nextVisitTime(replay, id, live.current.t);
              if (to !== null) seek(to);
            }}
          />
        </div>
        <div className="h-[420px] min-w-0 rounded border p-2">
          <Feed items={state.feed} follow={follow} onFollowChange={setFollow} onSeek={seek} />
        </div>
      </div>

      <Transport
        t={t}
        end={end}
        playing={player.playing}
        speed={player.speed}
        speeds={SPEEDS}
        skipWaits={player.skipWaits}
        ticks={ticks}
        onPlay={player.play}
        onPause={player.pause}
        onSeek={seek}
        onStep={player.step}
        onStart={player.toStart}
        onEnd={player.toEnd}
        onSpeed={setSpeed}
        onSkipWaits={player.setSkipWaits}
      />

      <div>
        <div role="tablist" aria-label="Run details" className="flex flex-wrap gap-1">
          {TABS.map((name) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={tab === name}
              className={`rounded-t border px-3 py-1 text-sm ${tab === name ? "font-semibold" : ""}`}
              onClick={() => setTab(name)}
            >
              {name}
            </button>
          ))}
        </div>
        <div role="tabpanel" className="rounded-b border p-3">
          {tab === "Issue" && <IssuePanel issue={replay.run.issue} />}
          {tab === "Plan" && <PlanPanel plans={state.plans} graphId={replay.run.graph} />}
          {tab === "Agent summary" && <ClaimPanel claims={state.claims} />}
          {tab === "Diff" && <DiffPanel diffs={state.diffs} />}
          {tab === "Tests" && <TestsPanel tests={state.tests} />}
          {tab === "Review" && <ReviewPanel reviews={state.reviews} graphId={replay.run.graph} />}
        </div>
      </div>

      <OutcomeBanner replay={replay} visible={state.finished} />

      <details>
        <summary className="cursor-pointer text-sm underline">About these replays</summary>
        <HowToRead />
      </details>
    </main>
  );
}
