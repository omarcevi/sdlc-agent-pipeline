import asyncio
import json
import logging
from pathlib import Path

import aiohttp
import httpx
import pytest
from google.adk.models import LlmCapabilities
from google.adk.plugins import ReflectAndRetryModelPlugin
from google.genai import errors as genai_errors
from pydantic import ValidationError

from app.budget import BudgetExceeded, BudgetPlugin
from app.driver import (
    RunCrashed,
    _ActiveAgentTracker,
    build_plugins,
    classify_failure,
    missing_answer_agent,
    run_pipeline,
    run_timeout_s,
)
from app.environment.base import ExecResult, InfraError
from app.guardrails import GuardrailPlugin
from app.models import RoleModels
from app.nodes import intake
from app.nodes.verify import DIFF_CMD, NUMSTAT_CMD, TEST_CMD
from app.pipeline import build_workflow
from app.schemas import PatchResult, Plan, Review, RunRequest, SoloResult
from tests.fakes import (
    FakeEnvironment,
    FakeLlm,
    call,
    empty_response,
    json_out,
    make_bench_task,
    malformed_call,
    no_content_response,
    raises,
    text,
)

DIFF = "diff --git a/mini.py b/mini.py\n--- a/mini.py\n+++ b/mini.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
PLAN = Plan(actionable=True, summary="fix add", files_to_inspect=["mini.py"])
PATCH = PatchResult(summary="fixed add")
APPROVE = Review(verdict="approve")
CHANGES = Review(verdict="request_changes", must_fix=["add a regression test"])
PASS = ExecResult(exit_code=0, stdout="2 passed", stderr="")
FAIL = ExecResult(
    exit_code=1, stdout="FAILED tests/test_mini.py::test_add\n1 failed", stderr=""
)


def diff_responses(diffs=(DIFF,), numstats=("1\t1\tmini.py\n",)):
    return {
        DIFF_CMD: [ExecResult(exit_code=0, stdout=d, stderr="") for d in diffs],
        NUMSTAT_CMD: [ExecResult(exit_code=0, stdout=n, stderr="") for n in numstats],
    }


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    return tmp_path


def use_env(monkeypatch, env):
    async def fake_start():
        return env

    monkeypatch.setattr(intake, "start_environment", fake_start)


async def run(planner, coder, reviewer):
    models = RoleModels(planner=planner, coder=coder, reviewer=reviewer)
    return await run_pipeline(
        RunRequest(task_id="t-1", run_id="r-1"), workflow=build_workflow(models)
    )


async def test_happy_path_writes_patch(bench, monkeypatch):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    record = await run(
        FakeLlm([json_out(PLAN)]),
        FakeLlm([json_out(PATCH)]),
        FakeLlm([json_out(APPROVE)]),
    )
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    assert Path(record.patch_path).read_text() == DIFF
    assert record.cost_usd > 0 and record.tokens_in == 3000
    assert env.closed
    assert (bench / "runs" / "r-1" / "events.jsonl").stat().st_size > 0
    assert (bench / "runs" / "r-1" / "record.json").exists()


class ResponseToolFakeLlm(FakeLlm):
    """Scripted model that, like the Gemini models from make_model, reports it
    cannot pair an output schema with tools, so ADK gives the agent the
    `set_model_response` tool and takes the final answer from that call."""

    def __init__(self, steps: list[dict]) -> None:
        super().__init__(steps)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)


def respond(value) -> dict:
    return call("set_model_response", **value.model_dump())


async def test_happy_path_with_answers_given_through_set_model_response(
    bench, monkeypatch
):
    env = FakeEnvironment(
        files={"/workspace/repo/mini.py": "def add(a, b):\n    return a - b\n"},
        responses={**diff_responses(), TEST_CMD: PASS},
    )
    use_env(monkeypatch, env)
    planner = ResponseToolFakeLlm([call("read_file", path="mini.py"), respond(PLAN)])
    coder = ResponseToolFakeLlm([respond(PATCH)])
    reviewer = ResponseToolFakeLlm([respond(APPROVE)])
    record = await run(planner, coder, reviewer)
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    assert Path(record.patch_path).read_text() == DIFF
    # One model call per scripted step: the set_model_response call ends the turn.
    assert (planner.calls, coder.calls, reviewer.calls) == (2, 1, 1)
    # read_file plus one set_model_response per agent, all counted toward the caps.
    assert record.tool_calls == 4


