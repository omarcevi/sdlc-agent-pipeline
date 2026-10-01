import pytest

from tests.fakes import make_bench_task
from tests.unit._constants import DIFF, PROBE_NOTE


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A tiny bench repo and task t-1 under tmp_path, with the bench env vars set."""
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    return tmp_path


@pytest.fixture
def probe_store(bench, monkeypatch):
    """The bench fixture plus one probe, rp-01, on task t-1."""
    monkeypatch.setenv("REVIEW_PROBES_DIR", str(bench / "probes"))
    directory = bench / "probes" / "rp-01"
    directory.mkdir(parents=True)
    (directory / "probe.yaml").write_text(
        "task_id: t-1\nkind: bad\nsource: shortcut\nsource_run: null\n"
        f"note: {PROBE_NOTE}\n"
    )
    (directory / "patch.diff").write_text(DIFF)
    return bench
