"""Checks against real Agent Runtime sandboxes (spec 3A §7.3).

Every test here is marked `cloud` or `cloud_slow` and is skipped unless
ITP_CLOUD_TESTS=1 (`make test-cloud`). They read their settings from the
`cloud_settings` fixture. No test prints the token or a resource name: only booleans
and variable names reach the output. Each test deletes what it created in a `finally`.
"""

import asyncio
import dataclasses
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from app.environment.agent_runtime import AgentRuntimeEnvironment, SandboxSettings
from app.environment.base import WORKDIR, InfraError
from tests.integration.sandbox_helpers import (
    credential_like_variables,
    delete_template_with_retry,
    is_not_found,
    second_port_failure,
)

ROOT = Path(__file__).resolve().parents[2]
ECHO_SERVER = Path(__file__).with_name("header_echo_server.py")
METADATA_URLS = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
)
# Prints one JSON object: for each URL, whether a response carried an access token.
METADATA_PROBE = f"""
import json, urllib.request

def got_token(url):
    request = urllib.request.Request(url, headers={{"Metadata-Flavor": "Google"}})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return "access_token" in response.read().decode("utf-8", "replace")
    except Exception:
        return False

print(json.dumps([got_token(url) for url in {METADATA_URLS!r}]))
"""
TEMPLATE_READY_TIMEOUT_S = 600
SANDBOX_GONE_TIMEOUT_S = 600


