import shutil

import pytest

from app.environment.base import WORKDIR
from app.environment.docker import DockerEnvironment

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
