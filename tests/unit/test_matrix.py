import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from google.adk.events import Event
from google.genai import types

from app.environment.base import InfraError
from app.models import RoleModels
from app.nodes import intake
from app.nodes.verify import TEST_CMD
from app.pipeline import build_workflow
from app.task_store import TaskSpec, load_task
from bench import matrix
from bench.matrix import RunSpec, plan_runs, run_matrix, run_spec
from bench.presets import workflow_for
from tests.fakes import STALL, FakeEnvironment, FakeLlm, fake_gemini, json_out
from tests.unit.test_pipeline import (
    APPROVE,
    PASS,
    PATCH,
    PLAN,
    diff_responses,
    gemini_answer,
    use_env,
)


@pytest.fixture(autouse=True)
def no_infra_retry_pause(monkeypatch):
    monkeypatch.setattr(matrix, "INFRA_RETRY_PAUSE_S", 0)


def spec(task_id: str, category: str = "bug") -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        repo="mini",
        title="t",
        body="b",
        category=category,
        difficulty="easy",
        split="dev",
    )


def row(cost: float = 0.25, failure_kind: str = "none", **extra) -> dict:
    return {
        "resolved": failure_kind == "none",
        "outcome": "patch_written" if failure_kind == "none" else "failed",
        "failure_kind": failure_kind,
        "cost_usd": cost,
        "duration_s": 1.0,
        **extra,
    }


def specs_for(*task_ids: str, repeats: int = 1) -> list[RunSpec]:
    return plan_runs([spec(t) for t in task_ids], "multi", "flash", repeats, "s")


def fake_workflow(system: str, preset: str):
    return build_workflow(
        RoleModels(
            planner=FakeLlm([json_out(PLAN)]),
            coder=FakeLlm([json_out(PATCH)]),
            reviewer=FakeLlm([json_out(APPROVE)]),
        )
    )


def test_plan_runs_orders_by_repeat_then_task():
    planned = specs_for("a", "b", repeats=2)
    assert [(s.repeat, s.task.task_id) for s in planned] == [
        (1, "a"),
        (1, "b"),
        (2, "a"),
        (2, "b"),
    ]


def test_run_ids_and_labels_are_unique_per_spec():
    planned = specs_for("a", "b", repeats=2)
    assert len({s.run_id for s in planned}) == 4
    assert len({s.label for s in planned}) == 4
    first = planned[0]
    assert first.label == "a/multi/flash/r1"
    assert first.run_id == "a-multi-flash-r1-s"
    assert replace(first, attempt=2).run_id == "a-multi-flash-r1-s-retry2"


def test_variant_makes_run_ids_unique_and_leaves_old_ids_unchanged():
    base = specs_for("a")[0]
    assert (base.label, base.run_id) == ("a/multi/flash/r1", "a-multi-flash-r1-s")
    one = replace(base, system="review", variant="rp-01")
    two = replace(base, system="review", variant="rp-02")
    assert one.label == "a/review/flash/rp-01/r1"
    assert one.run_id == "a-review-flash-rp-01-r1-s"
    assert one.run_id != two.run_id and one.label != two.label
    assert replace(one, attempt=2).run_id == "a-review-flash-rp-01-r1-s-retry2"


def test_workflow_for_rejects_an_unknown_system():
    with pytest.raises(ValueError, match="unknown system"):
        workflow_for("triple", "flash")


async def test_concurrency_limit_is_respected(tmp_path):
    active = peak = 0

    async def run_one(s: RunSpec) -> dict:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return row()

    rows = await run_matrix(
        specs_for("a", "b", "c", "d", "e", "f"),
        tmp_path / "r.json",
        concurrency=2,
        run_one=run_one,
    )
    assert peak == 2 and len(rows) == 6


async def test_concurrent_repeats_do_not_share_run_dirs(bench, monkeypatch):
    async def is_resolved(task, record):
        return True

    monkeypatch.setattr(matrix, "is_resolved", is_resolved)
    sandboxes: list[FakeEnvironment] = []

    async def fake_start():
        env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
        sandboxes.append(env)
        return env

    monkeypatch.setattr(intake, "start_environment", fake_start)

    async def run_one(s: RunSpec, on_event=None) -> dict:
        return await run_spec(s, on_event=on_event, workflow_factory=fake_workflow)

    planned = plan_runs([load_task("t-1")], "multi", "flash", 2, "s")
    rows = await run_matrix(
        planned, bench / "results.json", concurrency=2, run_one=run_one
    )
    assert [r["resolved"] for r in rows] == [True, True]
    assert len({r["run_id"] for r in rows}) == 2
    for r in rows:
        run_dir = bench / "runs" / r["run_id"]
        assert (run_dir / "record.json").exists()
        assert (run_dir / "patch.diff").exists()
        assert Path(r["patch_path"]).parent == run_dir
    assert len({env.env_id for env in sandboxes}) == 2
    assert all(env.closed for env in sandboxes)


