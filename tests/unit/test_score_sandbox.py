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


def use_env(monkeypatch, env):
    async def fake_start():
        return env

    monkeypatch.setattr(score, "start_environment", fake_start)


async def test_scoring_steps_run_in_order_with_hidden_tests_outside_repo(
    patch_file, monkeypatch
):
    env = FakeEnvironment()
    use_env(monkeypatch, env)
    assert await score_patch(load_task("t-1"), patch_file)
    assert env.commands == [APPLY_CMD, VISIBLE_CMD, HIDDEN_CMD]
    assert f"{HIDDEN_DIR}/test_hidden_mini.py" in env.files
    assert not HIDDEN_DIR.startswith(WORKDIR)
    assert not any("hidden" in p for p in env.files if p.startswith(f"{WORKDIR}/"))
    assert env.closed


async def test_hidden_tests_are_uploaded_only_after_the_visible_run_passes(
    patch_file, monkeypatch
):
    class Recording(FakeEnvironment):
        async def exec(self, command, **kwargs):
            self.commands.append(
                f"{command} [hidden on disk: {any(HIDDEN_DIR in p for p in self.files)}]"
            )
            return ExecResult(exit_code=0, stdout="", stderr="")

    env = Recording()
    use_env(monkeypatch, env)
    assert await score_patch(load_task("t-1"), patch_file)
    assert env.commands[1].endswith("[hidden on disk: False]")
    assert env.commands[2].endswith("[hidden on disk: True]")


async def test_a_failing_visible_run_never_uploads_hidden_tests(
    patch_file, monkeypatch
):
    env = FakeEnvironment(
        responses={VISIBLE_CMD: ExecResult(exit_code=1, stdout="", stderr="")}
    )
    use_env(monkeypatch, env)
    assert not await score_patch(load_task("t-1"), patch_file)
    assert not any(HIDDEN_DIR in p for p in env.files)
    assert HIDDEN_CMD not in env.commands


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
