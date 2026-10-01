"""The token canary: whole live runs with a sentinel token in the token file, one
for each code path that reads it (open_pr, report_failure's comment, the driver's
failure comment). The token must reach GitHub in the Authorization header and
appear nowhere else."""

import importlib
import json
import logging
import os
import sys
import uuid
from typing import Any, ClassVar

import httpx
import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import InMemoryRunner
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer
from pydantic import PrivateAttr

from app import driver
from app.approval import ApprovalDecision, ApprovalRequest
from app.driver import USER_ID, run_pipeline
from app.github_client import GitHubClient
from app.models import RoleModels
from tests.fakes import FakeLlm, call, json_out
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
    """System instruction, contents and tool declarations of one model request."""
    parts = [str(request.config.system_instruction or "")]
    parts += [content.model_dump_json() for content in request.contents]
    for tool in request.config.tools or []:
        dump = getattr(tool, "model_dump_json", None)
        parts.append(dump() if dump else repr(tool))
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
    """Point every ADK module's `tracer` at an in-memory exporter, for this test
    only. Modules that a run imports lazily are imported first, so they are
    patched, and restored, with the rest."""
    for name in ADK_LAZY_TRACING:
        importlib.import_module(name)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    ours = provider.get_tracer("gcp.vertex.agent")
    patched = []
    for name, module in list(sys.modules.items()):
        if name.startswith("google.adk") and isinstance(
            getattr(module, "tracer", None), Tracer
        ):
            monkeypatch.setattr(module, "tracer", ours)
            patched.append(name)
    assert "google.adk.telemetry.node_tracing" in patched
    return exporter, provider


# A run imports these during its first node or agent call.
ADK_LAZY_TRACING = ("google.adk.telemetry.node_tracing", "google.adk.apps.compaction")


def _scripted(case: str) -> RoleModels:
    if case == "budget":
        planner = RecordingLlm([call("list_dir", path="."), json_out(PLAN)])
        return RoleModels(
            planner=planner, coder=RecordingLlm([]), reviewer=RecordingLlm([])
        )
    return RoleModels(
        planner=RecordingLlm([json_out(PLAN)]),
        coder=RecordingLlm([json_out(PATCH)]),
        reviewer=RecordingLlm([json_out(APPROVE)]),
    )


# pr_opened: open_pr's client. rejected: report_failure's comment. budget: the
# driver's own failure comment (decision 10A). Each reads the token.
CASES = {
    "pr_opened": ("pr_opened", True),
    "rejected": ("rejected", False),
    "budget": ("failed", True),
}


@pytest.mark.parametrize("case", sorted(CASES))
async def test_token_canary_end_to_end(
    bench, tmp_path, monkeypatch, caplog, capfd, case
):
    outcome, approves = CASES[case]
    if case == "budget":
        monkeypatch.setenv("RUN_BUDGET_USD", "0.001")
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
    models = _scripted(case)
    requests: list[ApprovalRequest] = []

    async def approver(request: ApprovalRequest) -> ApprovalDecision:
        requests.append(request)
        return ApprovalDecision(
            approved=approves, approver="octocat", patch_sha256=request.patch_sha256
        )

    caplog.set_level(logging.DEBUG)
    record = await run_pipeline(
        live_request(),
        workflow=live_workflow(models),
        approver=approver,
        tracer=provider.get_tracer("test"),
    )
    assert record.outcome == outcome
    if case == "pr_opened":
        assert record.pr_url == PULL_URL
    else:
        # The token-reading comment path ran: one comment is on the issue.
        assert server.posted_comments() == 1 and record.comment_posted

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
    written = ["events.jsonl", "record.json"]
    if case != "budget":
        written.append("pr_body.md")
    for name in written:
        assert (run_dir / name).is_file()
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert canary.encode() not in path.read_bytes(), path.name

    # Every model request: system instruction, contents and tool declarations.
    sent = [
        r for m in (models.planner, models.coder, models.reviewer) for r in m.requests
    ]
    assert len(sent) == (1 if case == "budget" else 3)
    assert any(request.config.tools for request in sent)
    for request in sent:
        assert canary not in _request_text(request)

    # The approval request.
    assert len(requests) == (0 if case == "budget" else 1)
    for request in requests:
        assert canary not in request.model_dump_json()

    # The sandbox: every command, written file and uploaded file.
    assert env.commands and env.files
    assert not any(canary in command for command in env.commands)
    assert not any(canary in p or canary in text for p, text in env.files.items())

    # Every span and span event, ADK's included.
    spans = exporter.get_finished_spans()
    names = {span.name for span in spans}
    assert "issue_to_pr.run" in names
    # ADK's own spans were captured, the lazily imported node tracing's included.
    assert any(name.startswith("invoke_node") for name in names), names
    assert any(name.startswith("invoke_agent") for name in names), names
    for span in spans:
        assert canary not in _span_text(span), span.name

    # Every log record at DEBUG and above.
    assert caplog.records
    formatter = logging.Formatter()
    for log in caplog.records:
        assert canary not in formatter.format(log), log.name
    assert canary not in caplog.text

    # Anything written to the process's stdout or stderr.
    out, err = capfd.readouterr()
    assert canary not in out and canary not in err

    # The process environment.
    assert not any(canary in value for value in os.environ.values())
    assert not any(canary in name for name in os.environ)
