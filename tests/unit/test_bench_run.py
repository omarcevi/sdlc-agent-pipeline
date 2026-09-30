import json

import pytest
from google.adk.events import Event
from google.genai import types

from bench import matrix
from bench import run as bench_run
from bench.run import main
from tests.fakes import make_bench_task


def ok_row(spec: matrix.RunSpec) -> dict:
    return {
        "resolved": True,
        "outcome": "patch_written",
        "failure_kind": "none",
        "cost_usd": 0.25,
        "duration_s": 1.0,
    }


@pytest.fixture(autouse=True)
def _no_real_runs(monkeypatch):
    """Nothing in this module may load .env, run a validation or start a pipeline."""

    async def forbidden(*args, **kwargs):
        raise AssertionError("a unit test tried to run the real pipeline")

    monkeypatch.setattr(bench_run, "load_dotenv", lambda: None)
    monkeypatch.setattr(bench_run, "validate_task", lambda task: [])
    monkeypatch.setattr(matrix, "run_pipeline", forbidden)


def _use_run_one(monkeypatch, run_one, calls=None):
    real = bench_run.run_matrix

    async def fake_run_matrix(specs, path, **kwargs):
        if calls is not None:
            calls.append((specs, path, kwargs))
        return await real(specs, path, **{**kwargs, "run_one": run_one})

    monkeypatch.setattr(bench_run, "run_matrix", fake_run_matrix)


def _tool_event() -> Event:
    part = types.Part(
        function_call=types.FunctionCall(name="read_file", args={"path": "a.py"})
    )
    return Event(author="coder", content=types.Content(role="model", parts=[part]))


async def _fake_one(spec, on_event=None) -> dict:
    if on_event:
        on_event(_tool_event())
    return ok_row(spec)


def test_unknown_task_id_exits_2_and_runs_nothing(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["--tasks", "tc-001,nope-999", "--out", str(out)]) == 2
    assert "nope-999" in capsys.readouterr().err
    assert not out.exists()


