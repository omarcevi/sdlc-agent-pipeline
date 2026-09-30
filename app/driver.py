"""Runs one pipeline instance end to end and always releases its sandbox."""

import time
from collections.abc import Iterator

from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.adk.workflow import Workflow
from google.genai import types
from pydantic import ValidationError

from app.budget import BudgetExceeded, BudgetPlugin
from app.environment import registry
from app.environment.base import InfraError
from app.guardrails import GuardrailPlugin
from app.models import RoleModels
from app.nodes.finish import runs_dir
from app.pipeline import build_workflow
from app.schemas import FailureKind, RunRecord, RunRequest

USER_ID = "bench"


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def classify_failure(exc: BaseException) -> tuple[FailureKind, str]:
    """Map an exception escaping the workflow to a failure kind; re-raise bugs."""
    for error in _chain(exc):
        if isinstance(error, BudgetExceeded):
            return "budget", str(error)
        if isinstance(error, InfraError):
            return "infra", str(error)
        if isinstance(error, ValidationError):
            return "agent", f"malformed model output: {error.errors()[0]['msg']}"
    raise exc


async def run_pipeline(
    request: RunRequest, *, workflow: Workflow | None = None
) -> RunRecord:
    budget = BudgetPlugin()
    app = App(
        name="app",
        root_agent=workflow or build_workflow(RoleModels.from_env()),
        plugins=[budget, GuardrailPlugin()],
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name="app", user_id=USER_ID
    )
    run_dir = runs_dir() / request.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    message = types.Content(
        role="user", parts=[types.Part.from_text(text=request.model_dump_json())]
    )

    started = time.monotonic()
    failure: tuple[FailureKind, str] | None = None
    try:
        with (run_dir / "events.jsonl").open("w") as log:
            async for event in runner.run_async(
                user_id=USER_ID, session_id=session.id, new_message=message
            ):
                log.write(event.model_dump_json(exclude_none=True) + "\n")
    except Exception as exc:
        failure = classify_failure(exc)
    finally:
        final = await runner.session_service.get_session(
            app_name="app", user_id=USER_ID, session_id=session.id
        )
        state = dict(final.state) if final else {}
        if sandbox_id := state.get("sandbox_id"):
            await registry.release(sandbox_id)

    outcome = state.get("outcome") or {}
    if failure is not None:
        outcome = {
            "outcome": "failed",
            "failure_kind": failure[0],
            "reason": failure[1],
            "patch_path": None,
        }
    elif not outcome:
        outcome = {
            "outcome": "failed",
            "failure_kind": "infra",
            "reason": "workflow ended without an outcome",
            "patch_path": None,
        }

    usage = budget.usage(session.id)
    record = RunRecord(
        task_id=request.task_id,
        run_id=request.run_id,
        outcome=outcome["outcome"],
        failure_kind=outcome["failure_kind"],
        reason=outcome["reason"],
        patch_path=outcome["patch_path"],
        test_attempts=state.get("test_attempts", 0),
        review_rounds=state.get("review_rounds", 0),
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        cost_usd=round(usage.cost_usd, 4),
        tool_calls=usage.tool_calls,
        duration_s=round(time.monotonic() - started, 2),
    )
    (run_dir / "record.json").write_text(record.model_dump_json(indent=2))
    return record
