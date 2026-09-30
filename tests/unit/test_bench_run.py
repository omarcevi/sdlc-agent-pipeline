import json

import pytest

from app.schemas import RunRecord
from app.task_store import TaskSpec
from bench import run as bench_run
from bench.run import main, run_task, run_tasks


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


def ok_row(task: TaskSpec) -> dict:
    return {
        "task_id": task.task_id,
        "category": task.category,
        "resolved": True,
        "outcome": "patch_written",
        "failure_kind": "none",
        "cost_usd": 0.25,
        "duration_s": 1.0,
    }


@pytest.fixture(autouse=True)
def _no_real_runs(monkeypatch):
    """Nothing in this module may load .env or start a real pipeline."""

    async def forbidden(*args, **kwargs):
        raise AssertionError("a unit test tried to run the real pipeline")

    monkeypatch.setattr(bench_run, "load_dotenv", lambda: None)
    monkeypatch.setattr(bench_run, "run_pipeline", forbidden)


async def test_a_crashing_task_is_recorded_and_the_run_continues(tmp_path, capsys):
    async def run_one(task: TaskSpec, stamp: str) -> dict:
        if task.task_id == "b":
            raise RuntimeError("boom")
        return ok_row(task)

    results = tmp_path / "results.json"
    rows = await run_tasks([spec("a"), spec("b"), spec("c")], "s", results, run_one)
    assert [row["task_id"] for row in rows] == ["a", "b", "c"]
    assert rows[1] == {
        "task_id": "b",
        "category": "bug",
        "resolved": False,
        "outcome": "failed",
        "failure_kind": "infra",
        "reason": "unhandled RuntimeError: boom",
        "crashed": True,
    }
    assert json.loads(results.read_text()) == rows
    assert "b: crashed" in capsys.readouterr().out


async def test_results_are_written_after_every_task(tmp_path):
    results = tmp_path / "results.json"
    seen_before_each_task = []

    async def run_one(task: TaskSpec, stamp: str) -> dict:
        done = json.loads(results.read_text()) if results.exists() else []
        seen_before_each_task.append([row["task_id"] for row in done])
        return ok_row(task)

    await run_tasks([spec("a"), spec("b"), spec("c")], "s", results, run_one)
    assert seen_before_each_task == [[], ["a"], ["a", "b"]]


async def test_run_task_scores_the_pipeline_record(monkeypatch):
    record = RunRecord(
        task_id="a", run_id="a-s", outcome="patch_written", failure_kind="none"
    )
    requests = []

    async def fake_pipeline(request):
        requests.append(request)
        return record

    async def fake_is_resolved(task, scored):
        return scored is record

    monkeypatch.setattr(bench_run, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(bench_run, "is_resolved", fake_is_resolved)
    row = await run_task(spec("a"), "s")
    assert (requests[0].task_id, requests[0].run_id) == ("a", "a-s")
    assert row == {
        "task_id": "a",
        "category": "bug",
        "resolved": True,
        **record.model_dump(),
    }


async def test_a_scoring_crash_is_recorded_too(tmp_path, monkeypatch):
    async def fake_pipeline(request):
        return RunRecord(
            task_id="a", run_id="a-s", outcome="patch_written", failure_kind="none"
        )

    async def broken_scoring(task, record):
        raise OSError("docker is gone")

    monkeypatch.setattr(bench_run, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(bench_run, "is_resolved", broken_scoring)
    rows = await run_tasks([spec("a")], "s", tmp_path / "results.json")
    assert rows[0]["crashed"] and not rows[0]["resolved"]
    assert rows[0]["reason"] == "unhandled OSError: docker is gone"


def test_unknown_task_id_exits_2_and_runs_nothing(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["--tasks", "tc-001,nope-999", "--out", str(out)]) == 2
    assert "nope-999" in capsys.readouterr().err
    assert not out.exists()


def test_main_writes_results_and_survives_a_crash(tmp_path, monkeypatch, capsys):
    async def fake_run_task(task: TaskSpec, stamp: str) -> dict:
        if task.task_id == "tc-002":
            raise RuntimeError("boom")
        return ok_row(task)

    monkeypatch.setattr(bench_run, "run_task", fake_run_task)
    out = tmp_path / "out"
    assert main(["--tasks", "tc-001, tc-002,tc-005", "--out", str(out)]) == 0
    (results,) = out.glob("*.json")
    rows = json.loads(results.read_text())
    assert [(row["task_id"], row["resolved"]) for row in rows] == [
        ("tc-001", True),
        ("tc-002", False),
        ("tc-005", True),
    ]
    summary = capsys.readouterr().out
    assert "resolved 2/3" in summary and "1 crashed" in summary and "$0.50" in summary
