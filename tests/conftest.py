"""Test-wide setup.

Tests must not depend on the developer's shell or .env, and must never touch GCP.
These variables change how the package behaves (some of them at import time), so
they are removed before any test module imports it. SANDBOX_IMAGE is left alone:
it only selects which local image the Docker tests use.
"""

import os

import pytest

for _variable in (
    "GOOGLE_CLOUD_PROJECT",
    "BQ_ANALYTICS_ENABLED",
    "RUN_BUDGET_USD",
    "MAX_TOOL_CALLS_PER_RUN",
    "PLANNER_MODEL",
    "CODER_MODEL",
    "REVIEWER_MODEL",
    "ENVIRONMENT_BACKEND",
    "BENCH_TASKS_DIR",
    "BENCH_REPOS_DIR",
    "RUNS_DIR",
    "SANDBOX_TTL_S",
    "TRACE_TO_CLOUD",
):
    os.environ.pop(_variable, None)

# Imported only after the environment is clean: app/__init__.py imports app.agent.
from app.environment import registry  # noqa: E402


@pytest.fixture(autouse=True)
def _no_github_token(tmp_path, monkeypatch):
    """No test may find a real token: the file named here never exists."""
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(tmp_path / "no-such-github-token"))


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    registry.clear()