async def test_infra_failures_are_retried_then_recorded(tmp_path):
    seen: list[str] = []
    outcomes = [row(0.1, "infra"), row(0.2, "infra"), row(0.3)]

    async def flaky(s: RunSpec) -> dict:
        seen.append(s.run_id)
        return outcomes.pop(0)

    (only,) = await run_matrix(
        specs_for("a"), tmp_path / "r.json", run_one=flaky, max_infra_retries=2
    )
    assert seen == [
        "a-multi-flash-r1-s",
        "a-multi-flash-r1-s-retry1",
        "a-multi-flash-r1-s-retry2",
    ]
    assert only["infra_retries"] == 2 and only["resolved"] is True
    assert only["cost_usd"] == pytest.approx(0.6)

    async def always_infra(s: RunSpec) -> dict:
        return {**row(0.1, "infra"), "reason": "docker down"}

    (only,) = await run_matrix(
        specs_for("a"), tmp_path / "r2.json", run_one=always_infra, max_infra_retries=2
    )
    assert (only["outcome"], only["failure_kind"]) == ("failed", "infra")
    assert only["infra_retries"] == 2
    assert only["cost_usd"] == pytest.approx(0.3)
    assert only["crashed"] is False


async def test_model_stalls_add_up_over_infra_reruns_like_cost(tmp_path):
    stalled = {**row(0.1, "infra"), "reason": "model call stalled twice: ..."}
    outcomes = [{**stalled, "model_stalls": 2}, {**row(0.3), "model_stalls": 1}]

    async def flaky(s: RunSpec) -> dict:
        return outcomes.pop(0)

    (only,) = await run_matrix(specs_for("a"), tmp_path / "r.json", run_one=flaky)
    assert only["infra_retries"] == 1 and only["resolved"] is True
    assert only["model_stalls"] == 3


async def test_infra_reruns_wait_a_growing_pause_first(tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "INFRA_RETRY_PAUSE_S", 30.0)
    events: list = []

    async def fake_sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    monkeypatch.setattr(matrix.asyncio, "sleep", fake_sleep)
    outcomes = [row(0.1, "infra"), row(0.2, "infra"), row(0.3)]

    async def flaky(s: RunSpec) -> dict:
        events.append(("run", s.attempt))
        return outcomes.pop(0)

    (only,) = await run_matrix(
        specs_for("a"), tmp_path / "r.json", run_one=flaky, max_infra_retries=2
    )
    assert events == [
        ("run", 0),
        ("sleep", 30.0),
        ("run", 1),
        ("sleep", 60.0),
        ("run", 2),
    ]
    assert only["infra_retries"] == 2 and only["resolved"] is True
    assert only["cost_usd"] == pytest.approx(0.6)


async def test_non_infra_and_crashed_rows_request_no_pause(tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "INFRA_RETRY_PAUSE_S", 30.0)
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(matrix.asyncio, "sleep", fake_sleep)

    async def run_one(s: RunSpec) -> dict:
        if s.task.task_id == "crash":
            raise RuntimeError("boom")
        return row(0.1, "budget" if s.task.task_id == "budget" else "none")

    await run_matrix(
        specs_for("ok", "budget", "crash"), tmp_path / "r.json", run_one=run_one
    )
    assert sleeps == []


async def test_a_crashing_run_is_recorded_and_the_matrix_continues(tmp_path, capsys):
    calls: list[str] = []

    async def run_one(s: RunSpec) -> dict:
        calls.append(s.task.task_id)
        if s.task.task_id == "b":
            raise RuntimeError("boom")
        return row()

    path = tmp_path / "r.json"
    rows = await run_matrix(specs_for("a", "b", "c"), path, run_one=run_one)
    assert calls == ["a", "b", "c"]  # a crashed row is not rerun
    crashed = rows[1]
    assert crashed["crashed"] is True and crashed["resolved"] is False
    assert (crashed["outcome"], crashed["failure_kind"]) == ("failed", "infra")
    assert crashed["reason"] == "unhandled RuntimeError: boom"
    assert crashed["infra_retries"] == 0
    assert json.loads(path.read_text()) == rows
    assert "b/multi/flash/r1: crashed" in capsys.readouterr().out


