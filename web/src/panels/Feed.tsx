import { useEffect, useRef, useState } from "react";
import type { FeedItem } from "../replay/types";
import { Fields, Mono } from "./Text";
import { fmtClock } from "./format";

export interface FeedProps {
  items: FeedItem[];
  follow: boolean;
  onFollowChange(on: boolean): void;
  onSeek(t: number): void;
}

const NEAR_BOTTOM_PX = 24;

/** Identity of a line: step and kind, plus the call id where one step holds several. */
function itemKey(item: FeedItem): string {
  const id = item.kind === "call" ? item.call.id : item.kind === "result" ? item.result.call : "";
  return `${item.kind}-${item.step}-${id}`;
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function toolName(tool: string, label: string): string {
  return tool === "set_model_response" ? "answer" : label;
}

function Line({
  item,
  open,
  onToggle,
  onSeek,
}: {
  item: FeedItem;
  open: boolean;
  onToggle(): void;
  onSeek(t: number): void;
}) {
  if (item.kind === "separator") {
    const text = `${item.node} · visit ${item.visit}${item.via ? ` · via ${item.via}` : ""}`;
    return (
      <li className="border-t pt-1 mt-1 text-xs font-semibold" data-kind="separator">
        {text}
      </li>
    );
  }

  let summary: string;
  let details: React.ReactNode = null;
  let detailsName = "";
  let tone = "";
  if (item.kind === "call") {
    summary = toolName(item.call.tool, item.call.label);
    detailsName = `Show details of ${summary}`;
    details = <Fields data={item.call.args} />;
  } else if (item.kind === "result") {
    const r = item.result;
    summary = toolName(r.tool, r.tool);
    detailsName = `Show result of ${summary}`;
    details = <Fields data={r.result} />;
    if (r.error !== null) tone = "text-red-700";
  } else if (item.kind === "wait") {
    summary = `${Math.round(item.seconds)} s without events`;
  } else if (item.kind === "stop") {
    summary = "stopped";
  } else {
    summary = item.label;
  }
  const error = item.kind === "result" ? item.result.error : null;

  return (
    <li className="text-sm" data-kind={item.kind}>
      <div className="flex items-baseline gap-2">
        <button
          type="button"
          className="font-mono text-xs underline"
          aria-label={`Seek to ${fmtClock(item.t)} (${summary})`}
          onClick={() => onSeek(item.t)}
        >
          {fmtClock(item.t)}
        </button>
        <span className={`font-mono text-xs ${tone}`}>{summary}</span>
        {error !== null && <span className="font-mono text-xs text-red-700">{`✗ ${error}`}</span>}
        {details !== null && (
          <button
            type="button"
            className="ml-auto text-xs"
            aria-label={detailsName}
            aria-expanded={open}
            onClick={onToggle}
          >
            {open ? "▾" : "▸"}
          </button>
        )}
      </div>
      {item.kind === "stop" && <Mono>{item.text}</Mono>}
      {open && details !== null && <div className="ml-10 mt-1">{details}</div>}
    </li>
  );
}

export function Feed({ items, follow, onFollowChange, onSeek }: FeedProps) {
  const box = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    const el = box.current;
    if (!follow || !el) return;
    const top = el.scrollHeight;
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top, behavior: prefersReducedMotion() ? "auto" : "smooth" });
    } else {
      el.scrollTop = top;
    }
  }, [items.length, follow]);

  // Only the reader stops following. The app's own smooth scroll only ever moves down, so follow
  // turns off only when scrollTop moves up and the box is away from the bottom.
  const lastTop = useRef(0);
  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    const top = el.scrollTop;
    const movedUp = top < lastTop.current;
    lastTop.current = top;
    if (!follow || !movedUp) return;
    if (el.scrollHeight - top - el.clientHeight > NEAR_BOTTOM_PX) onFollowChange(false);
  };

  const toggle = (k: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(k)) next.add(k);
      return next;
    });

  return (
    <section className="relative flex h-full min-h-0 flex-col" aria-label="Feed">
      <h2 className="text-sm font-semibold m-0">Feed</h2>
      <div ref={box} data-testid="feed-scroll" className="min-h-0 flex-1 overflow-y-auto" onScroll={onScroll}>
        <ol className="m-0 list-none space-y-1 p-0">
          {items.map((item) => {
            const k = itemKey(item);
            return <Line key={k} item={item} open={open.has(k)} onToggle={() => toggle(k)} onSeek={onSeek} />;
          })}
        </ol>
      </div>
      {!follow && (
        <button
          type="button"
          className="absolute bottom-2 right-2 rounded border bg-white px-2 py-1 text-xs"
          aria-label="follow"
          onClick={() => onFollowChange(true)}
        >
          follow
        </button>
      )}
    </section>
  );
}
