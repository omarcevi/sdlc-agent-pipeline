import os
import shutil
import subprocess

import pytest

IMAGE = os.environ.get("SANDBOX_IMAGE", "issue-to-pr-sandbox:dev")

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]


def _run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "run", "--rm", "--network", "none", IMAGE, *cmd],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_image_exists():
    result = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    assert result.returncode == 0, f"build it first: make sandbox-image ({IMAGE})"


def test_toolchain_present():
    result = _run(
        "sh",
        "-c",
        "python --version && pytest --version && git --version && rg --version",
    )
    assert result.returncode == 0, result.stderr
    assert "Python 3.12" in result.stdout


def test_runs_as_non_root_sandbox_user():
    assert _run("id", "-u").stdout.strip() == "1000"


def test_network_is_unreachable():
    code = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"
    assert _run("python", "-c", code).returncode != 0
