"""Runs one pipeline instance end to end and always releases its sandbox.

A live run pauses at the approval gate: the first pass ends with a pending
`adk_request_input` call. The driver then releases the sandbox, asks the approver,
and resumes the same session with the decision as that call's function response.
"""

import asyncio
import logging
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import aiohttp
import httpx
from google.adk.apps import App
from google.adk.events import Event
from google.adk.plugins import ReflectAndRetryModelPlugin
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import InMemoryRunner
from google.adk.workflow import Workflow
from google.genai import errors as genai_errors
from google.genai import types
from opentelemetry import trace
from pydantic import ValidationError

from app.approval import ApprovalDecision, ApprovalRequest, Approver
from app.budget import BudgetExceeded, BudgetPlugin
from app.environment import registry
from app.environment.base import InfraError
from app.github_client import GitHubError
from app.guardrails import GuardrailPlugin
from app.models import RoleModels
from app.nodes.finish import runs_dir
from app.nodes.intake import RunRefused
from app.pipeline import build_workflow
from app.schemas import FailureKind, Plan, Review, RunRecord, RunRequest, SoloResult
from app.tracing import ROOT_SPAN_NAME, TRACER_NAME

USER_ID = "bench"
logger = logging.getLogger(__name__)

# The function call ADK emits for a RequestInput. tests/unit/test_human_gate.py
# pins it on a minimal workflow.
REQUEST_INPUT = "adk_request_input"
RESUME_TIMEOUT_S = 300.0  # the cap on the pass after the approval (delivery only)
APPROVAL_TIMED_OUT = ApprovalDecision(
    approved=False, approver="", patch_sha256="", note="approval timed out"
)


