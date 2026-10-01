"""Score one recorded run (a live run from a demo branch, or any bench run).

uv run python -m bench.score_run --run-id ID [--task TASK_ID] [--runs-dir runs] [--out results/live]

The task is `--task` or, when the record's `base_ref` is `demo/<task-id>`, that task.
Scoring runs in fresh sandboxes through `bench.score.is_resolved`. A held-out task is
refused by its name before anything else is read, with a message that names nothing.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from app.schemas import RunRecord
from app.task_store import TaskSpec, task_dir
from bench.audit import audit_patch
from bench.demo import NOT_DEV, dev_task, expected_tree_sha, is_heldout_id
from bench.score import is_resolved

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
TREE_MISMATCH = "the run's source is not this task's demo state; not scored"
_DEMO_PREFIX = "demo/"


class Refused(Exception):
    pass


def _fetched_issue(events_path: Path) -> dict | None:
    """The issue as the run fetched it, from events.jsonl. The pull request URL and
    the approval wait are in record.json."""
    try:
        lines = events_path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            delta = (json.loads(line).get("actions") or {}).get("state_delta") or {}
        except (ValueError, AttributeError):
            continue
        if isinstance(delta.get("issue"), dict):
            return delta["issue"]
    return None


def _task_for(requested: str | None, record: RunRecord) -> TaskSpec:
    task_id = requested
    if task_id is None and record.base_ref and record.base_ref.startswith(_DEMO_PREFIX):
        task_id = record.base_ref[len(_DEMO_PREFIX) :]
    if task_id is None:
        raise Refused("no task: pass --task")
    if is_heldout_id(task_id):
        raise Refused(NOT_DEV)
    task = dev_task(task_id)
    if task is not None:
        return task
    if not (task_dir(task_id) / "task.yaml").is_file():
        raise Refused(f"unknown task: {task_id}")
    raise Refused(NOT_DEV)


def _normalised(text: object) -> object:
    if not isinstance(text, str):
        return text
    return text.replace("\r\n", "\n").rstrip()


def same_issue_text(fetched: object, expected: object) -> bool:
    """Whether the fetched issue text is the task's text. GitHub keeps what a web
    form sent (CRLF line ends, often a trailing newline), so line-ending style and
    trailing whitespace are not a difference."""
    return _normalised(fetched) == _normalised(expected)


def score_run(
    run_id: str, task_id: str | None, runs_dir: Path, out: Path
) -> tuple[Path, dict]:
    # The held-out check comes before any file is read.
    if task_id is not None and is_heldout_id(task_id):
        raise Refused(NOT_DEV)
    if not _RUN_ID.fullmatch(run_id):
        raise Refused("invalid run id")
    run_dir = Path(runs_dir) / run_id
    try:
        record = RunRecord.model_validate_json((run_dir / "record.json").read_text())
    except (OSError, ValueError):
        raise Refused(f"no readable record for run {run_id}") from None
    task = _task_for(task_id, record)
    demo_ref = bool(record.base_ref and record.base_ref.startswith(_DEMO_PREFIX))
    # A bench run has no base tree; a run from a demo branch must prove its source.
    if (record.base_tree_sha is not None or demo_ref) and (
        record.base_tree_sha != expected_tree_sha(task)
    ):
        raise Refused(TREE_MISMATCH)

    # A live run that opened a pull request wrote a patch like any other.
    scored = (
        record.model_copy(update={"outcome": "patch_written"})
        if record.outcome == "pr_opened"
        else record
    )
    resolved = asyncio.run(is_resolved(task, scored))
    issue = _fetched_issue(run_dir / "events.jsonl")
    differs = (
        None
        if issue is None
        else not (
            same_issue_text(issue.get("title"), task.title)
            and same_issue_text(issue.get("body"), task.body)
        )
    )
    audit: list[str] = []
    if record.patch_path:
        try:
            audit = audit_patch(Path(record.patch_path).read_text())
        except OSError:
            pass
    row = {
        "task_id": task.task_id,
        "category": task.category,
        "repo": task.repo,
        "difficulty": task.difficulty,
        "split": task.split,
        "run_id": record.run_id,
        "resolved": resolved,
        "outcome": record.outcome,
        "failure_kind": record.failure_kind,
        "reason": record.reason,
        "cost_usd": record.cost_usd,
        "duration_s": record.duration_s,
        "tool_calls": record.tool_calls,
        "tokens_in": record.tokens_in,
        "tokens_out": record.tokens_out,
        "test_attempts": record.test_attempts,
        "review_rounds": record.review_rounds,
        "audit": audit,
        "mode": record.mode,
        "pr_url": record.pr_url,
        "approval_wait_s": record.approval_wait_s,
        "issue_text_differs": differs,
    }
    Path(out).mkdir(parents=True, exist_ok=True)
    path = Path(out) / f"{run_id}.json"
    path.write_text(json.dumps(row, indent=2))
    return path, row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.score_run")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task")
    parser.add_argument("--runs-dir", default=os.environ.get("RUNS_DIR", "runs"))
    parser.add_argument("--out", default="results/live")
    args = parser.parse_args(argv)
    try:
        path, row = score_run(
            args.run_id, args.task, Path(args.runs_dir), Path(args.out)
        )
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{row['run_id']}: resolved={row['resolved']} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
