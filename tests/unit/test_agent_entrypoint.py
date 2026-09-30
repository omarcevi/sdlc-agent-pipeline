"""The agents-cli entry point: importing it must be free of cloud side effects."""

import sys
from types import ModuleType

import google.cloud
import pytest
from google.adk.plugins import ReflectAndRetryModelPlugin

from app import agent as entrypoint
from app.budget import BudgetPlugin
from app.guardrails import GuardrailPlugin

BIGQUERY = "google.cloud.bigquery"
ANALYTICS_PLUGIN = "google.adk.plugins.bigquery_agent_analytics_plugin"


@pytest.fixture
def no_bigquery(monkeypatch):
    """Any attempt to import BigQuery or the analytics plugin raises ImportError."""
    monkeypatch.setitem(sys.modules, BIGQUERY, None)
    monkeypatch.setitem(sys.modules, ANALYTICS_PLUGIN, None)
    monkeypatch.delattr(google.cloud, "bigquery", raising=False)


def test_entry_point_names():
    assert entrypoint.root_agent.name == "issue_to_pr"
    assert entrypoint.app.name == "app"
    assert entrypoint.app.root_agent is entrypoint.root_agent


def test_app_plugins_are_budget_then_retry_then_guardrails():
    plugins = entrypoint.app.plugins
    assert [type(p) for p in plugins] == [
        BudgetPlugin,
        ReflectAndRetryModelPlugin,
        GuardrailPlugin,
    ]
    assert plugins[1].max_retries == 2


def test_analytics_is_off_when_only_the_project_is_set(monkeypatch, no_bigquery):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "dummy-project")
    monkeypatch.delenv("BQ_ANALYTICS_ENABLED", raising=False)
    assert entrypoint._analytics_plugins() == []


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_analytics_opt_in_must_be_exactly_1(value, monkeypatch, no_bigquery):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "dummy-project")
    monkeypatch.setenv("BQ_ANALYTICS_ENABLED", value)
    assert entrypoint._analytics_plugins() == []


def test_analytics_needs_a_project_even_when_enabled(monkeypatch, no_bigquery):
    monkeypatch.setenv("BQ_ANALYTICS_ENABLED", "1")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    assert entrypoint._analytics_plugins() == []


def test_analytics_opt_in_builds_the_plugin(monkeypatch):
    """Both switches on: the dataset is ensured and the plugin is returned. BigQuery
    and the plugin are stand-ins, so nothing here reaches GCP."""
    datasets = []

    class FakeClient:
        def __init__(self, project):
            self.project = project

        def create_dataset(self, dataset, exists_ok):
            datasets.append((self.project, dataset, exists_ok))

    class FakePlugin:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    bigquery = ModuleType(BIGQUERY)
    bigquery.Client = FakeClient
    plugin_module = ModuleType(ANALYTICS_PLUGIN)
    plugin_module.BigQueryAgentAnalyticsPlugin = FakePlugin
    plugin_module.BigQueryLoggerConfig = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, BIGQUERY, bigquery)
    monkeypatch.setitem(sys.modules, ANALYTICS_PLUGIN, plugin_module)
    monkeypatch.setattr(google.cloud, "bigquery", bigquery, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "dummy-project")
    monkeypatch.setenv("BQ_ANALYTICS_ENABLED", "1")
    monkeypatch.delenv("BQ_ANALYTICS_DATASET_ID", raising=False)

    (plugin,) = entrypoint._analytics_plugins()
    assert isinstance(plugin, FakePlugin)
    assert plugin.kwargs["project_id"] == "dummy-project"
    assert datasets == [("dummy-project", "dummy-project.adk_agent_analytics", True)]
