"""Run systems x presets x repeats over bench tasks, in parallel, with durable results."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

from google.adk.events import Event

from app.driver import RunCrashed, run_pipeline
from app.environment.base import InfraError
from app.schemas import RunRecord, RunRequest
from app.task_store import TaskSpec
from bench.audit import audit_patch
from bench.presets import workflow_for
from bench.progress import format_event
from bench.score import is_resolved

OnEvent = Callable[[Event], None]
# With progress on, run_one is also given an `on_event` keyword argument.
RunOne = Callable[..., Awaitable[dict]]
SCORE_ATTEMPTS = 3
SCORE_RETRY_PAUSE_S = 2.0
# Rerun k of an infra failure waits INFRA_RETRY_PAUSE_S * k seconds first, so a
# rerun does not land in the same rate-limit window as the failure it repeats.
INFRA_RETRY_PAUSE_S = 30.0


@dataclass(frozen=True)
class RunSpec:
    task: TaskSpec
    system: str
    preset: str
    repeat: int
    stamp: str
    attempt: int = 0

    @property
    def label(self) -> str:
        return f"{self.task.task_id}/{self.system}/{self.preset}/r{self.repeat}"

    @property
    def run_id(self) -> str:
        base = (
            f"{self.task.task_id}-{self.system}-{self.preset}-r{self.repeat}-"
            f"{self.stamp}"
        )
        return f"{base}-retry{self.attempt}" if self.attempt > 0 else base


def plan_runs(
    tasks: list[TaskSpec], system: str, preset: str, repeats: int, stamp: str
) -> list[RunSpec]:
    return [
        RunSpec(task, system, preset, repeat, stamp)
        for repeat in range(1, repeats + 1)
        for task in tasks
    ]


_DEFAULTS: dict = {
    "resolved": False,
    "outcome": "failed",
    "failure_kind": "none",
    "reason": "",
    "cost_usd": 0.0,
    "duration_s": 0.0,
    "tool_calls": 0,
    "tokens_in": 0,
    "tokens_out": 0,
    "test_attempts": 0,
    "review_rounds": 0,
    "audit": [],
    "infra_retries": 0,
    "crashed": False,
    "model_stalls": 0,
}


def _complete(row: dict, spec: RunSpec) -> dict:
    """Every row carries every field, whatever produced it."""
    full = {
        "task_id": spec.task.task_id,
        "category": spec.task.category,
        "repo": spec.task.repo,
        "difficulty": spec.task.difficulty,
        "split": spec.task.split,
        "system": spec.system,
        "preset": spec.preset,
        "repeat": spec.repeat,
        "run_id": spec.run_id,
        **{k: list(v) if isinstance(v, list) else v for k, v in _DEFAULTS.items()},
    }
    full.update(row)
    return full


def _audit(record: RunRecord) -> list[str]:
    if record.patch_path is None:
        return []
    try:
        return audit_patch(Path(record.patch_path).read_text())
    except OSError:
        return []


def _crash_row(spec: RunSpec, exc: Exception, record: RunRecord | None = None) -> dict:
    numbers = (
        record.model_dump(exclude={"task_id", "run_id", "outcome", "reason"})
        if record
        else {}
    )
    return _complete(
        {
            **numbers,
            "resolved": False,
            "outcome": "failed",
            "failure_kind": "infra",
            "reason": f"unhandled {type(exc).__name__}: {exc}",
            "crashed": True,
        },
        spec,
    )


async def _score(spec: RunSpec, record: RunRecord) -> bool:
    """Scoring is free, so an infra error (one failed `docker run`) is retried."""
    for attempt in range(SCORE_ATTEMPTS):
        try:
            return await is_resolved(spec.task, record)
        except InfraError:
            if attempt == SCORE_ATTEMPTS - 1:
                raise
            await asyncio.sleep(SCORE_RETRY_PAUSE_S)
    raise AssertionError("unreachable")


async def run_spec(
    spec: RunSpec,
    *,
    on_event: OnEvent | None = None,
    workflow_factory: Callable = workflow_for,
) -> dict:
    """Run one pipeline (own workflow, budget, run dir, sandbox) and score it."""
    record: RunRecord | None = None
    try:
        record = await run_pipeline(
            RunRequest(task_id=spec.task.task_id, run_id=spec.run_id),
            workflow=workflow_factory(spec.system, spec.preset),
            on_event=on_event,
        )
        resolved = await _score(spec, record)
    except RunCrashed as crashed:
        original = crashed.__cause__ or crashed
        return _crash_row(spec, original, crashed.record)
    except Exception as exc:
        return _crash_row(spec, exc, record)
    return _complete(
        {**record.model_dump(), "resolved": resolved, "audit": _audit(record)}, spec
    )


def _print_progress(label: str) -> OnEvent:
    def on_event(event: Event) -> None:
        for line in format_event(label, event):
            print(line, flush=True)

    return on_event


def _status_line(row: dict) -> str:
    label = f"{row['task_id']}/{row['system']}/{row['preset']}/r{row['repeat']}"
    if row["crashed"]:
        return f"{label}: crashed ({row['reason']})"
    verdict = "resolved" if row["resolved"] else "unresolved"
    return (
        f"{label}: {row['outcome']} {verdict} ({row['failure_kind']}) "
        f"${row['cost_usd']:.3f} {row['duration_s']:.0f}s"
    )


async def run_matrix(
    specs: list[RunSpec],
    results_path: Path,
    *,
    concurrency: int = 1,
    run_one: RunOne | None = None,
    progress: bool = False,
    max_infra_retries: int = 2,
) -> list[dict]:
    """Run every spec, at most `concurrency` at once. A crash becomes a crashed row
    and the matrix continues; an infra failure is rerun (fresh run id) up to
    `max_infra_retries` times, after a growing pause (the concurrency slot is held). The results file is rewritten after every spec."""
    run_one = run_one or run_spec
    semaphore = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    rows: list[dict] = []

    async def attempt(spec: RunSpec) -> dict:
        try:
            if progress:
                raw = await run_one(spec, on_event=_print_progress(spec.label))
            else:
                raw = await run_one(spec)
            return _complete(raw, spec)
        except Exception as exc:
            return _crash_row(spec, exc)

    async def run_with_retries(spec: RunSpec) -> dict:
        row = await attempt(spec)
        # Cost and model stalls add up over the reruns; the rest is the last run's.
        cost, stalls = row["cost_usd"], row["model_stalls"]
        retries = 0
        while (
            row["failure_kind"] == "infra"
            and not row["crashed"]
            and retries < max_infra_retries
        ):
            retries += 1
            await asyncio.sleep(INFRA_RETRY_PAUSE_S * retries)
            row = await attempt(replace(spec, attempt=retries))
            cost += row["cost_usd"]
            stalls += row["model_stalls"]
        row["infra_retries"] = retries
        row["cost_usd"] = cost
        row["model_stalls"] = stalls
        return row

    async def worker(spec: RunSpec) -> None:
        async with semaphore:
            row = await run_with_retries(spec)
        async with lock:
            rows.append(row)
            rows.sort(key=lambda r: (r["repeat"], r["task_id"]))
            try:
                results_path.write_text(json.dumps(rows, indent=2))
            finally:
                # A status line is display only; a closed pipe must not abort runs.
                try:
                    print(_status_line(row), flush=True)
                except (OSError, ValueError):  # ValueError: closed stdout
                    pass

    # A failing worker (for example an unwritable results file) must not cancel
    # the runs still in flight: wait for all of them, then raise the first error.
    results = await asyncio.gather(*(worker(s) for s in specs), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return rows
