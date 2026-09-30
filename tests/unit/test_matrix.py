import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from google.adk.events import Event
from google.genai import types

from app.models import RoleModels
from app.nodes import intake
from app.nodes.verify import TEST_CMD
from app.pipeline import build_workflow
from app.task_store import TaskSpec, load_task
from bench import matrix
from bench.matrix import RunSpec, plan_runs, run_matrix, run_spec
from bench.presets import workflow_for
from tests.fakes import FakeEnvironment, FakeLlm, json_out
from tests.unit.test_pipeline import (  # noqa: F401  (bench is a fixture)
    APPROVE,
    PASS,
    PATCH,
    PLAN,
    bench,
    diff_responses,
    use_env,
)


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


async def test_concurrent_repeats_do_not_share_run_dirs(bench, monkeypatch):  # noqa: F811
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
        "infra_retries", "crashed",
    }  # fmt: skip


async def test_a_scoring_crash_keeps_the_record_numbers(bench, monkeypatch):  # noqa: F811
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
