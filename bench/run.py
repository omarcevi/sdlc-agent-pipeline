"""Run the pipeline over bench tasks and score the results.

uv run python -m bench.run --tasks tc-001,tc-003
uv run python -m bench.run --split dev
"""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from google.adk.events import Event

from app.driver import run_pipeline
from app.schemas import RunRequest
from app.task_store import TaskSpec, list_tasks, load_task, task_dir
from app.tracing import enable_cloud_trace, flush_traces, trace_explorer_url
from bench.progress import format_event
from bench.score import is_resolved

OnEvent = Callable[[Event], None]
# With progress on, run_one is also given an `on_event` keyword argument.
RunOne = Callable[..., Awaitable[dict]]


async def run_task(task: TaskSpec, stamp: str, on_event: OnEvent | None = None) -> dict:
    """Run the pipeline on one task and score the result."""
    record = await run_pipeline(
        RunRequest(task_id=task.task_id, run_id=f"{task.task_id}-{stamp}"),
        on_event=on_event,
    )
    return {
        "task_id": task.task_id,
        "category": task.category,
        "resolved": await is_resolved(task, record),
        **record.model_dump(),
    }


def _crash_row(task: TaskSpec, exc: Exception) -> dict:
    return {
        "task_id": task.task_id,
        "category": task.category,
        "resolved": False,
        "outcome": "failed",
        "failure_kind": "infra",
        "reason": f"unhandled {type(exc).__name__}: {exc}",
        "crashed": True,
    }


def _print_progress(task_id: str) -> OnEvent:
    def on_event(event: Event) -> None:
        for line in format_event(task_id, event):
            print(line, flush=True)

    return on_event


def _status_line(row: dict) -> str:
    if row.get("crashed"):
        return f"{row['task_id']}: crashed ({row['reason']})"
    return (
        f"{row['task_id']}: {row['outcome']} ({row['failure_kind']}) "
        f"${row['cost_usd']:.3f} {row['duration_s']:.0f}s"
    )


async def run_tasks(
    tasks: list[TaskSpec],
    stamp: str,
    results_path: Path,
    run_one: RunOne | None = None,
    progress: bool = False,
) -> list[dict]:
    """Run every task in turn. An exception from one task's pipeline or scoring is
    recorded as a crashed row and the run continues; the results file is rewritten
    after every task, so an interrupted run keeps what it finished. With `progress`,
    each task's events are printed as they happen."""
    run_one = run_one or run_task
    rows: list[dict] = []
    for task in tasks:
        try:
            if progress:
                row = await run_one(task, stamp, on_event=_print_progress(task.task_id))
            else:
                row = await run_one(task, stamp)
        except Exception as exc:
            row = _crash_row(task, exc)
        rows.append(row)
        results_path.write_text(json.dumps(rows, indent=2))
        print(_status_line(row), flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    tracing_on = enable_cloud_trace()
    parser = argparse.ArgumentParser(prog="bench.run")
    parser.add_argument("--tasks", help="comma-separated task ids")
    parser.add_argument("--split", choices=["dev"], default="dev")
    parser.add_argument("--out", default="results")
    parser.add_argument(
        "--quiet", action="store_true", help="do not print live progress"
    )
    args = parser.parse_args(argv)

    # Every task is loaded before any of them runs, so a typo costs nothing.
    if args.tasks:
        task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
        unknown = [t for t in task_ids if not (task_dir(t) / "task.yaml").is_file()]
        if unknown:
            print(f"error: unknown task id: {', '.join(unknown)}", file=sys.stderr)
            return 2
        tasks = [load_task(t) for t in task_ids]
    else:
        tasks = [t for t in list_tasks() if t.split == args.split]
    if not tasks:
        print("error: no tasks selected", file=sys.stderr)
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / f"{stamp}.json"
    rows = asyncio.run(run_tasks(tasks, stamp, results_path, progress=not args.quiet))

    resolved = sum(r["resolved"] for r in rows)
    crashed = sum(bool(r.get("crashed")) for r in rows)
    cost = sum(r.get("cost_usd", 0.0) for r in rows)
    print(
        f"\nresolved {resolved}/{len(rows)}"
        + (f"  {crashed} crashed" if crashed else "")
        + f"  total ${cost:.2f}  → {results_path}"
    )
    if tracing_on:
        flush_traces()
        print(f"traces: {trace_explorer_url(os.environ['GOOGLE_CLOUD_PROJECT'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
