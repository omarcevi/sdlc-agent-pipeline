import asyncio
import re
import shutil
import subprocess
import time

import pytest

from app.environment.base import WORKDIR, InfraError
from app.environment.docker import DockerEnvironment
from app.tools.files import list_dir
from tests.fakes import fake_tool_context

CREDENTIAL_NAME = re.compile(r"TOKEN|SECRET|KEY|CREDENTIAL|PASSWORD|GOOGLE_")
# Set by the python base image: the public fingerprint of the key that signs CPython
# releases. It is not a credential; any other value under this name is a leak.
IMAGE_PUBLIC_VALUES = {"GPG_KEY": "7169605F62C751356D054A26A821E680E5FA6305"}

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]


@pytest.fixture
async def env():
    environment = await DockerEnvironment.start()
    yield environment
    await environment.close()


async def test_upload_and_run_pytest(env, tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    await env.upload_dir(tmp_path)
    result = await env.exec("python -m pytest -q")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


async def test_read_write_roundtrip(env):
    await env.write_file(f"{WORKDIR}/pkg/new.py", "x = 'ü'\n")
    assert await env.read_file(f"{WORKDIR}/pkg/new.py") == "x = 'ü'\n"


async def test_missing_file_raises_file_not_found(env):
    with pytest.raises(FileNotFoundError):
        await env.read_file(f"{WORKDIR}/nope.py")


async def test_timeout_is_reported(env):
    result = await env.exec("sleep 5", timeout=1)
    assert result.timed_out


async def test_no_network(env):
    code = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"
    assert (await env.exec(f'python -c "{code}"')).exit_code != 0


async def test_root_filesystem_is_read_only(env):
    assert (await env.exec("touch /opt/runtime/x", cwd="/workspace")).exit_code != 0


async def test_read_file_on_a_directory_is_an_os_error_not_infra(env):
    await env.exec("mkdir -p pkg")
    with pytest.raises(OSError) as caught:
        await env.read_file(f"{WORKDIR}/pkg")
    assert not isinstance(caught.value, FileNotFoundError)
    assert "directory" in str(caught.value).lower()


async def test_write_file_failures_are_os_errors_not_infra(env):
    await env.exec("mkdir -p pkg && touch plain.txt")
    with pytest.raises(OSError, match=r"(?i)directory"):
        await env.write_file(f"{WORKDIR}/pkg", "x")
    with pytest.raises(OSError):
        await env.write_file(f"{WORKDIR}/plain.txt/child.py", "x")


async def test_stderr_mentioning_not_running_returns_normally(env):
    result = await env.exec("echo 'the service is not running' >&2; exit 3")
    assert result.exit_code == 3 and not result.timed_out
    assert "is not running" in result.stderr
    result = await env.exec("echo 'cat: No such container' >&2; exit 1")
    assert result.exit_code == 1


async def test_sigterm_ignoring_command_is_reported_timed_out(env):
    result = await env.exec("trap '' TERM; sleep 30", timeout=1)
    assert result.timed_out and result.exit_code == 137


async def test_sub_second_timeout_is_still_enforced(env):
    started = time.monotonic()
    result = await env.exec("sleep 6", timeout=0.2)
    assert result.timed_out
    assert time.monotonic() - started < 5


async def test_closed_sandbox_raises_infra_error(env):
    await env.close()
    with pytest.raises(InfraError):
        await env.exec("true")
    with pytest.raises(InfraError):
        await env.read_file(f"{WORKDIR}/a.py")
    with pytest.raises(InfraError):
        await env.write_file(f"{WORKDIR}/a.py", "x")


async def test_list_dir_tool_against_a_real_sandbox(env):
    await env.exec("git init -q && mkdir -p pkg && touch pkg/b.py a.py")
    ctx = fake_tool_context(env)
    assert await list_dir(".", ctx) == {"entries": ["a.py", "pkg/b.py"]}
    assert await list_dir("pkg", ctx) == {"entries": ["pkg/b.py"]}
    assert "error" in await list_dir("missing", ctx)
    assert "error" in await list_dir("a.py", ctx)


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
    variables = dict(line.split("=", 1) for line in result.stdout.splitlines())
    suspicious = {
        name: value
        for name, value in variables.items()
        if CREDENTIAL_NAME.search(name) and IMAGE_PUBLIC_VALUES.get(name) != value
    }
    assert suspicious == {}
    for value in fakes.values():
        assert value not in result.stdout
