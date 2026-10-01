"""Every exit path deletes the cloud sandbox (spec §4.7). The bench graph runs on
scripted models while `provision_sandbox` starts an `AgentRuntimeEnvironment` on the
fake control plane and shim; the shim answers commands from a `FakeEnvironment`.
On the laptop the driver's `finally` deletes the sandbox; under agents-cli,
`SandboxReleasePlugin` does. A delete that fails while a run is being set up is
logged and never replaces the run's own error, a cancellation included."""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import pytest
from google.adk.apps import App
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import InMemoryRunner
from google.genai import types

from app import agent as agents_cli_entry
from app.driver import RunCrashed, run_pipeline
from app.environment import registry
from app.environment.agent_runtime import AgentRuntimeEnvironment
from app.environment.base import WORKDIR, InfraError
from app.models import RoleModels
from app.nodes import intake
from app.nodes.verify import TEST_CMD
from app.pipeline import build_workflow
from app.sandbox_release import SandboxReleasePlugin
from app.schemas import IssueTask, RunRequest
from tests.fakes import FakeEnvironment, FakeLlm, json_out, raises
from tests.unit.sandbox_fakes import (
    CLOUD_ENV,
    FakeSandboxControl,
    FakeShim,
    reset_template_checks,
    start_on_fakes,
)
from tests.unit.test_pipeline import (
    APPROVE,
    PASS,
    PATCH,
    PLAN,
    HangingLlm,
    diff_responses,
)


@pytest.fixture(autouse=True)
def _fresh_template_checks():
    reset_template_checks()
    yield
    reset_template_checks()


@pytest.fixture
def cloud_env(bench, monkeypatch):
    """The bench fixture, with ENVIRONMENT_BACKEND=agent_runtime and a valid cloud
    configuration, so the driver's configuration check passes."""
    monkeypatch.setenv("ENVIRONMENT_BACKEND", "agent_runtime")
    for name, value in CLOUD_ENV.items():
        monkeypatch.setenv(name, value)
    return bench


def run_cap(monkeypatch, run_s: str, model_s: str) -> None:
    """RUN_TIMEOUT_S and MODEL_CALL_TIMEOUT_S for a test, with the readiness bound
    below the run's cap, as the configuration check requires."""
    monkeypatch.setenv("RUN_TIMEOUT_S", run_s)
    monkeypatch.setenv("MODEL_CALL_TIMEOUT_S", model_s)
    monkeypatch.setenv("SANDBOX_READY_TIMEOUT_S", model_s)


@dataclass
class CloudFakes:
    """What `provision_sandbox` starts: the control plane, the shim, the sandbox
    behind it, and every environment started."""

    control: FakeSandboxControl
    shim: FakeShim
    sandbox: FakeEnvironment
    started: list[AgentRuntimeEnvironment] = field(default_factory=list)


def use_cloud_sandbox(
    monkeypatch,
    sandbox: FakeEnvironment | None = None,
    control: FakeSandboxControl | None = None,
    shim: FakeShim | None = None,
) -> CloudFakes:
    """Make `provision_sandbox` start an AgentRuntimeEnvironment on the fakes. The
    shim answers each command (unwrapped from the timeout wrapper) from `sandbox`,
    which also answers the baseline commit id."""
    sandbox = sandbox or FakeEnvironment()
    control = control or FakeSandboxControl()
    if shim is None:
        shim = FakeShim(
            exec_responder=lambda command, cwd: sandbox.exec(
                command, cwd=cwd or WORKDIR
            )
        )
    fakes = CloudFakes(control=control, shim=shim, sandbox=sandbox)

    async def start():
        env = await start_on_fakes(control, shim)
        fakes.started.append(env)
        return env

    # intake binds the factory's function by name at import (ruling 5).
    monkeypatch.setattr(intake, "start_environment", start)
    return fakes


def models(planner, coder, reviewer) -> RoleModels:
    return RoleModels(planner=planner, coder=coder, reviewer=reviewer)


async def run(planner, coder, reviewer):
    return await run_pipeline(
        RunRequest(task_id="t-1", run_id="r-1"),
        workflow=build_workflow(models(planner, coder, reviewer)),
    )