def _positive_seconds(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not value > 0 or value == float("inf"):
        raise ValueError(f"{name} must be a positive number, got {raw!r}")
    return value


def approval_timeout_s() -> float:
    """How long the driver waits for the approver, from APPROVAL_TIMEOUT_S (default
    3600 s). Anything that is not a positive number is a configuration error."""
    raw = os.environ.get("APPROVAL_TIMEOUT_S", "3600")
    return _positive_seconds("APPROVAL_TIMEOUT_S", raw)


def run_timeout_s() -> float:
    """The wall-clock cap for one run, from RUN_TIMEOUT_S (default 1500 s, below the
    sandbox TTL so the driver releases the sandbox first). Anything that is not a
    positive number is a configuration error."""
    raw = os.environ.get("RUN_TIMEOUT_S", "1500")
    value = _positive_seconds("RUN_TIMEOUT_S", raw)
    ttl_raw = os.environ.get("SANDBOX_TTL_S", "1800")  # the Docker backend's default
    try:
        ttl = float(ttl_raw)
    except ValueError:
        return value  # the backend rejects a bad SANDBOX_TTL_S itself
    if value >= ttl:
        raise ValueError(
            f"RUN_TIMEOUT_S ({raw}) must stay below SANDBOX_TTL_S ({ttl_raw}) so the "
            "driver releases the sandbox before it expires"
        )
    return value


class RunCrashed(Exception):
    """The pipeline hit a bug the driver cannot classify. `record` holds the run's
    tokens, cost and tool calls; the original exception is the `__cause__`."""

    def __init__(self, record: RunRecord) -> None:
        super().__init__(record.reason)
        self.record = record


# The native Gemini client raises APIError for HTTP error statuses and lets
# transport failures through from whichever HTTP library it is using.
_MODEL_API_ERRORS = (genai_errors.APIError, httpx.HTTPError, aiohttp.ClientError)
# ReflectAndRetryModelPlugin gives up with a bare RuntimeError carrying this text.
# tests/unit/test_pipeline.py runs the real plugin to its limit, so a change of
# wording in ADK shows up as a failing test.
_RETRIES_EXHAUSTED = "The model has failed consecutively"


class _ActiveAgentTracker(BasePlugin):
    """Remembers whether an LLM agent is mid-turn, per session.

    Output-schema validation of an LLM node fails inside the workflow wrapper
    before the agent's after_agent callback runs, so "an agent is still active"
    distinguishes malformed model output from validation errors raised by
    deterministic nodes.
    """

    def __init__(self) -> None:
        super().__init__(name="active_agent_tracker")
        self._active: dict[str, str] = {}
        self._last_finished: dict[str, str] = {}

    def active_agent(self, session_id: str) -> str | None:
        return self._active.get(session_id)

    def last_finished_agent(self, session_id: str) -> str | None:
        return self._last_finished.get(session_id)

    async def before_agent_callback(self, *, agent: Any, callback_context: Any) -> None:
        self._active[callback_context.session.id] = agent.name

    async def after_agent_callback(self, *, agent: Any, callback_context: Any) -> None:
        session_id = callback_context.session.id
        self._active.pop(session_id, None)
        self._last_finished[session_id] = agent.name


def build_plugins(budget: BudgetPlugin, tracker: BasePlugin) -> list[BasePlugin]:
    """Runner-wide plugins in the same order as app/agent.py, plus the tracker:
    budget, tracker, malformed-function-call retry, guardrails."""
    return [
        budget,
        tracker,
        ReflectAndRetryModelPlugin(max_retries=2),
        GuardrailPlugin(),
    ]


def _refusal(exc: BaseException) -> str | None:
    """The reason of a RunRefused anywhere in the exception chain."""
    for error in _chain(exc):
        if isinstance(error, RunRefused):
            return str(error)
    return None


def _chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_model_api_error(error: BaseException) -> bool:
    """True for a failed call to a model provider: an API error or a transport error."""
    if isinstance(error, _MODEL_API_ERRORS):
        return True
    # LiteLLM's errors subclass openai.OpenAIError. Matching the class by name
    # avoids importing litellm here, which takes several seconds.
    return any(
        cls.__name__ == "OpenAIError" and cls.__module__.partition(".")[0] == "openai"
        for cls in type(error).__mro__
    )


# Router nodes that consume an LLM agent's structured answer:
# router -> (producing agent, the router's input schema). An LLM node that gets no
# usable answer completes with no output and its after_agent callback runs. Then
# either the scheduler's validation fails, titled "dynamic node '<router>'" (a
# content object without usable text), or, when the response has no content
# object at all, the node output is None, the scheduler skips its validation, and
# the router's own validation fails, titled with the schema name and with input None.
_ANSWER_CONSUMERS: dict[str, tuple[str, type]] = {
    "route_plan": ("planner", Plan),
    "route_review": ("reviewer", Review),
    "route_solo": ("solo", SoloResult),
}


def _input_is_none(error: ValidationError) -> bool:
    errors = error.errors()
    return len(errors) == 1 and errors[0]["loc"] == () and errors[0]["input"] is None


def missing_answer_agent(exc: BaseException, last_finished: str | None) -> str | None:
    """The LLM agent whose answer is missing: `exc` is the input validation of the
    router that consumes that agent's output, and that agent finished last."""
    if last_finished is None:
        return None
    for error in _chain(exc):
        if not isinstance(error, ValidationError):
            continue
        for router, (agent, schema) in _ANSWER_CONSUMERS.items():
            if agent != last_finished:
                continue
            if error.title == f"dynamic node '{router}'":
                return agent
            if error.title == schema.__name__ and _input_is_none(error):
                return agent
    return None


def classify_failure(
    exc: BaseException,
    *,
    llm_agent_active: bool,
    missing_answer_from: str | None = None,
) -> tuple[FailureKind, str]:
    """Map an exception escaping the workflow to a failure kind; re-raise bugs."""
    for error in _chain(exc):
        if isinstance(error, BudgetExceeded):
            return "budget", str(error)
        if isinstance(error, InfraError):
            return "infra", str(error)
        if isinstance(error, GitHubError):
            # Only open_pr lets one escape (live intake turns them into refusals, the
            # failure comment logs them). A fixed reason: no response text.
            return "infra", "GitHub refused the pull request"
        if isinstance(error, genai_errors.ClientError) and error.code in (400, 413):
            # The agent built a request the model cannot take, e.g. a context overflow.
            return "agent", f"model rejected the request: {error.code} {error.message}"
        if _is_model_api_error(error):
            return "infra", f"{type(error).__name__}: {error}"
        if isinstance(error, RuntimeError) and _RETRIES_EXHAUSTED in str(error):
            return "agent", "malformed function calls: retry limit exceeded"
        if llm_agent_active and isinstance(error, ValidationError):
            return "agent", f"malformed model output: {error.errors()[0]['msg']}"
    if missing_answer_from is not None:
        return "agent", f"model returned no structured answer ({missing_answer_from})"
    raise exc


def _classify_or_crash(
    exc: Exception, tracker: _ActiveAgentTracker, session_id: str
) -> tuple[tuple[FailureKind, str], Exception | None]:
    try:
        return (
            classify_failure(
                exc,
                llm_agent_active=tracker.active_agent(session_id) is not None,
                missing_answer_from=missing_answer_agent(
                    exc, tracker.last_finished_agent(session_id)
                ),
            ),
            None,
        )
    except Exception:
        # A bug, not a run failure. Keep the run's numbers and raise after
        # record.json is written.
        return ("infra", f"unhandled {type(exc).__name__}: {exc}"), exc


async def run_pipeline(
    request: RunRequest,
    *,
    workflow: Workflow | None = None,
    tracer: trace.Tracer | None = None,
    on_event: Callable[[Event], None] | None = None,
    approver: Approver | None = None,
) -> RunRecord:
    """Run one pipeline inside a root span that carries the run's outcome.

    `on_event`, when given, sees every event after its line is in events.jsonl.
    It is display-only: an exception from it is logged once and never affects the run.
    `approver` answers the live graph's approval gate; a run that reaches the gate
    without one ends at once as an infra failure.
    """
    tracer = tracer or trace.get_tracer(TRACER_NAME)
    with tracer.start_as_current_span(
        ROOT_SPAN_NAME,
        attributes={"task_id": request.subject_id, "run_id": request.run_id},
    ) as span:
        try:
            record = await _run(request, workflow, on_event, approver)
        except RunCrashed as crashed:
            _set_record_attributes(span, crashed.record)
            raise
        _set_record_attributes(span, record)
    return record


def _set_record_attributes(span: trace.Span, record: RunRecord) -> None:
    span.set_attributes(
        {
            "outcome": record.outcome,
            "failure_kind": record.failure_kind,
            "cost_usd": record.cost_usd,
            "tool_calls": record.tool_calls,
            "tokens_in": record.tokens_in,
            "tokens_out": record.tokens_out,
            "test_attempts": record.test_attempts,
            "review_rounds": record.review_rounds,
        }
    )


class _EventLog:
    """Writes each event to events.jsonl, then shows it to `on_event`. The first
    pass truncates the file; the pass after an approval appends to it."""

    def __init__(self, path: Path, on_event: Callable[[Event], None] | None) -> None:
        self.path = path
        self._on_event = on_event
        self._warned = False

    async def drain(
        self, runner: InMemoryRunner, session_id: str, message: types.Content, mode: str
    ) -> list[Event]:
        events: list[Event] = []
        with self.path.open(mode) as log:
            async for event in runner.run_async(
                user_id=USER_ID, session_id=session_id, new_message=message
            ):
                log.write(event.model_dump_json(exclude_none=True) + "\n")
                log.flush()
                events.append(event)
                self._show(event)
        return events

    def _show(self, event: Event) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logger.warning(
                    "on_event callback failed; further failures ignored: %s: %s",
                    type(exc).__name__,
                    exc,
                )


def _pending_approval(events: list[Event]) -> types.FunctionCall | None:
    """The `adk_request_input` call the run stopped at: a long-running call of that
    name with no later function response for it."""
    pending: dict[str, types.FunctionCall] = {}
    for event in events:
        long_running = event.long_running_tool_ids or set()
        for call in event.get_function_calls():
            if call.name == REQUEST_INPUT and call.id in long_running:
                pending[call.id] = call
        for response in event.get_function_responses():
            pending.pop(response.id, None)
    return next(iter(pending.values()), None)


async def _release_sandbox(sandbox_id: str | None) -> None:
    if not sandbox_id:
        return
    # A failed release must not replace the run's real outcome or stop record.json
    # from being written. The sandbox removes itself at its TTL.
    try:
        await registry.release(sandbox_id)
    except Exception as exc:
        logger.warning(
            "could not release sandbox %s: %s: %s",
            sandbox_id,
            type(exc).__name__,
            exc,
        )


async def _session_state(runner: InMemoryRunner, session_id: str) -> dict:
    session = await runner.session_service.get_session(
        app_name="app", user_id=USER_ID, session_id=session_id
    )
    return dict(session.state) if session else {}


async def _ask(
    approver: Approver, call: types.FunctionCall, limit_s: float
) -> ApprovalDecision:
    """The approver's decision on the gate's request; no answer within `limit_s`
    rejects. A timeout names no approver, so no one is mentioned on the issue."""
    request = ApprovalRequest.model_validate((call.args or {}).get("payload"))
    try:
        async with asyncio.timeout(limit_s) as cap:
            return ApprovalDecision.model_validate(await approver(request))
    except TimeoutError:
        if not cap.expired():
            raise
        return APPROVAL_TIMED_OUT


async def _resume(
    runner: InMemoryRunner,
    session_id: str,
    call_id: str,
    decision: ApprovalDecision,
    log: _EventLog,
    tracker: _ActiveAgentTracker,
) -> tuple[tuple[FailureKind, str] | None, Exception | None]:
    """Continue the paused session with the decision as the gate call's response:
    route_approval, then open_pr or report_failure. Returns (failure, crash)."""
    message = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id, name=REQUEST_INPUT, response=decision.model_dump()
                )
            )
        ],
    )
    try:
        async with asyncio.timeout(RESUME_TIMEOUT_S) as cap:
            await log.drain(runner, session_id, message, "a")
    except TimeoutError as exc:
        if cap.expired():
            # No model runs after the gate: a stalled delivery is infra, not spend.
            reason = f"resumed run exceeded {RESUME_TIMEOUT_S:g} s wall clock"
            return ("infra", reason), None
        return _classify_or_crash(exc, tracker, session_id)
    except Exception as exc:
        return _classify_or_crash(exc, tracker, session_id)
    return None, None


