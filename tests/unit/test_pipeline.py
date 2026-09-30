from pathlib import Path

import pytest
from pydantic import ValidationError

from app.budget import BudgetExceeded
from app.driver import classify_failure, run_pipeline
from app.environment.base import ExecResult, InfraError
from app.models import RoleModels
from app.nodes import intake
from app.pipeline import build_workflow
from app.schemas import PatchResult, Plan, Review, RunRequest
from tests.fakes import FakeEnvironment, FakeLlm, call, json_out, make_bench_task, text

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
        "git add -A && git diff": [
            ExecResult(exit_code=0, stdout=d, stderr="") for d in diffs
        ],
        "git diff --cached --numstat": [
            ExecResult(exit_code=0, stdout=n, stderr="") for n in numstats
        ],
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
    env = FakeEnvironment(responses={**diff_responses(), "python -m pytest": PASS})
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
        FakeEnvironment(responses={**diff_responses(), "python -m pytest": FAIL}),
    )
    coder = FakeLlm([json_out(PATCH)] * 4)
    record = await run(FakeLlm([json_out(PLAN)]), coder, FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.test_attempts == 4 and coder.calls == 4


async def test_review_rounds_exhaust(bench, monkeypatch):
    use_env(
        monkeypatch,
        FakeEnvironment(responses={**diff_responses(), "python -m pytest": PASS}),
    )
    coder = FakeLlm([json_out(PATCH)] * 3)
    reviewer = FakeLlm([json_out(CHANGES)] * 3)
    record = await run(FakeLlm([json_out(PLAN)]), coder, reviewer)
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.review_rounds == 3 and coder.calls == 3


async def test_empty_diff_is_sent_back_to_coder(bench, monkeypatch):
    responses = {
        **diff_responses(diffs=("", DIFF), numstats=("", "1\t1\tmini.py\n")),
        "python -m pytest": PASS,
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
    with pytest.raises(ValidationError):
        await run(FakeLlm([]), FakeLlm([]), FakeLlm([]))


async def test_unclassified_bug_reraises_and_releases_sandbox(bench, monkeypatch):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    with pytest.raises(AssertionError):
        await run(FakeLlm([json_out(PLAN)]), FakeLlm([]), FakeLlm([]))
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
