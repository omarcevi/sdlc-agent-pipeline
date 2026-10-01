"""Review audit: how often did the reviewer of past multi-agent runs catch a bad diff
and send back a good one?

uv run python -m bench.review_audit results/<multi>.json [...] [--runs-dir runs] --out docs/results/<name>.md

Each reviewer verdict in a run's events.jsonl is paired with the diff it reviewed. The
diff is scored once with the hidden tests, by the scorer's own visible-then-hidden
sandbox procedure (`bench.probes.check_patch`). "Good" means it passed the hidden
tests. Dev tasks only: a row whose split is not `dev` is counted and never opened.
Nothing printed or written holds patch text, test names or test output.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from google.adk.events import Event

from app.task_store import TaskSpec
from bench.probes import check_patch, dev_task

_HELDOUT_ID = re.compile(r"-h\d\d$")
APPROVE = "approve"


@dataclass(frozen=True)
class ReviewedDiff:
    run_id: str
    task_id: str
    round: int
    diff_sha256: str
    verdict: str


@dataclass
class Counts:
    bad_caught: int = 0
    bad_approved: int = 0
    good_sent_back: int = 0
    good_approved: int = 0
    unscored: int = 0
    rounds: int = 0
    distinct_diffs: int = 0


@dataclass
class AuditSummary:
    total: Counts = field(default_factory=Counts)
    by_task: dict[str, Counts] = field(default_factory=dict)
    skipped_not_multi: int = 0
    skipped_not_dev: int = 0
    skipped_no_events: int = 0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _diff_text(delta: dict) -> str | None:
    if isinstance(delta.get("diff_text"), str):
        return delta["diff_text"]
    diff = delta.get("diff")
    if isinstance(diff, dict) and isinstance(diff.get("unified_diff"), str):
        return diff["unified_diff"]
    return None


def _verdict_of(value: object) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("verdict"), str):
        return value["verdict"]
    return None


def pair_reviews(events_path: Path) -> list[tuple[str, ReviewedDiff]]:
    """Each reviewer answer with the latest diff before it. The answer is the `review`
    state delta, or the arguments of the reviewer's `set_model_response` call when no
    delta follows it. Returns (diff text, ReviewedDiff); run and task ids are left
    empty for the caller to fill in."""
    pairs: list[tuple[str, ReviewedDiff]] = []
    diff: str | None = None
    pending: str | None = None  # a verdict seen in the call, not yet in a delta

    def emit(verdict: str) -> None:
        if diff is not None:
            pairs.append(
                (diff, ReviewedDiff("", "", len(pairs) + 1, _sha(diff), verdict))
            )

    for line in events_path.read_text().splitlines():
        if not line.strip():
            continue
        event = Event.model_validate_json(line)
        delta = event.actions.state_delta or {}
        new_diff = _diff_text(delta)
        if new_diff is not None:
            if pending is not None:
                emit(pending)
                pending = None
            diff = new_diff
        if event.author != "reviewer":
            continue
        for part in (event.content.parts if event.content else None) or []:
            call = part.function_call
            if call and call.name == "set_model_response":
                pending = _verdict_of(call.args)
        if (verdict := _verdict_of(delta.get("review"))) is not None:
            pending = None
            emit(verdict)
    if pending is not None:
        emit(pending)
    return pairs


def _real_score(task: TaskSpec, patch: str) -> bool:
    return bool(asyncio.run(check_patch(task, patch)).hidden_passes)


def audit(
    rows: list[dict],
    runs_dir: Path,
    *,
    score: Callable[[TaskSpec, str], bool] = _real_score,
) -> AuditSummary:
    summary = AuditSummary()
    scores: dict[tuple[str, str], bool | None] = {}
    seen: dict[str, set[str]] = {}
    for row in rows:
        if row.get("system") != "multi":
            summary.skipped_not_multi += 1
            continue
        task_id = str(row.get("task_id", ""))
        if row.get("split") != "dev" or _HELDOUT_ID.search(task_id):
            summary.skipped_not_dev += 1
            continue
        task = dev_task(task_id)
        events = Path(runs_dir) / str(row.get("run_id", "")) / "events.jsonl"
        if task is None:
            summary.skipped_not_dev += 1
            continue
        if not events.is_file():
            summary.skipped_no_events += 1
            continue
        counts = summary.by_task.setdefault(task_id, Counts())
        for patch, reviewed in pair_reviews(events):
            key = (task_id, reviewed.diff_sha256)
            if key not in scores:
                try:
                    scores[key] = bool(score(task, patch))
                except Exception:  # a message could quote patch or test text
                    scores[key] = None
            good = scores[key]
            for c in (counts, summary.total):
                c.rounds += 1
                if good is None:
                    c.unscored += 1
                elif good:
                    if reviewed.verdict == APPROVE:
                        c.good_approved += 1
                    else:
                        c.good_sent_back += 1
                elif reviewed.verdict == APPROVE:
                    c.bad_approved += 1
                else:
                    c.bad_caught += 1
            seen.setdefault(task_id, set()).add(reviewed.diff_sha256)
    for task_id, hashes in seen.items():
        summary.by_task[task_id].distinct_diffs = len(hashes)
        summary.total.distinct_diffs += len(hashes)
    return summary


_COLUMNS = (
    "bad caught",
    "bad approved (missed)",
    "good sent back (false alarm)",
    "good approved",
    "rounds reviewed",
    "distinct diffs",
)


def _cells(c: Counts) -> list[str]:
    return [
        str(n)
        for n in (
            c.bad_caught,
            c.bad_approved,
            c.good_sent_back,
            c.good_approved,
            c.rounds,
            c.distinct_diffs,
        )
    ]


def render_report(summary: AuditSummary, title: str = "Review audit") -> str:
    t = summary.total
    lines = [
        f"# {title}",
        "",
        "Each reviewer verdict in the multi-agent runs, paired with the diff it "
        "reviewed. A diff is good if it passes the hidden tests, bad if not.",
        "",
        "| task | " + " | ".join(_COLUMNS) + " |",
        "|---|" + "---|" * len(_COLUMNS),
    ]
    for task_id in sorted(summary.by_task):
        lines.append(
            f"| {task_id} | " + " | ".join(_cells(summary.by_task[task_id])) + " |"
        )
    lines.append("| **total** | " + " | ".join(_cells(t)) + " |")
    bad = t.bad_caught + t.bad_approved
    good = t.good_sent_back + t.good_approved
    lines += [""]
    if bad:
        lines.append(f"- Bad diffs caught: {t.bad_caught} of {bad}.")
    if good:
        lines.append(f"- Good diffs sent back: {t.good_sent_back} of {good}.")
    if t.unscored:
        lines.append(f"- Rounds whose diff could not be scored: {t.unscored}.")
    skipped = summary.skipped_not_multi + summary.skipped_not_dev
    lines.append(
        f"- Rows skipped: {summary.skipped_not_multi} not multi-agent, "
        f"{summary.skipped_not_dev} not in the dev split (not opened), "
        f"{summary.skipped_no_events} without an event log. ({skipped} excluded "
        "by system or split.)"
    )
    lines += [
        "",
        "## Caveats",
        "",
        "- A catch counts the verdict, not its reasons: the reviewer may have sent a "
        "bad diff back for the wrong reason.",
        "- Only diffs that passed the visible tests reach the reviewer, so the bad "
        "diffs here are the ones the visible tests did not catch.",
        "",
    ]
    return "\n".join(lines)


def _load_rows(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        rows.extend(json.loads(Path(path).read_text()))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.review_audit")
    parser.add_argument("results", nargs="+")
    parser.add_argument("--runs-dir", default=os.environ.get("RUNS_DIR", "runs"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--title", default="Review audit")
    args = parser.parse_args(argv)
    load_dotenv()
    summary = audit(_load_rows(args.results), Path(args.runs_dir))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(summary, args.title))
    t = summary.total
    print(f"{t.rounds} rounds, {t.distinct_diffs} distinct diffs; wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