async def test_invalid_set_model_response_arguments_can_be_corrected(
    bench, monkeypatch
):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    planner = ResponseToolFakeLlm(
        [call("set_model_response", summary=5), respond(PLAN)]
    )
    record = await run(
        planner, ResponseToolFakeLlm([respond(PATCH)]), FakeLlm([json_out(APPROVE)])
    )
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    assert planner.calls == 2


async def test_declined_issue_skips_coder(bench, monkeypatch):
    use_env(monkeypatch, FakeEnvironment())
    coder = FakeLlm([])
    record = await run(
        FakeLlm(
            [
                json_out(
                    Plan(actionable=False, summary="n/a", decline_reason="needs OAuth")
                )
            ]
        ),
        coder,
        FakeLlm([]),
    )
    assert (record.outcome, record.reason) == ("declined", "needs OAuth")
    assert coder.calls == 0


async def test_failing_tests_exhaust_after_three_returns(bench, monkeypatch):
    use_env(
        monkeypatch,
        FakeEnvironment(responses={**diff_responses(), TEST_CMD: FAIL}),
    )
    coder = FakeLlm([json_out(PATCH)] * 4)
    record = await run(FakeLlm([json_out(PLAN)]), coder, FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.test_attempts == 4 and coder.calls == 4


async def test_review_rounds_exhaust(bench, monkeypatch):
    use_env(
        monkeypatch,
        FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS}),
    )
    coder = FakeLlm([json_out(PATCH)] * 3)
    reviewer = FakeLlm([json_out(CHANGES)] * 3)
    record = await run(FakeLlm([json_out(PLAN)]), coder, reviewer)
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.review_rounds == 3 and coder.calls == 3


async def test_empty_diff_is_sent_back_to_coder(bench, monkeypatch):
    responses = {
        **diff_responses(diffs=("", DIFF), numstats=("", "1\t1\tmini.py\n")),
        TEST_CMD: PASS,
    }
    use_env(monkeypatch, FakeEnvironment(responses=responses))
    coder = FakeLlm([json_out(PATCH)] * 2)
    record = await run(FakeLlm([json_out(PLAN)]), coder, FakeLlm([json_out(APPROVE)]))
    assert record.outcome == "patch_written"
    assert coder.calls == 2 and record.test_attempts == 1


async def test_budget_cap_aborts_run_and_releases_sandbox(bench, monkeypatch):
    monkeypatch.setenv("RUN_BUDGET_USD", "0.001")
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    planner = FakeLlm([call("list_dir", path="."), json_out(PLAN)])
    record = await run(planner, FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "budget")
    assert env.closed