async def test_every_row_carries_every_field(tmp_path):
    async def run_one(s: RunSpec) -> dict:
        return {"resolved": True}

    (only,) = await run_matrix(specs_for("a"), tmp_path / "r.json", run_one=run_one)
    assert set(only) >= {
        "task_id", "category", "system", "preset", "repeat", "run_id", "resolved",
        "outcome", "failure_kind", "reason", "cost_usd", "duration_s", "tool_calls",
        "tokens_in", "tokens_out", "test_attempts", "review_rounds", "audit",
        "infra_retries", "crashed", "model_stalls",
    }  # fmt: skip
    assert only["model_stalls"] == 0


async def test_a_scoring_crash_keeps_the_record_numbers(bench, monkeypatch):
    async def broken(task, record):
        raise OSError("docker is gone")

    monkeypatch.setattr(matrix, "is_resolved", broken)
    use_env(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )
    result = await run_spec(
        RunSpec(load_task("t-1"), "multi", "flash", 1, "s"),
        workflow_factory=fake_workflow,
    )
    assert result["crashed"] and not result["resolved"]
    assert result["reason"] == "unhandled OSError: docker is gone"
    assert result["cost_usd"] > 0 and result["tokens_in"] > 0


async def test_results_file_is_rewritten_after_every_run(tmp_path):
    path = tmp_path / "r.json"
    seen: list[list[str]] = []

    async def run_one(s: RunSpec) -> dict:
        done = json.loads(path.read_text()) if path.exists() else []
        seen.append([r["task_id"] for r in done])
        return row()

    await run_matrix(specs_for("a", "b", "c"), path, run_one=run_one)
    assert seen == [[], ["a"], ["a", "b"]]


async def test_rows_are_sorted_by_repeat_then_task(tmp_path):
    async def run_one(s: RunSpec) -> dict:
        return row()

    rows = await run_matrix(
        specs_for("b", "a", repeats=2), tmp_path / "r.json", run_one=run_one
    )
    assert [(r["repeat"], r["task_id"]) for r in rows] == [
        (1, "a"),
        (1, "b"),
        (2, "a"),
        (2, "b"),
    ]


async def test_progress_lines_and_status_are_prefixed_with_the_label(tmp_path, capsys):
    part = types.Part(
        function_call=types.FunctionCall(name="read_file", args={"path": "a.py"})
    )
    event = Event(author="coder", content=types.Content(role="model", parts=[part]))

    async def run_one(s: RunSpec, on_event=None) -> dict:
        assert on_event is not None
        on_event(event)
        return row()

    await run_matrix(
        specs_for("a"), tmp_path / "r.json", run_one=run_one, progress=True
    )
    out = capsys.readouterr().out
    assert "a/multi/flash/r1     coder → read_file a.py" in out
    assert "a/multi/flash/r1: patch_written" in out


# --- A3: a crash keeps the money it spent ---


async def test_a_pipeline_crash_after_spending_shows_the_spend(bench, monkeypatch):
    use_env(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )

    def crashing_workflow(system: str, preset: str):
        # The reviewer's script is empty: it raises an AssertionError (a bug)
        # after the planner and coder have spent tokens.
        return build_workflow(
            RoleModels(
                planner=FakeLlm([json_out(PLAN)]),
                coder=FakeLlm([json_out(PATCH)]),
                reviewer=FakeLlm([]),
            )
        )

    result = await run_spec(
        RunSpec(load_task("t-1"), "multi", "flash", 1, "s"),
        workflow_factory=crashing_workflow,
    )
    assert result["crashed"] and not result["resolved"]
    assert result["reason"].startswith("unhandled AssertionError: ")
    assert (result["outcome"], result["failure_kind"]) == ("failed", "infra")
    assert result["cost_usd"] > 0 and result["tokens_in"] > 0


async def test_a_row_carries_the_runs_model_stalls(bench, monkeypatch):
    monkeypatch.setenv("RUN_TIMEOUT_S", "5")
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", "0.2")

    async def resolved(task, record):
        return True

    monkeypatch.setattr(matrix, "is_resolved", resolved)
    use_env(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )

    def stalling_workflow(system: str, preset: str):
        planner, _ = fake_gemini([STALL, gemini_answer(PLAN)])
        return build_workflow(
            RoleModels(
                planner=planner,
                coder=FakeLlm([json_out(PATCH)]),
                reviewer=FakeLlm([json_out(APPROVE)]),
            )
        )

    result = await run_spec(
        RunSpec(load_task("t-1"), "multi", "flash", 1, "s"),
        workflow_factory=stalling_workflow,
    )
    assert result["resolved"] and result["failure_kind"] == "none"
    assert result["model_stalls"] == 1