async def _run(
    request: RunRequest,
    workflow: Workflow | None,
    on_event: Callable[[Event], None] | None = None,
    approver: Approver | None = None,
) -> RunRecord:
    timeout_s = run_timeout_s()
    approval_limit_s = approval_timeout_s()
    budget = BudgetPlugin()
    tracker = _ActiveAgentTracker()
    app = App(
        name="app",
        root_agent=workflow or build_workflow(RoleModels.from_env()),
        plugins=build_plugins(budget, tracker),
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name="app", user_id=USER_ID
    )
    run_dir = runs_dir() / request.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log = _EventLog(run_dir / "events.jsonl", on_event)
    message = types.Content(
        role="user", parts=[types.Part.from_text(text=request.model_dump_json())]
    )

    started = time.monotonic()
    waited_s = 0.0
    paused = False
    failure: tuple[FailureKind, str] | None = None
    refused: str | None = None
    crash: Exception | None = None
    try:
        events: list[Event] = []
        try:
            # The cap bounds the whole event loop; on expiry the in-flight work (a
            # hung model call included) is cancelled. asyncio.timeout turns only its
            # own expiry into TimeoutError, so an outer cancellation still propagates.
            async with asyncio.timeout(timeout_s) as cap:
                events = await log.drain(runner, session.id, message, "w")
        except TimeoutError as exc:
            if cap.expired():
                failure = ("budget", f"run exceeded {timeout_s:g} s wall clock")
            else:
                failure, crash = _classify_or_crash(exc, tracker, session.id)
        except Exception as exc:
            # Checked before classification: a refusal is no failure of the system.
            refused = _refusal(exc)
            if refused is None:
                failure, crash = _classify_or_crash(exc, tracker, session.id)

        stopped = failure is not None or refused is not None
        call = None if stopped else _pending_approval(events)
        if call is not None:
            paused = True
            # Nothing after the gate needs the sandbox; release it before waiting.
            state = await _session_state(runner, session.id)
            await _release_sandbox(state.get("sandbox_id"))
            if approver is None:
                failure = ("infra", "approval needed but no approver")
            else:
                asked = time.monotonic()
                decision: ApprovalDecision | None = None
                try:
                    decision = await _ask(approver, call, approval_limit_s)
                except Exception as exc:
                    failure, crash = _classify_or_crash(exc, tracker, session.id)
                waited_s = time.monotonic() - asked
                if decision is not None:
                    failure, crash = await _resume(
                        runner, session.id, call.id, decision, log, tracker
                    )
    finally:
        state = await _session_state(runner, session.id)
        # Already released at the gate when the run paused; releasing is idempotent.
        await _release_sandbox(state.get("sandbox_id"))

    outcome = state.get("outcome") or {}
    if paused and outcome.get("outcome") == "patch_written":
        # deliver_patch's outcome: the run never got past the approval gate.
        outcome = {}
    if refused is not None:
        outcome = {
            "outcome": "refused",
            "failure_kind": "none",
            "reason": refused,
            "patch_path": None,
        }
    elif failure is not None:
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
    issue = state.get("issue") or {}
    record = RunRecord(
        task_id=request.subject_id,
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
        duration_s=round(time.monotonic() - started - waited_s, 2),
        mode=request.mode,
        base_ref=issue.get("base_ref"),
        base_sha=issue.get("base_sha"),
        base_tree_sha=issue.get("base_tree_sha"),
        pr_url=outcome.get("pr_url"),
        approval_wait_s=round(waited_s, 2),
        comment_posted=bool(outcome.get("comment_posted", False)),
    )
    (run_dir / "record.json").write_text(record.model_dump_json(indent=2))
    if crash is not None:
        raise RunCrashed(record) from crash
    return record
