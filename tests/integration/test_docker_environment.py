"""Checks that hold for the local Docker backend only. The behaviour both backends
share is in tests/integration/test_environment_contract.py. The cloud counterpart of
"no credentials from the host" is the identity test in
tests/integration/test_agent_runtime_sandbox.py: it is about what `docker run`
could leak from the host, which a cloud sandbox has no path for."""

import asyncio
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from app.environment.base import InfraError
from app.environment.docker import DockerEnvironment
from tests.integration.sandbox_helpers import credential_like_variables

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]


@pytest.fixture
async def env():
    environment = await DockerEnvironment.start()
    yield environment
    await environment.close()


async def test_root_filesystem_is_read_only(env):
    assert (await env.exec("touch /opt/runtime/x", cwd="/workspace")).exit_code != 0


def _container_exists(name: str) -> bool:
    listed = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", f"name=^{name}$"],
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(listed.stdout.strip())


async def test_sandbox_self_destructs_after_its_ttl(monkeypatch):
    monkeypatch.setenv("SANDBOX_TTL_S", "2")
    environment = await DockerEnvironment.start()
    try:
        assert _container_exists(environment.env_id)
        deadline = time.monotonic() + 30
        while _container_exists(environment.env_id) and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        assert not _container_exists(environment.env_id)
        with pytest.raises(InfraError):
            await environment.exec("true")
    finally:
        await environment.close()


async def test_sandbox_has_no_credentials_from_the_host(monkeypatch):
    fakes = {
        "GITHUB_TOKEN": "fake-token-for-test",
        "GOOGLE_API_KEY": "fake-google-api-key-for-test",
        "GOOGLE_APPLICATION_CREDENTIALS": "/fake/credentials-for-test.json",
    }
    for name, value in fakes.items():
        monkeypatch.setenv(name, value)
    environment = await DockerEnvironment.start()
    try:
        result = await environment.exec("env")
    finally:
        await environment.close()
    assert result.exit_code == 0
    assert credential_like_variables(result.stdout) == []
    for value in fakes.values():
        assert value not in result.stdout


async def test_sandbox_cannot_see_the_token_file(monkeypatch):
    # Under the host home: Docker Desktop shares it with its VM, so this is the
    # place a careless mount would expose.
    token = f"ghp_CANARY_{uuid.uuid4().hex}"
    path = Path.home() / f".issue-to-pr-canary-{uuid.uuid4().hex}"
    path.write_text(token + "\n")
    path.chmod(0o600)
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(path))
    try:
        environment = await DockerEnvironment.start()
        try:
            variables = await environment.exec("env")
            read = await environment.exec(f"cat {shlex.quote(str(path))}")
        finally:
            await environment.close()
    finally:
        path.unlink(missing_ok=True)
    assert variables.exit_code == 0
    assert "GITHUB_TOKEN_FILE" not in variables.stdout
    assert str(path) not in variables.stdout and token not in variables.stdout
    assert read.exit_code != 0
    assert token not in read.stdout + read.stderr
