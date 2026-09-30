"""Every demo repository's visible tests pass in a fresh sandbox.

The repositories are discovered under `bench/repos/`, so a new one is covered without
editing this file. Each is copied in through the `Environment` interface, the way the
scorer copies a task's repository, and its tests are run twice to catch flaky ones.
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from app.environment.base import WORKDIR
from app.environment.docker import DockerEnvironment

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed"),
]

REPOS_DIR = Path(__file__).resolve().parents[2] / "bench" / "repos"
REPO_NAMES = sorted(path.name for path in REPOS_DIR.iterdir() if path.is_dir())
VISIBLE_CMD = "python -m pytest -q tests"
TEST_TIMEOUT_S = 120.0


def test_repos_were_discovered():
    assert REPO_NAMES, f"no repositories under {REPOS_DIR}"


@pytest.mark.parametrize("name", REPO_NAMES)
async def test_repo_visible_tests_pass_in_the_sandbox(name):
    env = await DockerEnvironment.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "repo"
            shutil.copytree(
                REPOS_DIR / name,
                clean,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            )
            await env.upload_dir(clean, WORKDIR)
        for attempt in (1, 2):
            result = await env.exec(VISIBLE_CMD, timeout=TEST_TIMEOUT_S)
            assert result.exit_code == 0, (
                f"run {attempt} of {name}: exit {result.exit_code}\n"
                f"{result.stdout}\n{result.stderr}"
            )
    finally:
        await env.close()
