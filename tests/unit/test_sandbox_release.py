"""SandboxReleasePlugin: releases the sandbox a run started under agents-cli, where
the driver's `finally` does not run, and never one that belongs to another run."""

import logging
from types import SimpleNamespace

import pytest

from app.environment import registry
from app.sandbox_release import SandboxReleasePlugin


class FakeEnv:
    def __init__(self, env_id: str, fail: bool = False) -> None:
        self.env_id = env_id
        self.closed = 0
        self._fail = fail

    async def close(self) -> None:
        self.closed += 1
        if self._fail:
            raise RuntimeError("docker is gone")


def ctx(**state):
    return SimpleNamespace(session=SimpleNamespace(id="s1", state=dict(state)))


def test_plugin_name():
    assert SandboxReleasePlugin().name == "sandbox_release"


async def test_releases_after_a_run():
    mine, other = FakeEnv("env-mine"), FakeEnv("env-other")
    registry.register(mine)
    registry.register(other)
    plugin = SandboxReleasePlugin()
    context = ctx()
    await plugin.before_run_callback(invocation_context=context)
    context.session.state["sandbox_id"] = "env-mine"  # set by provision_sandbox
    await plugin.after_run_callback(invocation_context=context)
    assert mine.closed == 1
    assert other.closed == 0  # another run's sandbox is untouched
    with pytest.raises(Exception, match="released"):
        registry.get("env-mine")
    assert registry.get("env-other") is other


async def test_releases_after_an_error():
    mine = FakeEnv("env-mine")
    registry.register(mine)
    plugin = SandboxReleasePlugin()
    context = ctx()
    await plugin.before_run_callback(invocation_context=context)
    context.session.state["sandbox_id"] = "env-mine"
    await plugin.on_run_error_callback(
        invocation_context=context, error=ValueError("x")
    )
    assert mine.closed == 1


async def test_a_run_without_a_sandbox_releases_nothing():
    other = FakeEnv("env-other")
    registry.register(other)
    plugin = SandboxReleasePlugin()
    context = ctx()
    await plugin.before_run_callback(invocation_context=context)
    await plugin.after_run_callback(invocation_context=context)
    await plugin.on_run_error_callback(
        invocation_context=context, error=ValueError("x")
    )
    assert other.closed == 0


async def test_a_sandbox_the_session_already_held_is_not_released():
    """The id was in the state before this run began (a seeded or replayed session):
    this run did not start it, so it is not this run's to release."""
    theirs = FakeEnv("env-theirs")
    registry.register(theirs)
    plugin = SandboxReleasePlugin()
    context = ctx(sandbox_id="env-theirs")
    await plugin.before_run_callback(invocation_context=context)
    await plugin.after_run_callback(invocation_context=context)
    assert theirs.closed == 0
    assert registry.get("env-theirs") is theirs


async def test_error_then_after_run_releases_once():
    mine = FakeEnv("env-mine")
    registry.register(mine)
    plugin = SandboxReleasePlugin()
    context = ctx()
    await plugin.before_run_callback(invocation_context=context)
    context.session.state["sandbox_id"] = "env-mine"
    await plugin.on_run_error_callback(
        invocation_context=context, error=ValueError("x")
    )
    await plugin.after_run_callback(invocation_context=context)
    assert mine.closed == 1


async def test_release_failure_is_logged_not_raised(caplog):
    mine = FakeEnv("env-mine", fail=True)
    registry.register(mine)
    plugin = SandboxReleasePlugin()
    context = ctx()
    await plugin.before_run_callback(invocation_context=context)
    context.session.state["sandbox_id"] = "env-mine"
    with caplog.at_level(logging.WARNING, logger="app.sandbox_release"):
        await plugin.after_run_callback(invocation_context=context)
        await plugin.on_run_error_callback(
            invocation_context=context, error=ValueError("x")
        )
    assert "could not release sandbox env-mine" in caplog.text
    assert "docker is gone" in caplog.text


async def test_releases_the_sandbox_of_a_real_runner_run(bench, monkeypatch):
    """Under a plain runner (no driver `finally`), the plugin sees the sandbox id that
    `provision_sandbox` put in session state and releases it, on success and on a
    run that fails after provisioning."""
    from google.adk.apps import App
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from app.models import RoleModels
    from app.nodes.verify import TEST_CMD
    from app.pipeline import build_workflow
    from app.schemas import RunRequest
    from tests.fakes import FakeEnvironment, FakeLlm, json_out, raises
    from tests.unit.test_pipeline import (
        APPROVE,
        PASS,
        PATCH,
        PLAN,
        diff_responses,
        use_env,
    )

    async def run_once(env, planner, coder, reviewer):
        use_env(monkeypatch, env)
        models = RoleModels(planner=planner, coder=coder, reviewer=reviewer)
        app = App(
            name="app",
            root_agent=build_workflow(models),
            plugins=[SandboxReleasePlugin()],
        )
        runner = InMemoryRunner(app=app)
        session = await runner.session_service.create_session(
            app_name="app", user_id="u"
        )
        message = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=RunRequest(task_id="t-1", run_id="r-1").model_dump_json()
                )
            ],
        )
        try:
            async for _ in runner.run_async(
                user_id="u", session_id=session.id, new_message=message
            ):
                pass
        except Exception:
            return False
        return True

    ok_env = FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    assert await run_once(
        ok_env,
        FakeLlm([json_out(PLAN)]),
        FakeLlm([json_out(PATCH)]),
        FakeLlm([json_out(APPROVE)]),
    )
    assert ok_env.closed

    bad_env = FakeEnvironment()
    assert not await run_once(
        bad_env,
        FakeLlm([raises(RuntimeError("model down"))]),
        FakeLlm([]),
        FakeLlm([]),
    )
    assert bad_env.closed
