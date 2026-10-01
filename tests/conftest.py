"""Test-wide setup.

Tests must not depend on the developer's shell or .env, and must never touch GCP.
These variables change how the package behaves (some of them at import time), so
they are removed before any test module imports it. SANDBOX_IMAGE is left alone:
it only selects which local image the Docker tests use.

Tests marked `cloud` or `cloud_slow` talk to real Agent Runtime sandboxes. They are
skipped unless ITP_CLOUD_TESTS=1 (`make test-cloud`). Their settings come from the
`cloud_settings` fixture: a snapshot of the cloud variables, taken here before the
scrub removes them, and only when the cloud tests are opted in.
"""

import os

import pytest

pytest_plugins = ["pytester"]  # tests/unit/test_cloud_marker_gate.py runs pytest

CLOUD_TESTS_OPT_IN = "ITP_CLOUD_TESTS"
CLOUD_SKIP_REASON = "cloud test: set ITP_CLOUD_TESTS=1 (make test-cloud)"
_CLOUD_MARKERS = ("cloud", "cloud_slow")
_CLOUD_SETTING_NAMES = (
    "SANDBOX_ENGINE",
    "SANDBOX_TEMPLATE",
    "SANDBOX_CALLER_SA",
    "SANDBOX_READY_TIMEOUT_S",
    "GOOGLE_CLOUD_PROJECT",
)
# Taken before the scrub below, and only for an opted-in session.
CLOUD_SETTINGS: dict[str, str] = (
    {name: os.environ[name] for name in _CLOUD_SETTING_NAMES if name in os.environ}
    if os.environ.get(CLOUD_TESTS_OPT_IN) == "1"
    else {}
)

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
    "SANDBOX_ENGINE",
    "SANDBOX_TEMPLATE",
    "SANDBOX_CALLER_SA",
    "SANDBOX_READY_TIMEOUT_S",
    "TRACE_TO_CLOUD",
):
    os.environ.pop(_variable, None)

# Imported only after the environment is clean: app/__init__.py imports app.agent.
from app.environment import registry  # noqa: E402


def pytest_collection_modifyitems(config, items):
    """Skip every test marked `cloud` or `cloud_slow` unless ITP_CLOUD_TESTS=1."""
    if os.environ.get(CLOUD_TESTS_OPT_IN) == "1":
        return
    skip = pytest.mark.skip(reason=CLOUD_SKIP_REASON)
    for item in items:
        if any(item.get_closest_marker(name) for name in _CLOUD_MARKERS):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def cloud_settings() -> dict[str, str]:
    """The cloud settings of an opted-in session (CLOUD_SETTINGS), for tests marked
    `cloud` or `cloud_slow`; empty otherwise. Read them here, never from os.environ,
    which the scrub above cleaned."""
    return dict(CLOUD_SETTINGS)


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
def _no_cloud_config(monkeypatch):
    """No test runs on the developer's sandbox backend. .env may hold the cloud
    settings and a module can load it mid-session (the entry points do), so the
    backend and its settings are removed for every test; a test that needs them
    sets its own."""
    for name in (
        "ENVIRONMENT_BACKEND",
        "SANDBOX_ENGINE",
        "SANDBOX_TEMPLATE",
        "SANDBOX_CALLER_SA",
        "SANDBOX_READY_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    registry.clear()
