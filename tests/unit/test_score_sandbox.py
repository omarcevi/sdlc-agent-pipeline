"""Scoring flow against a fake sandbox. Real scoring is in tests/integration."""

import logging

import pytest

from app.environment.base import WORKDIR, ExecResult, InfraError
from app.task_store import load_task
from bench import score
from bench.score import APPLY_CMD, HIDDEN_CMD, HIDDEN_DIR, VISIBLE_CMD, score_patch
from tests.fakes import FakeEnvironment, make_bench_task

TIMED_OUT = ExecResult(exit_code=124, stdout="", stderr="", timed_out=True)


@pytest.fixture
def patch_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    make_bench_task(tmp_path)
    path = tmp_path / "patch.diff"
    path.write_text("diff --git a/mini.py b/mini.py\n")
    return path


def use_env(monkeypatch, *envs):
    """start_environment hands out `envs` in order; a single env is reused."""
    queue = list(envs)
    started = []

    async def fake_start():
        env = queue.pop(0) if len(queue) > 1 else queue[0]
        started.append(env)
        return env

    monkeypatch.setattr(score, "start_environment", fake_start)
    return started


async def test_visible_and_hidden_phases_use_two_fresh_sandboxes(
    patch_file, monkeypatch
):
    first, second = FakeEnvironment(), FakeEnvironment()
    started = use_env(monkeypatch, first, second)
    assert await score_patch(load_task("t-1"), patch_file)
    assert started == [first, second]
    assert first.commands == [APPLY_CMD, VISIBLE_CMD]
    assert second.commands == [APPLY_CMD, HIDDEN_CMD]
    assert not any(HIDDEN_DIR in p for p in first.files)
    assert f"{HIDDEN_DIR}/test_hidden_mini.py" in second.files
    assert not HIDDEN_DIR.startswith(WORKDIR)
    assert not any("hidden" in p for p in second.files if p.startswith(f"{WORKDIR}/"))
    assert first.closed and second.closed


async def test_the_hidden_sandbox_runs_nothing_but_apply_before_the_hidden_tests(
    patch_file, monkeypatch
):
    class Recording(FakeEnvironment):
        async def exec(self, command, **kwargs):
            on_disk = any(HIDDEN_DIR in p for p in self.files)
            self.commands.append(f"{command} [hidden on disk: {on_disk}]")
            return ExecResult(exit_code=0, stdout="", stderr="")

    first, second = Recording(), Recording()
    use_env(monkeypatch, first, second)
    assert await score_patch(load_task("t-1"), patch_file)
    assert second.commands == [
        f"{APPLY_CMD} [hidden on disk: False]",
        f"{HIDDEN_CMD} [hidden on disk: True]",
    ]


async def test_the_visible_sandbox_is_closed_before_the_hidden_run(
    patch_file, monkeypatch
):
    events = []

    class Ordered(FakeEnvironment):
        def __init__(self, name):
            super().__init__()
            self.name = name

        async def exec(self, command, **kwargs):
            events.append(f"{self.name}:{command}")
            return await super().exec(command, **kwargs)

        async def close(self):
            events.append(f"{self.name}:close")
            await super().close()

    use_env(monkeypatch, Ordered("A"), Ordered("B"))
    assert await score_patch(load_task("t-1"), patch_file)
    assert events.index("A:close") < events.index(f"B:{HIDDEN_CMD}")


async def test_a_failing_visible_run_starts_no_second_sandbox(patch_file, monkeypatch):
    first = FakeEnvironment(
        responses={VISIBLE_CMD: ExecResult(exit_code=1, stdout="", stderr="")}
    )
    started = use_env(monkeypatch, first, FakeEnvironment())
    assert not await score_patch(load_task("t-1"), patch_file)
    assert started == [first]
    assert not any(HIDDEN_DIR in p for p in first.files)
    assert HIDDEN_CMD not in first.commands
    assert first.closed


@pytest.mark.parametrize("command", [VISIBLE_CMD, HIDDEN_CMD])
async def test_test_timeout_is_not_resolved(command, patch_file, monkeypatch):
    env = FakeEnvironment(responses={command: TIMED_OUT})
    use_env(monkeypatch, env)
    assert not await score_patch(load_task("t-1"), patch_file)
    assert env.closed


async def test_sandbox_is_closed_when_scoring_raises(patch_file, monkeypatch):
    class BrokenEnvironment(FakeEnvironment):
        async def exec(self, command, **kwargs):
            raise InfraError("sandbox gone")

    env = BrokenEnvironment()
    use_env(monkeypatch, env)
    with pytest.raises(InfraError):
        await score_patch(load_task("t-1"), patch_file)
    assert env.closed


async def test_failing_sandbox_release_does_not_change_the_score(
    patch_file, monkeypatch, caplog
):
    class UnclosableEnvironment(FakeEnvironment):
        async def close(self) -> None:
            raise InfraError("docker command timed out: docker rm -f")

    env = UnclosableEnvironment()
    use_env(monkeypatch, env)
    with caplog.at_level(logging.WARNING, logger="bench.score"):
        assert await score_patch(load_task("t-1"), patch_file)
    assert env.env_id in caplog.text and "docker rm" in caplog.text