def _infra():
    """scripts/sandbox_infra.py (not a package), loaded the way its unit tests do."""
    if "sandbox_infra" not in sys.modules:
        path = ROOT / "scripts" / "sandbox_infra.py"
        spec = importlib.util.spec_from_file_location("sandbox_infra", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["sandbox_infra"] = module
        spec.loader.exec_module(module)
    return sys.modules["sandbox_infra"]


def _settings(cloud_settings) -> SandboxSettings:
    return SandboxSettings.from_env(cloud_settings)


def _platform(settings: SandboxSettings):
    """Task 4's control-plane client: template create and delete, sandbox get."""
    return _infra().SdkPlatform(settings.project, settings.location)


# The sandbox's HTTP client binds its connections to the event loop that first
# uses them. pyproject sets async fixtures to the session loop, but each test runs
# in its own loop, so this fixture runs in the test's loop.
@pytest_asyncio.fixture(loop_scope="function")
async def started(cloud_settings):
    """Starts sandboxes with the configured settings (or changed ones) and closes
    every one of them at the end, whatever the test did."""
    environments: list[AgentRuntimeEnvironment] = []

    async def start(**changes) -> AgentRuntimeEnvironment:
        settings = dataclasses.replace(_settings(cloud_settings), **changes)
        environments.append(await AgentRuntimeEnvironment.start(settings=settings))
        return environments[-1]

    async def close_all() -> None:
        for environment in environments:
            await environment.close()  # idempotent

    start.close_all = close_all
    try:
        yield start
    finally:
        await close_all()


@pytest.mark.cloud
async def test_metadata_server_gives_no_token(started):
    env = await started()
    await env.write_file(f"{WORKDIR}/metadata_probe.py", METADATA_PROBE)
    result = await env.exec(f"python {WORKDIR}/metadata_probe.py")
    assert result.exit_code == 0 and not result.timed_out, "the probe did not run"
    got = json.loads(result.stdout.strip().splitlines()[-1])
    print(f"metadata server gave a token (by host name, by address): {got}")
    assert got == [False, False]


@pytest.mark.cloud_slow
async def test_authorization_header_does_not_reach_the_container(
    cloud_settings, started
):
    """Starts `header_echo_server.py` through the shim's `POST /processes` (a
    background child of `/exec` would hold the request's pipe open), sends one
    request with the sandbox's headers to port 8081, and reads which headers the
    container saw. The sandbox and the test template are deleted in `finally`."""
    settings = _settings(cloud_settings)
    platform = _platform(settings)
    image = (
        await asyncio.to_thread(platform.get_template, settings.template)
    ).image_uri
    infra = _infra()
    config = infra.template_config(
        image, f"itp-test-{uuid.uuid4().hex[:8]}", ports=(8080, 8081)
    )
    template = await asyncio.to_thread(
        platform.create_template, settings.engine, config
    )
    try:
        await _wait_until_active(platform, template)
        env = await started(template=template)
        await env.write_file(
            f"{WORKDIR}/header_echo_server.py", ECHO_SERVER.read_text()
        )
        spawn = await env.proxy_request(
            "POST",
            "/processes",
            json={"command": f"python {WORKDIR}/header_echo_server.py", "cwd": WORKDIR},
        )
        assert spawn.status_code == 200, f"spawn answered HTTP {spawn.status_code}"
        seen = await _echo_answer(env, spawn.json()["session_id"])
        print(f"headers that reached the container: {seen}")
        assert seen["authorization"] is False
    finally:
        try:
            await started.close_all()
        finally:
            # close() does not wait for the sandbox delete, which holds the template
            await delete_template_with_retry(platform, template)


async def _wait_until_active(platform, template: str) -> None:
    deadline = time.monotonic() + TEMPLATE_READY_TIMEOUT_S
    while True:
        state = (await asyncio.to_thread(platform.get_template, template)).state
        if state.endswith("ACTIVE"):
            return
        assert time.monotonic() < deadline, "the test template did not become ACTIVE"
        await asyncio.sleep(5)


async def _echo_answer(env: AgentRuntimeEnvironment, session: str) -> dict[str, bool]:
    """The echo server's JSON, once it answers; polls while it starts. When it never
    answers, one status check of its process tells a failed spawn from a port the
    platform did not route."""
    deadline = time.monotonic() + 60
    while True:
        try:
            response = await env.proxy_request("GET", "/", port="8081")
        except InfraError:
            response = None
        if response is not None and response.status_code == 200:
            try:
                answer = response.json()
            except ValueError:
                answer = None
            if isinstance(answer, dict) and "authorization" in answer:
                return answer
        if time.monotonic() > deadline:
            pytest.fail(await _second_port_failure(env, session))
        await asyncio.sleep(2)


async def _second_port_failure(env: AgentRuntimeEnvironment, session: str) -> str:
    try:
        status = await env.proxy_request("GET", f"/processes/{session}/status")
    except InfraError:
        return second_port_failure(None, None)
    try:
        body = status.json()
    except ValueError:
        body = None
    return second_port_failure(status.status_code, body)


@pytest.mark.cloud
async def test_identity_and_environment_hold_no_credentials(started):
    env = await started()
    ident = await env.exec("id -u")
    assert ident.exit_code == 0 and ident.stdout.strip() == "1000"
    result = await env.exec("env")
    assert result.exit_code == 0
    # Names only; GPG_KEY, set by the python base image, is public (see the helper).
    assert credential_like_variables(result.stdout) == []


@pytest.mark.cloud
async def test_root_filesystem_writability_is_recorded(started):
    """Records, does not judge: the template may or may not mount `/` read-only."""
    env = await started()
    mounts = await env.exec("grep -E '^[^ ]+ / ' /proc/mounts")
    assert not mounts.timed_out and mounts.exit_code in (0, 1), "the probe did not run"
    options = mounts.stdout.split()[3].split(",") if mounts.stdout.split() else []
    writable = ("rw" in options) if options else "unknown"
    print(f"root filesystem writable: {writable}")


@pytest.mark.cloud_slow
async def test_sandbox_is_gone_after_its_ttl(started, cloud_settings):
    """A sandbox created with ttl 120 s is deleted, or gone, within 10 minutes."""
    platform = _platform(_settings(cloud_settings))
    env = await started(ttl_s=120)
    # Private on purpose: the name holds the project number, so the backend offers no
    # public accessor. If `_handle` is renamed, this test breaks (found in Task 13).
    name = env._handle.name
    terminal = _infra().TERMINAL_STATES
    deadline = time.monotonic() + SANDBOX_GONE_TIMEOUT_S
    while True:
        try:
            state = (await asyncio.to_thread(platform.get_sandbox, name)).state
        except Exception as error:
            if not is_not_found(error):
                raise
            return
        if state in terminal:
            return
        assert time.monotonic() < deadline, f"sandbox still {state} after 10 minutes"
        await asyncio.sleep(10)
