import pytest

from tests.fakes import make_bench_task


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A tiny bench repo and task t-1 under tmp_path, with the bench env vars set."""
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    return tmp_path
