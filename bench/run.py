"""Run the pipeline over bench tasks and score the results.

uv run python -m bench.run --tasks tc-001,tc-003
uv run python -m bench.run --split dev --system single --preset pro --repeats 3 \
    --concurrency 2

Every run spends model credits.
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from app.environment.factory import check_environment_config, environment_backend
from app.task_store import list_tasks, load_task, task_dir
from app.tracing import enable_cloud_trace, flush_traces, trace_explorer_url
from bench.matrix import plan_runs, run_matrix
from bench.presets import PRESETS, solo_model_name
from bench.validate import validate_task

HELDOUT_ON_CLOUD = "held-out tasks never run on ENVIRONMENT_BACKEND=agent_runtime"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench.run")
    parser.add_argument("--tasks", help="comma-separated task ids")
    parser.add_argument("--split", choices=["dev", "heldout"], default="dev")
    parser.add_argument("--system", choices=["multi", "single"], default="multi")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="flash")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--out", default="results")
    parser.add_argument(
        "--quiet", action="store_true", help="do not print live progress"
    )
    parser.add_argument(
        "--confirm-heldout",
        action="store_true",
        help="allow running held-out tasks (their scores are for reporting only; "
        "refused on ENVIRONMENT_BACKEND=agent_runtime)",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="do not validate the selected tasks before running",
    )
    return parser


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    load_dotenv()  # BENCH_*_DIR may live in .env; selection and runs must agree
    try:
        check_environment_config()
    except ValueError as exc:
        return _fail(str(exc))
    on_cloud = environment_backend() == "agent_runtime"
    if args.repeats < 1 or args.concurrency < 1:
        return _fail("--repeats and --concurrency must be at least 1")
    if args.system == "single":
        try:
            solo_model_name(args.preset)
        except ValueError as exc:
            return _fail(str(exc))

    # Every task is loaded and checked before any of them runs, so a typo costs nothing.
    if args.tasks:
        task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
        unknown = [t for t in task_ids if not (task_dir(t) / "task.yaml").is_file()]
        if unknown:
            return _fail(f"unknown task id: {', '.join(unknown)}")
        tasks = [load_task(t) for t in task_ids]
    else:
        tasks = [t for t in list_tasks() if t.split == args.split]
    if not tasks:
        return _fail("no tasks selected")

    heldout = [t.task_id for t in tasks if t.split == "heldout"]
    if on_cloud and (heldout or args.confirm_heldout):
        # Hidden tests of held-out tasks never reach a cloud sandbox, confirmed or
        # not. The message names no task.
        return _fail(HELDOUT_ON_CLOUD)
    if heldout and not args.confirm_heldout:
        return _fail(
            f"held-out tasks selected ({', '.join(heldout)}); "
            "pass --confirm-heldout to run them"
        )
    if not args.skip_validate:
        problems = [
            f"{task.task_id}: {problem}"
            for task in tasks
            for problem in validate_task(task)
        ]
        if problems:
            return _fail("invalid tasks:\n  " + "\n  ".join(problems))

    tracing_on = enable_cloud_trace()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_path = out / f"{stamp}-{args.system}-{args.preset}.json"
    specs = plan_runs(tasks, args.system, args.preset, args.repeats, stamp)
    rows = asyncio.run(
        run_matrix(
            specs,
            results_path,
            concurrency=args.concurrency,
            progress=not args.quiet,
        )
    )

    resolved = sum(r["resolved"] for r in rows)
    crashed = sum(bool(r["crashed"]) for r in rows)
    cost = sum(r["cost_usd"] for r in rows)
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
