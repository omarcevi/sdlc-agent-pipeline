import pytest
from google.adk.telemetry.setup import OTelHooks
from google.auth.exceptions import DefaultCredentialsError
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app import tracing
from app.driver import run_pipeline
from app.environment.base import ExecResult
from app.models import RoleModels
from app.nodes import intake
from app.nodes.verify import DIFF_CMD, NUMSTAT_CMD, TEST_CMD
from app.pipeline import build_workflow
from app.schemas import PatchResult, Plan, Review, RunRequest
from tests.fakes import FakeEnvironment, FakeLlm, json_out, make_bench_task

DIFF = "diff --git a/mini.py b/mini.py\n--- a/mini.py\n+++ b/mini.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"


@pytest.fixture(autouse=True)
def _fresh_tracing_state(monkeypatch):
    monkeypatch.setattr(tracing, "_enabled", False)


def _forbid_exporters(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("no exporter may be created")

    monkeypatch.setattr(tracing, "get_gcp_exporters", boom)
    monkeypatch.setattr(tracing, "maybe_set_otel_providers", boom)


def test_tracing_is_off_by_default(monkeypatch):
    monkeypatch.delenv("TRACE_TO_CLOUD", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _forbid_exporters(monkeypatch)
    assert tracing.enable_cloud_trace() is False


def test_tracing_needs_a_project(monkeypatch):
    monkeypatch.setenv("TRACE_TO_CLOUD", "1")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    _forbid_exporters(monkeypatch)
    assert tracing.enable_cloud_trace() is False


def test_tracing_is_set_up_once_and_pinned_to_the_project(monkeypatch):
    monkeypatch.setenv("TRACE_TO_CLOUD", "1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    auth_calls: list[dict] = []
    exporter_calls: list[dict] = []
    provider_calls: list[int] = []

    def fake_default(**kwargs):
        auth_calls.append(kwargs)
        return ("credentials", "adc-default-project")

    def fake_exporters(**kwargs):
        exporter_calls.append(kwargs)
        return OTelHooks()

    def fake_providers(hooks, otel_resource=None):
        provider_calls.append(1)

    monkeypatch.setattr(tracing.google.auth, "default", fake_default)
    monkeypatch.setattr(tracing, "get_gcp_exporters", fake_exporters)
    monkeypatch.setattr(tracing, "get_gcp_resource", lambda project_id: project_id)
    monkeypatch.setattr(tracing, "maybe_set_otel_providers", fake_providers)

    assert tracing.enable_cloud_trace() is True
    assert tracing.enable_cloud_trace() is True
    assert len(provider_calls) == 1
    # Quota and destination are this project, not whatever ADC defaults to.
    assert auth_calls == [{"quota_project_id": "some-project"}]
    assert exporter_calls == [
        {"enable_cloud_tracing": True, "google_auth": ("credentials", "some-project")}
    ]


def test_trace_url_points_at_the_project():
    url = tracing.trace_explorer_url("some-project")
    assert "project=some-project" in url and "traces" in url


async def test_each_run_has_a_root_span_with_its_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    env = FakeEnvironment(
        responses={
            DIFF_CMD: ExecResult(exit_code=0, stdout=DIFF, stderr=""),
            NUMSTAT_CMD: ExecResult(exit_code=0, stdout="1\t1\tmini.py\n", stderr=""),
            TEST_CMD: ExecResult(exit_code=0, stdout="2 passed", stderr=""),
        }
    )

    async def fake_start():
        return env

    monkeypatch.setattr(intake, "start_environment", fake_start)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    models = RoleModels(
        planner=FakeLlm([json_out(Plan(actionable=True, summary="fix add"))]),
        coder=FakeLlm([json_out(PatchResult(summary="fixed add"))]),
        reviewer=FakeLlm([json_out(Review(verdict="approve"))]),
    )

    record = await run_pipeline(
        RunRequest(task_id="t-1", run_id="r-trace"),
        workflow=build_workflow(models),
        tracer=provider.get_tracer("test"),
    )

    roots = [s for s in exporter.get_finished_spans() if s.name == "issue_to_pr.run"]
    assert len(roots) == 1
    attributes = dict(roots[0].attributes or {})
    assert attributes["task_id"] == "t-1"
    assert attributes["run_id"] == "r-trace"
    assert attributes["outcome"] == record.outcome == "patch_written"
    assert attributes["failure_kind"] == "none"
    assert attributes["cost_usd"] == record.cost_usd
    assert attributes["tool_calls"] == record.tool_calls
    assert attributes["test_attempts"] == 0 and attributes["review_rounds"] == 0


def test_missing_default_credentials_disable_tracing_with_one_warning(
    monkeypatch, caplog
):
    monkeypatch.setenv("TRACE_TO_CLOUD", "1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    _forbid_exporters(monkeypatch)

    def no_credentials(**kwargs):
        raise DefaultCredentialsError("no adc")

    monkeypatch.setattr(tracing.google.auth, "default", no_credentials)
    with caplog.at_level("WARNING", logger="app.tracing"):
        assert tracing.enable_cloud_trace() is False
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "gcloud auth application-default login" in warnings[0].getMessage()


async def test_unclassified_error_marks_the_root_span_as_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BENCH_TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setenv("BENCH_REPOS_DIR", str(tmp_path / "repos"))
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    make_bench_task(tmp_path)
    env = FakeEnvironment()

    async def fake_start():
        return env

    monkeypatch.setattr(intake, "start_environment", fake_start)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # An empty script makes the planner fail with an AssertionError: a bug, not a
    # classified failure.
    models = RoleModels(planner=FakeLlm([]), coder=FakeLlm([]), reviewer=FakeLlm([]))

    with pytest.raises(AssertionError):
        await run_pipeline(
            RunRequest(task_id="t-1", run_id="r-bug"),
            workflow=build_workflow(models),
            tracer=provider.get_tracer("test"),
        )

    (root,) = [s for s in exporter.get_finished_spans() if s.name == "issue_to_pr.run"]
    assert root.status.status_code == StatusCode.ERROR
