import { useMemo, useState } from "react";
import { Diff, Hunk, parseDiff } from "react-diff-view";
import type { FileData } from "react-diff-view";
import "react-diff-view/style/index.css";
import type { DiffStep } from "../replay/types";
import { Mono, Panel } from "./Text";

const CUT_TAIL = /\n?\[\.\.\. [\d,]+ characters cut \.\.\.\]\n?$/;

interface Parsed {
  files: FileData[];
  marker: string | null;
}

/** F19: the converter cuts at a hunk boundary and appends a marker; peel it off before parsing. */
function parse(text: string): Parsed | null {
  const m = CUT_TAIL.exec(text);
  const body = m ? text.slice(0, m.index) : text;
  const marker = m ? m[0].trim() : null;
  try {
    const files = parseDiff(body, { nearbySequences: "zip" });
    if (files.length === 0 || files.every((f) => f.hunks.length === 0)) return null;
    return { files, marker };
  } catch {
    return null;
  }
}

function counts(f: FileData): { add: number; del: number } {
  let add = 0;
  let del = 0;
  for (const h of f.hunks)
    for (const c of h.changes) {
      if (c.type === "insert") add += 1;
      else if (c.type === "delete") del += 1;
    }
  return { add, del };
}

function fileName(f: FileData): string {
  return f.type === "delete" ? f.oldPath : f.newPath;
}

export function DiffPanel({ diffs }: { diffs: DiffStep[] }) {
  const [picked, setPicked] = useState<{ n: number; k: number } | null>(null);
  // Follow the newest diff until the reader picks one; a new diff resets the pick.
  const k = picked && picked.n === diffs.length ? Math.min(picked.k, diffs.length - 1) : diffs.length - 1;
  const text = diffs[k]?.value.unified_diff ?? "";
  const parsed = useMemo(() => parse(text), [text]);
  if (diffs.length === 0) {
    return (
      <Panel title="Diff">
        <p className="m-0 italic">No diff yet.</p>
      </Panel>
    );
  }
  const step = diffs[k];
  const v = step.value;

  return (
    <Panel title="Diff">
      {diffs.length > 1 && (
        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            aria-label="Previous diff"
            disabled={k === 0}
            onClick={() => setPicked({ n: diffs.length, k: k - 1 })}
          >
            ◀
          </button>
          <span>{`diff ${k + 1} of ${diffs.length}`}</span>
          <button
            type="button"
            aria-label="Next diff"
            disabled={k === diffs.length - 1}
            onClick={() => setPicked({ n: diffs.length, k: k + 1 })}
          >
            ▶
          </button>
        </div>
      )}
      <p className="m-0 text-xs">{`${v.files.length} files · +${v.insertions} −${v.deletions}`}</p>
      {v.cut && (
        <p className="m-0 text-xs">This diff was shortened for the page; the counts are those of the full diff.</p>
      )}
      {parsed === null ? (
        <Mono>{v.unified_diff}</Mono>
      ) : (
        <>
          {parsed.files.map((f, j) => {
            const c = counts(f);
            return (
              <div key={j} className="space-y-1">
                <p className="m-0 font-mono text-xs font-semibold" data-file={fileName(f)}>
                  {`${fileName(f)} +${c.add} −${c.del}`}
                </p>
                <Diff viewType="unified" diffType={f.type} hunks={f.hunks}>
                  {(hunks) => hunks.map((h, hi) => <Hunk key={hi} hunk={h} />)}
                </Diff>
              </div>
            );
          })}
          {parsed.marker !== null && <Mono>{parsed.marker}</Mono>}
        </>
      )}
    </Panel>
  );
}
