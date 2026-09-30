"""Test-wide setup.

app/agent.py builds a BigQuery analytics plugin at import time when
GOOGLE_CLOUD_PROJECT is set. Unit tests must never touch GCP, so clear it
before any test module imports the package.
"""

import os

os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest

from app.environment import registry


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    registry.clear()
