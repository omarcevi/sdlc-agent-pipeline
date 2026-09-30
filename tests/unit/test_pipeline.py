from pathlib import Path

import pytest

from app.driver import run_pipeline
from app.environment.base import ExecResult, InfraError
from app.models import RoleModels
from app.nodes import intake
from app.pipeline import build_workflow
from app.schemas import PatchResult, Plan, Review, RunRequest
from tests.fakes import FakeEnvironment, FakeLlm, call, json_out, make_bench_task

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