async def assert_deleted_once(fakes: CloudFakes) -> None:
    """One sandbox was started and deleted exactly once, then left the registry;
    the environment object is closed."""
    [env] = fakes.started
    [handle] = fakes.control.created
    assert fakes.control.deletes == [handle.name]
    assert fakes.control.calls == ["check_template", "sign_token", "create", "delete"]
    with pytest.raises(InfraError, match="released"):
        registry.get(env.env_id)
    with pytest.raises(InfraError, match="is closed"):
        await env.exec("true")


async def test_driver_deletes_the_cloud_sandbox_after_a_normal_run(
    cloud_env, monkeypatch
):
    fakes = use_cloud_sandbox(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )
    record = await run(
        FakeLlm([json_out(PLAN)]),
        FakeLlm([json_out(PATCH)]),
        FakeLlm([json_out(APPROVE)]),
    )
    assert (record.outcome, record.failure_kind) == ("patch_written", "none")
    await assert_deleted_once(fakes)
    # The run really went through the cloud backend's data plane.
    commands = [command for command, _ in fakes.shim.exec_calls]
    assert TEST_CMD in commands and intake.BASELINE_SHA_CMD in commands
    assert fakes.shim.requests_to("/files/zip")


async def test_driver_deletes_the_cloud_sandbox_when_the_wall_clock_cap_expires(
    cloud_env, monkeypatch
):
    run_cap(monkeypatch, "0.3", "0.1")
    fakes = use_cloud_sandbox(monkeypatch)
    record = await run(HangingLlm([]), FakeLlm([]), FakeLlm([]))
    assert (record.outcome, record.failure_kind) == ("failed", "budget")
    assert record.reason == "run exceeded 0.3 s wall clock"
    await assert_deleted_once(fakes)


async def test_driver_deletes_the_cloud_sandbox_after_a_crash(cloud_env, monkeypatch):
    fakes = use_cloud_sandbox(monkeypatch)
    with pytest.raises(RunCrashed) as crashed:
        # The coder's script is empty: an unclassified AssertionError, a crash.
        await run(FakeLlm([json_out(PLAN)]), FakeLlm([]), FakeLlm([]))
    assert isinstance(crashed.value.__cause__, AssertionError)
    await assert_deleted_once(fakes)


def _fresh(plugin: BasePlugin) -> BasePlugin:
    """A new instance of the plugin's class: plugins keep state per runner, so the
    module's own instances are not shared with a test (ruling 5)."""
    new = cast(Callable[[], BasePlugin], type(plugin))
    return new()


async def _run_under_agents_cli(planner, coder, reviewer) -> bool:
    """One run as agents-cli runs it: a plain runner over an App with app.agent's
    plugins (fresh instances, in order) and the bench graph on scripted models. No
    driver, so no driver `finally`. True when the run ended without an error."""
    plugins = [_fresh(plugin) for plugin in agents_cli_entry.app.plugins]
    assert any(isinstance(plugin, SandboxReleasePlugin) for plugin in plugins)
    app = App(
        name="app",
        root_agent=build_workflow(models(planner, coder, reviewer)),
        plugins=plugins,
    )
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(app_name="app", user_id="u")
    request = RunRequest(task_id="t-1", run_id="r-1").model_dump_json()
    message = types.Content(role="user", parts=[types.Part.from_text(text=request)])
    try:
        async for _ in runner.run_async(
            user_id="u", session_id=session.id, new_message=message
        ):
            pass
    except Exception:
        return False
    return True


async def test_release_plugin_deletes_the_cloud_sandbox_under_agents_cli(
    cloud_env, monkeypatch
):
    fakes = use_cloud_sandbox(
        monkeypatch, FakeEnvironment(responses={**diff_responses(), TEST_CMD: PASS})
    )
    assert await _run_under_agents_cli(
        FakeLlm([json_out(PLAN)]),
        FakeLlm([json_out(PATCH)]),
        FakeLlm([json_out(APPROVE)]),
    )
    await assert_deleted_once(fakes)

    # A run that fails after provisioning: on_run_error_callback deletes it. (The
    # template is checked once per process; forget it, as for a new process.)
    reset_template_checks()
    failing = use_cloud_sandbox(monkeypatch)
    assert not await _run_under_agents_cli(
        FakeLlm([raises(RuntimeError("model down"))]), FakeLlm([]), FakeLlm([])
    )
    await assert_deleted_once(failing)


