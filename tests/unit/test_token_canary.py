"""The token canary: a whole live run with a sentinel token in the token file. The
token must reach GitHub in the Authorization header and appear nowhere else."""

import json
import logging
import os
import sys
import uuid
from typing import Any, ClassVar

import httpx
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import InMemoryRunner
from google.adk.telemetry import tracing as adk_tracing
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import PrivateAttr

from app import driver
from app.approval import ApprovalDecision, ApprovalRequest
from app.driver import USER_ID, run_pipeline
from app.github_client import GitHubClient
from app.models import RoleModels
from tests.fakes import FakeLlm, json_out
from tests.unit.delivery_fakes import REPO
from tests.unit.live_rest import (
    APPROVE,
    PATCH,
    PLAN,
    PULL_URL,
    LiveGitHubRest,
    live_request,
    live_workflow,
    no_sleep,
    source_archive,
    use_sandbox,
)


class RecordingLlm(FakeLlm):
    """FakeLlm that keeps every request it was sent."""

    _requests: list[LlmRequest] = PrivateAttr(default_factory=list)

    def __init__(self, steps: list[dict], **kwargs: Any) -> None:
        super().__init__(steps, **kwargs)

    @property
    def requests(self) -> list[LlmRequest]:
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        self._requests.append(llm_request)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


class RecordingRunner(InMemoryRunner):
    instances: ClassVar[list["RecordingRunner"]] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        RecordingRunner.instances.append(self)


def _request_text(request: LlmRequest) -> str:
    parts = [str(request.config.system_instruction or "")]
    parts += [content.model_dump_json() for content in request.contents]
    return "\n".join(parts)


def _span_text(span) -> str:
    events = [(event.name, dict(event.attributes or {})) for event in span.events]
    return json.dumps(
        {
            "name": span.name,
            "attributes": dict(span.attributes or {}),
            "events": events,
            "status": span.status.description,
        },
        default=str,
    )


def _capture_adk_spans(monkeypatch) -> tuple[InMemorySpanExporter, TracerProvider]:
    """Point ADK's own tracer (module attribute `tracer`, imported by name into
    several modules) at an in-memory exporter for this test only."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    ours = provider.get_tracer("gcp.vertex.agent")
    original = adk_tracing.tracer
    for name, module in list(sys.modules.items()):
        if (
            name.startswith("google.adk")
            and getattr(module, "tracer", None) is original
        ):
            monkeypatch.setattr(module, "tracer", ours)
    return exporter, provider


async def test_token_canary_end_to_end(bench, tmp_path, monkeypatch, caplog):
    canary = f"ghp_CANARY_{uuid.uuid4().hex}"
    token_file = tmp_path / "secrets" / "github-token"
    token_file.parent.mkdir()
    token_file.write_text(canary + "\n")
    token_file.chmod(0o600)
    monkeypatch.setenv("GITHUB_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("LIVE_REPOS", REPO)
    monkeypatch.setenv("LIVE_ALLOWED_USERS", "owner")

    server = LiveGitHubRest(source_archive(tmp_path))
    real_from_token_file = GitHubClient.from_token_file.__func__

    def from_token_file(cls, **kwargs):
        # The real token loading; only the transport is the in-memory server.
        return real_from_token_file(
            cls, transport=httpx.MockTransport(server), sleep=no_sleep, **kwargs
        )

    monkeypatch.setattr(GitHubClient, "from_token_file", classmethod(from_token_file))
    env = use_sandbox(monkeypatch)
    RecordingRunner.instances = []
    monkeypatch.setattr(driver, "InMemoryRunner", RecordingRunner)
    exporter, provider = _capture_adk_spans(monkeypatch)
    models = RoleModels(
        planner=RecordingLlm([json_out(PLAN)]),
        coder=RecordingLlm([json_out(PATCH)]),
        reviewer=RecordingLlm([json_out(APPROVE)]),
    )
    requests: list[ApprovalRequest] = []

    async def approver(request: ApprovalRequest) -> ApprovalDecision:
        requests.append(request)
        return ApprovalDecision(
            approved=True, approver="octocat", patch_sha256=request.patch_sha256
        )

    caplog.set_level(logging.DEBUG)
    record = await run_pipeline(
        live_request(),
        workflow=live_workflow(models),
        approver=approver,
        tracer=provider.get_tracer("test"),
    )
    assert (record.outcome, record.pr_url) == ("pr_opened", PULL_URL)

    # Positive control: the token reached GitHub, in the Authorization header.
    assert server.seen
    for seen in server.seen:
        assert seen.headers["authorization"] == f"Bearer {canary}"
        rest = {k: v for k, v in seen.headers.items() if k != "authorization"}
        assert canary not in json.dumps(rest)
        assert canary not in seen.url
        assert canary.encode() not in seen.body

    # Session state and the session's events.
    [runner] = RecordingRunner.instances
    listed = await runner.session_service.list_sessions(app_name="app", user_id=USER_ID)
    assert listed.sessions
    for listed_session in listed.sessions:
        session = await runner.session_service.get_session(
            app_name="app", user_id=USER_ID, session_id=listed_session.id
        )
        assert session is not None and session.state
        for key, value in session.state.items():
            assert canary not in json.dumps(value, default=str), key
        for event in session.events:
            assert canary not in event.model_dump_json()

    # Everything the run wrote: events.jsonl, record.json, pr_body.md and the rest.
    run_dir = bench / "runs" / "r-live"
    for name in ("events.jsonl", "record.json", "pr_body.md"):
        assert (run_dir / name).is_file()
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert canary.encode() not in path.read_bytes(), path.name

    # Every model request: system instruction and contents.
    sent = [
        r for m in (models.planner, models.coder, models.reviewer) for r in m.requests
    ]
    assert len(sent) == 3
    for request in sent:
        assert canary not in _request_text(request)

    # The approval request.
    assert len(requests) == 1 and canary not in requests[0].model_dump_json()

    # The sandbox: every command, written file and uploaded file.
    assert env.commands and env.files
    assert not any(canary in command for command in env.commands)
    assert not any(canary in p or canary in text for p, text in env.files.items())

    # Every span and span event, ADK's included.
    spans = exporter.get_finished_spans()
    assert any(span.name == "issue_to_pr.run" for span in spans)
    assert len({span.name for span in spans}) > 1  # ADK's spans were captured too
    for span in spans:
        assert canary not in _span_text(span), span.name

    # Every log record at DEBUG and above.
    assert caplog.records
    formatter = logging.Formatter()
    for log in caplog.records:
        assert canary not in formatter.format(log), log.name
    assert canary not in caplog.text

    # The process environment.
    assert not any(canary in value for value in os.environ.values())
    assert not any(canary in name for name in os.environ)
