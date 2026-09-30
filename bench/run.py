"""Run the pipeline over bench tasks and score the results.

uv run python -m bench.run --tasks tc-001,tc-003
uv run python -m bench.run --split dev
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from app.driver import run_pipeline
from app.schemas import RunRequest
from app.task_store import list_tasks, load_task
from bench.score import is_resolved


async def run_tasks(task_ids: list[str], stamp: str) -> list[dict]:
    rows = []
    for task_id in task_ids:
        task = load_task(task_id)
        record = await run_pipeline(
            RunRequest(task_id=task_id, run_id=f"{task_id}-{stamp}")
        )
        rows.append(
            {
                "task_id": task_id,
                "category": task.category,
                "resolved": is_resolved(task, record),
                **record.model_dump(),
            }
        )
        print(
            f"{task_id}: {record.outcome} ({record.failure_kind}) ${record.cost_usd:.3f} {record.duration_s:.0f}s",
            flush=True,
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="bench.run")
    parser.add_argument("--tasks", help="comma-separated task ids")
    parser.add_argument("--split", choices=["dev"], default="dev")
    parser.add_argument("--out", default="results")
    args = parser.parse_args(argv)

    task_ids = (
        args.tasks.split(",")
        if args.tasks
        else [t.task_id for t in list_tasks() if t.split == args.split]
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows = asyncio.run(run_tasks(task_ids, stamp))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stamp}.json").write_text(json.dumps(rows, indent=2))
    resolved = sum(r["resolved"] for r in rows)
    cost = sum(r["cost_usd"] for r in rows)
    print(
        f"\nresolved {resolved}/{len(rows)}  total ${cost:.2f}  → {out / (stamp + '.json')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
