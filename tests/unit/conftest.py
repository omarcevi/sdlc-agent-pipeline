import httpx
import pytest

from app.environment import docker
from app.github_client import GitHubClient
from tests.fakes import make_bench_task
from tests.unit._constants import DIFF, PROBE_NOTE
from tests.unit.delivery_fakes import FakeGitHubRest


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


@pytest.fixture
def github(monkeypatch):
    server = FakeGitHubRest()

    async def no_sleep(_seconds: float) -> None:
        return None

    def factory(**kwargs):
        return GitHubClient(
            "tok",
            transport=httpx.MockTransport(server),
            sleep=no_sleep,
            max_attempts=2,
        )

    monkeypatch.setattr(GitHubClient, "from_token_file", staticmethod(factory))
    return server


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


@pytest.fixture(autouse=True)
def no_real_sandbox(request, monkeypatch):
    """A test not marked `docker` must never run a real docker command: it would
    pass on a machine that has the image and fail on one that has not."""
    if request.node.get_closest_marker("docker"):
        return

    async def refuse(*args, **kwargs):
        raise AssertionError(
            "a test not marked `docker` tried to run a real docker command; "
            "use a fake environment (use_env(monkeypatch, FakeEnvironment()))"
        )

    monkeypatch.setattr(docker, "_run", refuse)