# --- a delete that fails while the run is being set up ----------------------------

# When the failing delete replaced the cancellation, the run never ended (the cap's
# cancellation was lost); the guard turns that into a failure instead of a hang.
GUARD_S = 10.0


def _baseline_never_finishes(sandbox: FakeEnvironment):
    """A shim whose `git init` baseline never answers, so provisioning stalls after
    the sandbox exists and is registered."""

    async def responder(command: str, cwd: str | None):
        if command == intake.GIT_BASELINE:
            await asyncio.Event().wait()
        return await sandbox.exec(command, cwd=cwd or WORKDIR)

    return FakeShim(exec_responder=responder)


async def baseline_started(fakes: CloudFakes, task: asyncio.Task) -> None:
    """Wait until the shim got the baseline command, i.e. the sandbox exists and is
    registered; fail at once if the task ended first."""
    baseline = intake.GIT_BASELINE
    while not any(command == baseline for command, _ in fakes.shim.exec_calls):
        assert not task.done(), task.exception()
        await asyncio.sleep(0.01)


def _control_whose_delete_fails() -> FakeSandboxControl:
    control = FakeSandboxControl()
    control.fail("delete", InfraError("sandbox delete failed: ServerError 503"))
    return control


def _stalled_provisioning(monkeypatch) -> CloudFakes:
    sandbox = FakeEnvironment()
    return use_cloud_sandbox(
        monkeypatch,
        sandbox,
        control=_control_whose_delete_fails(),
        shim=_baseline_never_finishes(sandbox),
    )


def _released_with_a_warning(fakes: CloudFakes, caplog) -> None:
    [env] = fakes.started
    [handle] = fakes.control.created
    assert fakes.control.deletes == [handle.name]  # tried once, and it failed
    assert f"could not release sandbox {env.env_id}" in caplog.text
    assert "sandbox delete failed: ServerError 503" in caplog.text
    with pytest.raises(InfraError, match="released"):
        registry.get(env.env_id)


async def test_a_failing_delete_never_replaces_a_cancellation_in_provisioning(
    monkeypatch, caplog, bench
):
    """provision_sandbox on its own: cancelled while it sets the sandbox up, with a
    delete that fails. The cancellation is what propagates; the delete is logged."""
    fakes = _stalled_provisioning(monkeypatch)
    issue = IssueTask(task_id="t-1", run_id="r-1", repo="mini", title="t", body="b")

    async def provision():
        async for _ in intake.provision_sandbox(issue):
            pass

    with caplog.at_level(logging.WARNING, logger="app.nodes.intake"):
        task = asyncio.create_task(provision())
        await baseline_started(fakes, task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    _released_with_a_warning(fakes, caplog)


async def test_a_failing_delete_keeps_the_wall_clock_cap_a_budget_failure(
    cloud_env, monkeypatch, caplog
):
    run_cap(monkeypatch, "0.3", "0.1")
    fakes = _stalled_provisioning(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.nodes.intake"):
        record = await asyncio.wait_for(
            run(FakeLlm([]), FakeLlm([]), FakeLlm([])), GUARD_S
        )
    assert (record.outcome, record.failure_kind) == ("failed", "budget")
    assert record.reason == "run exceeded 0.3 s wall clock"
    _released_with_a_warning(fakes, caplog)


async def test_a_failing_delete_keeps_an_outer_cancellation_a_cancellation(
    cloud_env, monkeypatch, caplog
):
    run_cap(monkeypatch, "60", "30")
    fakes = _stalled_provisioning(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.nodes.intake"):
        task = asyncio.create_task(run(FakeLlm([]), FakeLlm([]), FakeLlm([])))
        await baseline_started(fakes, task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, GUARD_S)
    record = json.loads((cloud_env / "runs" / "r-1" / "record.json").read_text())
    assert (record["outcome"], record["reason"]) == ("failed", "run cancelled")
    _released_with_a_warning(fakes, caplog)