def test_an_invalid_task_exits_2_before_anything_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        bench_run,
        "validate_task",
        lambda task: ["hidden tests already pass"] if task.task_id == "tc-002" else [],
    )
    calls: list = []
    _use_run_one(monkeypatch, _fake_one, calls)
    out = tmp_path / "out"
    assert main(["--tasks", "tc-001,tc-002", "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "tc-002" in err and "hidden tests already pass" in err
    assert calls == [] and not out.exists()


def test_skip_validate_runs_invalid_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(bench_run, "validate_task", lambda task: ["broken"])
    _use_run_one(monkeypatch, _fake_one)
    assert main(["--tasks", "tc-001", "--skip-validate", "--out", str(tmp_path)]) == 0


def _make_heldout(monkeypatch):
    real = bench_run.load_task

    def load(task_id):
        return real(task_id).model_copy(update={"split": "heldout"})

    monkeypatch.setattr(bench_run, "load_task", load)
    monkeypatch.setattr(bench_run, "list_tasks", lambda: [load("tc-001")])


def test_heldout_needs_confirmation(tmp_path, monkeypatch, capsys):
    _make_heldout(monkeypatch)
    calls: list = []
    _use_run_one(monkeypatch, _fake_one, calls)
    for argv in (["--tasks", "tc-001"], ["--split", "heldout"]):
        assert main([*argv, "--out", str(tmp_path / "out")]) == 2
        assert "tc-001" in capsys.readouterr().err
    assert calls == []
    argv = ["--split", "heldout", "--confirm-heldout", "--out", str(tmp_path)]
    assert main(argv) == 0


def test_single_system_with_the_mixed_preset_exits_2(tmp_path, capsys):
    argv = ["--system", "single", "--preset", "mixed", "--out", str(tmp_path / "o")]
    assert main(["--tasks", "tc-001", *argv]) == 2
    assert "mixed" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_flags_reach_the_matrix_and_name_the_results_file(
    tmp_path, monkeypatch, capsys
):
    calls: list = []
    _use_run_one(monkeypatch, _fake_one, calls)
    argv = [
        "--tasks", "tc-001, tc-002", "--system", "single", "--preset", "pro",
        "--repeats", "2", "--concurrency", "3", "--out", str(tmp_path / "out"),
    ]  # fmt: skip
    assert main(argv) == 0
    ((specs, path, kwargs),) = calls
    assert [(s.task.task_id, s.system, s.preset, s.repeat) for s in specs] == [
        ("tc-001", "single", "pro", 1),
        ("tc-002", "single", "pro", 1),
        ("tc-001", "single", "pro", 2),
        ("tc-002", "single", "pro", 2),
    ]
    assert kwargs["concurrency"] == 3 and kwargs["progress"] is True
    assert path.parent == tmp_path / "out"
    assert path.name.endswith("-single-pro.json")
    assert len(json.loads(path.read_text())) == 4
    assert "resolved 4/4" in capsys.readouterr().out


def test_defaults_are_dev_multi_flash_one_repeat(tmp_path, monkeypatch):
    calls: list = []
    _use_run_one(monkeypatch, _fake_one, calls)
    assert main(["--out", str(tmp_path)]) == 0
    ((specs, path, kwargs),) = calls
    assert {(s.system, s.preset, s.repeat) for s in specs} == {("multi", "flash", 1)}
    assert all(s.task.split == "dev" for s in specs)
    assert kwargs["concurrency"] == 1
    assert path.name.endswith("-multi-flash.json")


def test_main_summary_counts_crashes(tmp_path, monkeypatch, capsys):
    async def run_one(spec, on_event=None) -> dict:
        if spec.task.task_id == "tc-002":
            raise RuntimeError("boom")
        return ok_row(spec)

    _use_run_one(monkeypatch, run_one)
    assert main(["--tasks", "tc-001,tc-002,tc-005", "--out", str(tmp_path)]) == 0
    summary = capsys.readouterr().out
    assert "resolved 2/3" in summary and "1 crashed" in summary and "$0.50" in summary


def test_progress_lines_are_printed_by_default(tmp_path, monkeypatch, capsys):
    _use_run_one(monkeypatch, _fake_one)
    assert main(["--tasks", "tc-001", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "tc-001/multi/flash/r1     coder → read_file a.py" in out
    assert "tc-001/multi/flash/r1: patch_written" in out


def test_quiet_prints_no_progress_lines(tmp_path, monkeypatch, capsys):
    _use_run_one(monkeypatch, _fake_one)
    assert main(["--tasks", "tc-001", "--quiet", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "read_file" not in out
    assert "tc-001/multi/flash/r1: patch_written" in out


def test_help_lists_every_flag(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    for flag in (
        "--tasks", "--split", "--system", "--preset", "--repeats",
        "--concurrency", "--out", "--quiet", "--confirm-heldout", "--skip-validate",
    ):  # fmt: skip
        assert flag in text


def test_dotenv_is_loaded_before_tasks_are_selected(tmp_path, monkeypatch):
    make_bench_task(tmp_path, task_id="env-only-1")

    def fake_load_dotenv():
        monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
        monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))

    monkeypatch.setattr(bench_run, "load_dotenv", fake_load_dotenv)
    _use_run_one(monkeypatch, _fake_one)
    out = tmp_path / "out"
    assert main(["--tasks", "env-only-1", "--out", str(out)]) == 0
    (results,) = out.glob("*.json")
    assert [r["task_id"] for r in json.loads(results.read_text())] == ["env-only-1"]


@pytest.mark.parametrize("flag", ["--repeats", "--concurrency"])
def test_zero_repeats_or_concurrency_exits_2(tmp_path, capsys, flag):
    assert main(["--tasks", "tc-001", flag, "0", "--out", str(tmp_path / "o")]) == 2
    assert "at least 1" in capsys.readouterr().err
