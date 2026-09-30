import pytest

from app.task_store import list_tasks, load_task, materialize, test_files
from tests.fakes import make_bench_task


@pytest.fixture
def bench_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    make_bench_task(tmp_path)
    return tmp_path


def test_load_and_list(bench_root):
    task = load_task("t-1")
    assert (task.repo, task.category, task.split) == ("mini", "bug", "dev")
    assert [t.task_id for t in list_tasks()] == ["t-1"]


def test_materialize_layers(bench_root, tmp_path):
    task = load_task("t-1")
    planted = materialize(task, tmp_path / "a")
    assert "a - b" in (planted / "mini.py").read_text()
    assert not (planted / "hidden_tests").exists()
    solved = materialize(
        task, tmp_path / "b", with_solution=True, with_hidden_tests=True
    )
    assert "a + b" in (solved / "mini.py").read_text()
    assert (solved / "hidden_tests" / "test_hidden_mini.py").exists()


def test_test_files(bench_root):
    assert test_files(bench_root / "repos" / "mini") == ["tests/test_mini.py"]
