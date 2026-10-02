"""The behaviour every sandbox backend must have (spec 3A §7.2).

The `env` fixture runs each test on local Docker (marked `docker`) and on an Agent
Runtime sandbox (marked `cloud`, skipped unless ITP_CLOUD_TESTS=1). Checks that are
true of one backend only live elsewhere: tests/integration/test_docker_environment.py
and tests/integration/test_agent_runtime_sandbox.py.
"""

import asyncio
import shutil
import time

import pytest
import pytest_asyncio

from app.environment.agent_runtime import AgentRuntimeEnvironment, SandboxSettings
from app.environment.base import WORKDIR, InfraError
from app.environment.docker import DockerEnvironment
from app.tools.files import list_dir
from tests.fakes import fake_tool_context

_NO_DOCKER = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker not installed"
)


# The sandbox's HTTP client binds its connections to the event loop that first
# uses them. pyproject sets async fixtures to the session loop, but each test runs
# in its own loop, so this fixture runs in the test's loop.
@pytest_asyncio.fixture(
    loop_scope="function",
    params=[
        pytest.param("docker", marks=[pytest.mark.docker, _NO_DOCKER]),
        pytest.param("agent_runtime", marks=pytest.mark.cloud),
    ],
)
async def env(request, cloud_settings):
    if request.param == "docker":
        environment = await DockerEnvironment.start()
    else:
        environment = await AgentRuntimeEnvironment.start(
            settings=SandboxSettings.from_env(cloud_settings)
        )
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


async def test_children_of_a_timed_out_command_are_stopped(env):
    # The command starts a background child and waits for it. The timeout must stop
    # the child too, not just the shell that started it.
    result = await env.exec(
        "sleep 300 >/dev/null 2>&1 & echo $! > child.pid; wait", timeout=2
    )
    assert result.timed_out
    pid = int((await env.read_file(f"{WORKDIR}/child.pid")).strip())
    # Gone, or a zombie nobody has reaped yet (the sandbox's init may not reap).
    gone = f's=$(cut -d\' \' -f3 /proc/{pid}/stat 2>/dev/null); [ -z "$s" ] || [ "$s" = Z ]'
    deadline = time.monotonic() + 10
    while True:
        if (await env.exec(gone)).exit_code == 0:
            break
        assert time.monotonic() < deadline, "the child of a timed-out command is alive"
        await asyncio.sleep(0.5)


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
