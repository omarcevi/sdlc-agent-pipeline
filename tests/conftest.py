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
    "MODEL_CALL_TIMEOUT_S",
    "RUN_TIMEOUT_S",
    "APPROVAL_TIMEOUT_S",
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
def _no_live_config(monkeypatch):
    """No test sees the developer's live repositories or logins. They may come from
    .env, which a module can load mid-session (app/fast_api_app.py does at import),
    so they are removed for every test; a test that needs them sets its own."""
    monkeypatch.delenv("LIVE_REPOS", raising=False)
    monkeypatch.delenv("LIVE_ALLOWED_USERS", raising=False)


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    registry.clear()