async def test_infra_failure_is_classified(bench, monkeypatch):
    async def broken_start():
        raise InfraError("docker daemon down")

    monkeypatch.setattr(intake, "start_environment", broken_start)
    record = await run(FakeLlm([]), FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert "docker daemon down" in record.reason


def _server_error() -> genai_errors.ServerError:
    body = {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}
    return genai_errors.ServerError(503, body)


def _record_on_disk(bench) -> dict:
    return json.loads((bench / "runs" / "r-1" / "record.json").read_text())


async def test_model_api_error_is_an_infra_failure_with_a_record(bench, monkeypatch):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    record = await run(FakeLlm([raises(_server_error())]), FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert "ServerError" in record.reason and "overloaded" in record.reason
    assert env.closed
    assert _record_on_disk(bench)["failure_kind"] == "infra"


class UnclosableEnvironment(FakeEnvironment):
    async def close(self) -> None:
        raise RuntimeError("docker rm timed out")


async def test_failing_sandbox_release_keeps_a_successful_outcome(
    bench, monkeypatch, caplog
):
    env = UnclosableEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    with caplog.at_level(logging.WARNING, logger="app.driver"):
        record = await run(
            FakeLlm([json_out(PLAN)]),
            FakeLlm([json_out(PATCH)]),
            FakeLlm([json_out(APPROVE)]),
        )
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    assert _record_on_disk(bench)["outcome"] == "patch_written"
    assert "docker rm timed out" in caplog.text and env.env_id in caplog.text


async def test_failing_sandbox_release_keeps_the_classified_failure(bench, monkeypatch):
    monkeypatch.setenv("RUN_BUDGET_USD", "0.001")
    use_env(monkeypatch, UnclosableEnvironment())
    planner = FakeLlm([call("list_dir", path="."), json_out(PLAN)])
    record = await run(planner, FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "budget")
    assert _record_on_disk(bench)["failure_kind"] == "budget"


def test_driver_plugins_are_budget_tracker_retry_guardrails():
    budget, tracker = BudgetPlugin(), _ActiveAgentTracker()
    plugins = build_plugins(budget, tracker)
    assert plugins[0] is budget and plugins[1] is tracker
    assert [type(p) for p in plugins[2:]] == [
        ReflectAndRetryModelPlugin,
        GuardrailPlugin,
    ]
    assert plugins[2].max_retries == 2


async def test_malformed_function_call_is_retried(bench, monkeypatch):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    planner = FakeLlm([malformed_call(), malformed_call(), json_out(PLAN)])
    record = await run(
        planner, FakeLlm([json_out(PATCH)]), FakeLlm([json_out(APPROVE)])
    )
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    assert planner.calls == 3
    assert record.tokens_in == 5000  # the failed calls are still paid for


async def test_malformed_function_calls_beyond_the_retry_limit_fail_the_agent(
    bench, monkeypatch
):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    planner = FakeLlm([malformed_call()] * 3)
    record = await run(planner, FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert "malformed function calls" in record.reason
    assert planner.calls == 3 and env.closed


async def test_malformed_model_output_is_an_agent_failure(bench, monkeypatch):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    record = await run(FakeLlm([text("this is not json")]), FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert "malformed model output" in record.reason
    assert env.closed


async def test_bad_task_data_is_reraised_not_blamed_on_agent(bench, monkeypatch):
    use_env(monkeypatch, FakeEnvironment())
    for task_yaml in (bench / "tasks").rglob("task.yaml"):
        task_yaml.write_text("repo: mini\n")
    with pytest.raises(RunCrashed) as crashed:
        await run(FakeLlm([]), FakeLlm([]), FakeLlm([]))
    assert isinstance(crashed.value.__cause__, ValidationError)
    assert crashed.value.record.failure_kind == "infra"


async def test_unclassified_bug_raises_run_crashed_keeps_spend_and_releases_sandbox(
    bench, monkeypatch
):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    with pytest.raises(RunCrashed) as crashed:
        await run(FakeLlm([json_out(PLAN)]), FakeLlm([]), FakeLlm([]))
    assert isinstance(crashed.value.__cause__, AssertionError)
    record = crashed.value.record
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.reason.startswith("unhandled AssertionError: ")
    assert record.tokens_in > 0 and record.cost_usd > 0
    assert _record_on_disk(bench)["cost_usd"] == record.cost_usd
    assert env.closed


def _validation_error() -> ValidationError:
    try:
        Plan.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


def test_classify_budget_through_runtime_wrapper():
    try:
        try:
            raise BudgetExceeded("over budget")
        except BudgetExceeded as inner:
            raise RuntimeError("plugin failed") from inner
    except RuntimeError as exc:
        assert classify_failure(exc, llm_agent_active=False) == (
            "budget",
            "over budget",
        )


def test_classify_infra():
    kind, reason = classify_failure(InfraError("docker down"), llm_agent_active=False)
    assert (kind, reason) == ("infra", "docker down")


def test_classify_validation_error_with_active_agent_is_agent_failure():
    kind, reason = classify_failure(_validation_error(), llm_agent_active=True)
    assert kind == "agent" and "malformed model output" in reason


def test_classify_validation_error_without_active_agent_reraises():
    with pytest.raises(ValidationError):
        classify_failure(_validation_error(), llm_agent_active=False)


def _none_input_error(schema) -> ValidationError:
    try:
        schema.model_validate(None)
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


@pytest.mark.parametrize(
    ("schema", "agent"), [(Plan, "planner"), (Review, "reviewer"), (SoloResult, "solo")]
)
def test_none_input_validation_is_a_missing_answer_only_for_its_producer(schema, agent):
    error = _none_input_error(schema)
    assert missing_answer_agent(error, agent) == agent
    assert missing_answer_agent(error, "coder") is None
    assert missing_answer_agent(error, None) is None


def test_plan_validation_error_with_a_non_none_input_is_not_a_missing_answer():
    # A deterministic bug that builds a bad Plan while the planner finished last.
    error = _validation_error()
    assert error.title == "Plan" and error.errors()[0]["input"] == {}
    assert missing_answer_agent(error, "planner") is None
    with pytest.raises(ValidationError):
        classify_failure(error, llm_agent_active=False, missing_answer_from=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_server_error(), "ServerError: 503 UNAVAILABLE"),
        (
            genai_errors.ClientError(429, {"error": {"message": "quota exceeded"}}),
            "ClientError: 429",
        ),
        (httpx.ConnectError("connection refused"), "ConnectError: connection refused"),
        (httpx.ReadTimeout("read timed out"), "ReadTimeout: read timed out"),
        (
            aiohttp.ServerDisconnectedError("server hung up"),
            "ServerDisconnectedError: server hung up",
        ),
    ],
)
def test_classify_model_api_and_transport_errors_as_infra(error, expected):
    kind, reason = classify_failure(error, llm_agent_active=True)
    assert kind == "infra" and expected in reason


def test_classify_wrapped_model_api_error_as_infra():
    try:
        try:
            raise _server_error()
        except genai_errors.ServerError as inner:
            raise RuntimeError("node failed") from inner
    except RuntimeError as exc:
        kind, reason = classify_failure(exc, llm_agent_active=True)
    assert kind == "infra" and "ServerError" in reason


def test_classify_litellm_errors_as_infra_without_importing_litellm():
    # LiteLLM's errors subclass the OpenAI SDK's. Stand-ins with the same module
    # names keep this test from importing litellm, which takes seconds.
    openai_error = type("OpenAIError", (Exception,), {"__module__": "openai"})
    rate_limit = type(
        "RateLimitError", (openai_error,), {"__module__": "litellm.exceptions"}
    )
    kind, reason = classify_failure(rate_limit("slow down"), llm_agent_active=True)
    assert (kind, reason) == ("infra", "RateLimitError: slow down")


def test_classify_does_not_treat_lookalike_errors_as_model_api_errors():
    lookalike = type("OpenAIError", (Exception,), {"__module__": "myapp.errors"})
    with pytest.raises(lookalike):
        classify_failure(lookalike("x"), llm_agent_active=False)


async def test_on_event_sees_events_in_order_after_they_are_logged(bench, monkeypatch):
    use_env(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )
    log = bench / "runs" / "r-1" / "events.jsonl"
    seen, lines_on_disk = [], []

    def on_event(event):
        seen.append(event.id)
        lines_on_disk.append(len(log.read_text().splitlines()))

    models = RoleModels(
        planner=FakeLlm([json_out(PLAN)]),
        coder=FakeLlm([json_out(PATCH)]),
        reviewer=FakeLlm([json_out(APPROVE)]),
    )
    record = await run_pipeline(
        RunRequest(task_id="t-1", run_id="r-1"),
        workflow=build_workflow(models),
        on_event=on_event,
    )
    assert record.outcome == "patch_written"
    logged = [json.loads(line)["id"] for line in log.read_text().splitlines()]
    assert seen == logged and len(seen) > 1
    # Each event's own line is on disk before on_event sees it, and already flushed.
    assert lines_on_disk == list(range(1, len(seen) + 1))


async def test_a_raising_on_event_changes_nothing_and_warns_once(
    bench, monkeypatch, caplog
):
    use_env(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )
    calls = []

    def on_event(event):
        calls.append(event)
        raise ValueError("display broke")

    models = RoleModels(
        planner=FakeLlm([json_out(PLAN)]),
        coder=FakeLlm([json_out(PATCH)]),
        reviewer=FakeLlm([json_out(APPROVE)]),
    )
    with caplog.at_level(logging.WARNING, logger="app.driver"):
        record = await run_pipeline(
            RunRequest(task_id="t-1", run_id="r-1"),
            workflow=build_workflow(models),
            on_event=on_event,
        )
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    assert len(calls) > 1
    warnings = [r for r in caplog.records if "on_event" in r.getMessage()]
    assert len(warnings) == 1 and "display broke" in warnings[0].getMessage()


def _client_error(code: int) -> genai_errors.ClientError:
    body = {"error": {"code": code, "message": "too big", "status": "X"}}
    return genai_errors.ClientError(code, body)


async def test_model_request_rejected_is_an_agent_failure(bench, monkeypatch):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    record = await run(FakeLlm([raises(_client_error(400))]), FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert "model rejected the request" in record.reason
    assert env.closed


async def test_model_rate_limit_is_an_infra_failure(bench, monkeypatch):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    record = await run(FakeLlm([raises(_client_error(429))]), FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "infra")


@pytest.mark.parametrize(
    ("code", "kind"), [(400, "agent"), (413, "agent"), (403, "infra"), (429, "infra")]
)
def test_classify_client_error_by_http_code(code, kind):
    got_kind, reason = classify_failure(_client_error(code), llm_agent_active=True)
    assert got_kind == kind
    if kind == "agent":
        assert reason.startswith(f"model rejected the request: {code} ")


@pytest.mark.parametrize("error_code", [None, "MAX_TOKENS"])
async def test_no_content_planner_reply_is_an_agent_failure(
    bench, monkeypatch, error_code
):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    record = await run(
        FakeLlm([no_content_response(error_code)]), FakeLlm([]), FakeLlm([])
    )
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.reason == "model returned no structured answer (planner)"
    assert record.tokens_in > 0 and record.cost_usd > 0 and env.closed


@pytest.mark.parametrize("error_code", [None, "MAX_TOKENS"])
async def test_no_content_reviewer_reply_is_an_agent_failure(
    bench, monkeypatch, error_code
):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    record = await run(
        FakeLlm([json_out(PLAN)]),
        FakeLlm([json_out(PATCH)]),
        FakeLlm([no_content_response(error_code)]),
    )
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.reason == "model returned no structured answer (reviewer)"
    assert record.tokens_in > 0 and record.cost_usd > 0 and env.closed


async def test_empty_planner_answer_is_an_agent_failure(bench, monkeypatch):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    record = await run(FakeLlm([empty_response()]), FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.reason == "model returned no structured answer (planner)"
    assert record.tokens_in > 0 and record.cost_usd > 0 and env.closed


async def test_empty_reviewer_answer_is_an_agent_failure(bench, monkeypatch):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    record = await run(
        FakeLlm([json_out(PLAN)]),
        FakeLlm([json_out(PATCH)]),
        FakeLlm([empty_response()]),
    )
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.reason == "model returned no structured answer (reviewer)"
    assert record.tokens_in > 0 and env.closed


async def test_empty_coder_answer_is_still_tolerated(bench, monkeypatch):
    env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    use_env(monkeypatch, env)
    record = await run(
        FakeLlm([json_out(PLAN)]),
        FakeLlm([empty_response()]),
        FakeLlm([json_out(APPROVE)]),
    )
    assert record.outcome == "patch_written", record.reason


# --- wall-clock cap per run ---


class HangingLlm(FakeLlm):
    """A model call that never returns, like a stalled provider connection."""

    def __init__(self, steps: list[dict]) -> None:
        super().__init__(steps)

    async def generate_content_async(self, llm_request, stream=False):
        await asyncio.Event().wait()
        yield  # pragma: no cover


async def test_run_exceeding_the_wall_clock_cap_is_a_budget_failure(bench, monkeypatch):
    monkeypatch.setenv("RUN_TIMEOUT_S", "0.3")
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    record = await run(HangingLlm([]), FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "budget")
    assert record.reason == "run exceeded 0.3 s wall clock"
    assert env.closed
    assert _record_on_disk(bench)["failure_kind"] == "budget"


async def test_wall_clock_failure_keeps_the_runs_spend(bench, monkeypatch):
    monkeypatch.setenv("RUN_TIMEOUT_S", "0.5")
    use_env(
        monkeypatch,
        FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS}),
    )
    record = await run(
        FakeLlm([json_out(PLAN)]), FakeLlm([json_out(PATCH)]), HangingLlm([])
    )
    assert record.failure_kind == "budget"
    assert record.tokens_in > 0 and record.cost_usd > 0


async def test_default_wall_clock_cap_leaves_a_normal_run_unchanged(bench, monkeypatch):
    monkeypatch.delenv("RUN_TIMEOUT_S", raising=False)
    assert run_timeout_s() == 1500.0

    use_env(
        monkeypatch,
        FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS}),
    )
    record = await run(
        FakeLlm([json_out(PLAN)]),
        FakeLlm([json_out(PATCH)]),
        FakeLlm([json_out(APPROVE)]),
    )
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")


@pytest.mark.parametrize("value", ["0", "-1", "abc", "", "nan", "inf"])
async def test_bad_run_timeout_fails_before_anything_runs(bench, monkeypatch, value):
    monkeypatch.setenv("RUN_TIMEOUT_S", value)
    started = []

    async def fake_start():
        started.append(1)
        return FakeEnvironment()

    monkeypatch.setattr(intake, "start_environment", fake_start)
    with pytest.raises(ValueError, match="RUN_TIMEOUT_S"):
        await run(FakeLlm([]), FakeLlm([]), FakeLlm([]))
    assert started == []
    assert not (bench / "runs" / "r-1").exists()


async def test_outer_cancellation_still_propagates_and_releases_the_sandbox(
    bench, monkeypatch
):
    monkeypatch.setenv("RUN_TIMEOUT_S", "60")
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    task = asyncio.create_task(run(HangingLlm([]), FakeLlm([]), FakeLlm([])))
    await asyncio.sleep(0.5)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.closed