# --- A4: scoring retries infra errors ---


def _score_with(bench, monkeypatch, behaviour):
    """Run one spec whose scoring calls `behaviour`; returns (row, call count)."""
    monkeypatch.setattr(matrix, "SCORE_RETRY_PAUSE_S", 0)
    calls: list[int] = []

    async def is_resolved(task, record):
        calls.append(1)
        return behaviour(len(calls))

    monkeypatch.setattr(matrix, "is_resolved", is_resolved)
    use_env(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )
    return calls, run_spec(
        RunSpec(load_task("t-1"), "multi", "flash", 1, "s"),
        workflow_factory=fake_workflow,
    )


async def test_scoring_infra_error_is_retried_then_succeeds(bench, monkeypatch):
    def behaviour(n):
        if n == 1:
            raise InfraError("docker run failed")
        return True

    calls, pending = _score_with(bench, monkeypatch, behaviour)
    result = await pending
    assert len(calls) == 2
    assert result["resolved"] is True and result["crashed"] is False


async def test_scoring_infra_error_three_times_is_a_crashed_row(bench, monkeypatch):
    def behaviour(n):
        raise InfraError("docker run failed")

    calls, pending = _score_with(bench, monkeypatch, behaviour)
    result = await pending
    assert len(calls) == 3
    assert result["crashed"] and result["cost_usd"] > 0
    assert result["reason"] == "unhandled InfraError: docker run failed"


async def test_other_scoring_errors_are_not_retried(bench, monkeypatch):
    def behaviour(n):
        raise OSError("disk")

    calls, pending = _score_with(bench, monkeypatch, behaviour)
    result = await pending
    assert len(calls) == 1 and result["crashed"]


# --- A5: one failing write or print must not cancel paid runs ---


async def test_a_failed_results_write_does_not_stop_the_other_runs(
    tmp_path, monkeypatch
):
    real_write = Path.write_text
    writes = 0

    def flaky_write(self, *args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write)
    finished: list[str] = []

    async def run_one(s: RunSpec) -> dict:
        await asyncio.sleep(0 if s.task.task_id == "a" else 0.05)
        finished.append(s.task.task_id)
        return row()

    path = tmp_path / "r.json"
    with pytest.raises(OSError, match="disk full"):
        await run_matrix(specs_for("a", "b", "c"), path, concurrency=3, run_one=run_one)
    assert sorted(finished) == ["a", "b", "c"]
    assert len(json.loads(path.read_text())) == 3


@pytest.mark.parametrize(
    "error",
    [BrokenPipeError(), ValueError("I/O operation on closed file")],
    ids=["broken-pipe", "closed-stdout"],
)
async def test_a_broken_status_print_loses_nothing(tmp_path, monkeypatch, error):
    def broken_print(*args, **kwargs):
        raise error

    monkeypatch.setattr("builtins.print", broken_print)

    async def run_one(s: RunSpec) -> dict:
        return row()

    path = tmp_path / "r.json"
    rows = await run_matrix(specs_for("a", "b"), path, run_one=run_one)
    assert len(rows) == 2 and json.loads(path.read_text()) == rows


# --- A6: the status line says whether the run resolved ---


def test_status_line_says_resolved_or_unresolved():
    base = {
        "task_id": "tc-001",
        "system": "multi",
        "preset": "flash",
        "repeat": 1,
        "outcome": "patch_written",
        "failure_kind": "none",
        "cost_usd": 0.16,
        "duration_s": 95.0,
        "crashed": False,
        "reason": "",
    }
    assert (
        matrix._status_line({**base, "resolved": True})
        == "tc-001/multi/flash/r1: patch_written resolved (none) $0.160 95s"
    )
    assert "unresolved (none)" in matrix._status_line({**base, "resolved": False})


async def test_rows_carry_repo_difficulty_and_split(tmp_path):
    async def run_one(s: RunSpec) -> dict:
        if s.task.task_id == "b":
            raise RuntimeError("boom")
        return {"resolved": True}

    rows = await run_matrix(specs_for("a", "b"), tmp_path / "r.json", run_one=run_one)
    assert [r["crashed"] for r in rows] == [False, True]
    for r in rows:
        assert (r["repo"], r["difficulty"], r["split"]) == ("mini", "easy", "dev")
