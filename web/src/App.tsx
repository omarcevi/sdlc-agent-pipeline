import { useEffect, useState } from "react";
import { loadIndex, loadReplay } from "./data";
import graphsJson from "./graph/graphs.json";
import { NotFound } from "./pages/NotFound";
import { RunList } from "./pages/RunList";
import { RunPage } from "./pages/RunPage";
import { parseGraphs, ReplayError } from "./replay/parse";
import type { Replay, ReplayIndex } from "./replay/types";
import { parseHash, type Route } from "./routes";

const GRAPHS = parseGraphs(graphsJson);

type Loaded<T> = { status: "loading" } | { status: "error"; error: unknown } | { status: "ready"; value: T };

function Message({ children }: { children: string }) {
  return (
    <main className="mx-auto max-w-3xl space-y-3 p-6">
      <p>{children}</p>
      <a className="underline" href="#/">
        Back to all replays
      </a>
    </main>
  );
}

function RunRoute({ index, runId, t }: { index: ReplayIndex; runId: string; t: number | null }) {
  const [loaded, setLoaded] = useState<Loaded<Replay | null>>({ status: "loading" });
  useEffect(() => {
    let live = true;
    setLoaded({ status: "loading" });
    loadReplay(index, runId, GRAPHS)
      .then((value) => live && setLoaded({ status: "ready", value }))
      .catch((error: unknown) => live && setLoaded({ status: "error", error }));
    return () => {
      live = false;
    };
  }, [index, runId]);

  if (loaded.status === "loading") return <p className="p-6">Loading replay…</p>;
  if (loaded.status === "error") {
    const newer = loaded.error instanceof ReplayError && loaded.error.message === "unsupported schema";
    return <Message>{newer ? "This replay needs a newer viewer" : "Could not load this replay"}</Message>;
  }
  const entry = index.replays.find((e) => e.run_id === runId);
  if (loaded.value === null || !entry) return <NotFound />;
  return <RunPage key={runId} entry={entry} replay={loaded.value} graphs={GRAPHS} startT={t} />;
}

export function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [index, setIndex] = useState<Loaded<ReplayIndex>>({ status: "loading" });

  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  useEffect(() => {
    let live = true;
    loadIndex()
      .then((value) => live && setIndex({ status: "ready", value }))
      .catch((error: unknown) => live && setIndex({ status: "error", error }));
    return () => {
      live = false;
    };
  }, []);

  if (route.name === "notFound") return <NotFound />;
  if (index.status === "loading") return <p className="p-6">Loading replays…</p>;
  if (index.status === "error") {
    const newer = index.error instanceof ReplayError && index.error.message === "unsupported schema";
    return <Message>{newer ? "This replay site needs a newer viewer" : "Could not load the replays"}</Message>;
  }
  if (route.name === "list") return <RunList index={index.value} />;
  return <RunRoute key={route.runId} index={index.value} runId={route.runId} t={route.t} />;
}
