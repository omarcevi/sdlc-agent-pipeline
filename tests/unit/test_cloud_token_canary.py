"""The cloud token canary (spec §4.6, §8): whole bench runs on the cloud backend's
fakes, with a sentinel sandbox token from the fake signer. The token must reach the
sandbox proxy in the Authorization header of every request and appear nowhere else:
not in other headers, URLs or bodies, the sandbox, session state or events, the run's
files, the record (its error text included), model requests, spans, logs, output,
the process environment or the environment's repr."""

import json
import logging
import os
import uuid

import httpx
import pytest

from app import driver
from app.driver import USER_ID, run_pipeline
from app.models import RoleModels
from app.nodes.verify import TEST_CMD
from app.pipeline import build_workflow
from app.schemas import RunRequest
from tests.fakes import FakeEnvironment, json_out
from tests.unit.sandbox_fakes import (
    CLOUD_ENV,
    FakeSandboxControl,
    reset_template_checks,
)
from tests.unit.test_cloud_backend_release import use_cloud_sandbox
from tests.unit.test_pipeline import APPROVE, PASS, PATCH, PLAN, diff_responses
from tests.unit.test_token_canary import (
    RecordingLlm,
    RecordingRunner,
    _capture_adk_spans,
    _request_text,
    _span_text,
)


@pytest.fixture(autouse=True)
def _fresh_template_checks():
    reset_template_checks()
    yield
    reset_template_checks()


class LostConnection(FakeEnvironment):
    """A sandbox whose connection drops when the tests run: the run ends as infra,
    with the backend's error text in its record."""

    async def exec(self, command, *, timeout=120.0, cwd="/workspace/repo"):
        if command == TEST_CMD:
            raise httpx.ReadError("connection reset by peer")
        return await super().exec(command, timeout=timeout, cwd=cwd)


CASES = {
    "patch_written": (FakeEnvironment, "patch_written"),
    "infra": (LostConnection, "failed"),
}


@pytest.mark.parametrize("case", sorted(CASES))
async def test_sandbox_token_reaches_only_request_headers_in_a_pipeline_run(
    bench, monkeypatch, caplog, capfd, case
):
    sandbox_class, outcome = CASES[case]
    monkeypatch.setenv("ENVIRONMENT_BACKEND", "agent_runtime")
    for name, value in CLOUD_ENV.items():
        monkeypatch.setenv(name, value)
    canary = f"canary-sandbox-jwt-{uuid.uuid4().hex}"
    sandbox = sandbox_class(responses={**diff_responses(), TEST_CMD: PASS})
    fakes = use_cloud_sandbox(monkeypatch, sandbox, FakeSandboxControl(token=canary))
    RecordingRunner.instances = []
    monkeypatch.setattr(driver, "InMemoryRunner", RecordingRunner)
    exporter, provider = _capture_adk_spans(monkeypatch)
    models = RoleModels(
        planner=RecordingLlm([json_out(PLAN)]),
        coder=RecordingLlm([json_out(PATCH)]),
        reviewer=RecordingLlm([json_out(APPROVE)]),
    )

    caplog.set_level(logging.DEBUG)
    record = await run_pipeline(
        RunRequest(task_id="t-1", run_id="r-1"),
        workflow=build_workflow(models),
        tracer=provider.get_tracer("test"),
    )
    assert record.outcome == outcome
    if case == "infra":
        assert record.failure_kind == "infra"
        assert "POST /exec failed: ReadError" in record.reason

    # The record, its reason (an exception's text in the infra case) included.
    assert canary not in record.model_dump_json()

    # Positive control: every request reached the proxy with the token, and only in
    # the Authorization header.
    [env] = fakes.started
    assert fakes.control.signs and fakes.control.deletes  # signed, then deleted
    assert len(fakes.shim.requests) > 4  # readiness, mkdir, upload, git, ...
    for request in fakes.shim.requests:
        assert request.headers["authorization"] == f"Bearer {canary}"
        rest = {k: v for k, v in request.headers.items() if k != "authorization"}
        assert canary not in json.dumps(rest)
        assert canary not in str(request.url)
        assert canary.encode() not in request.content

    # The sandbox: every command, its directory, and every file.
    assert fakes.shim.exec_calls and fakes.shim.files
    for command, cwd in fakes.shim.exec_calls:
        assert canary not in command and canary not in (cwd or "")
    for path, content in fakes.shim.files.items():
        assert canary not in path and canary.encode() not in content
    assert not any(canary in command for command in sandbox.commands)

    # The environment object shows its short id only.
    assert canary not in repr(env) and canary not in str(env)

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

    # Everything the run wrote.
    run_dir = bench / "runs" / "r-1"
    assert (run_dir / "events.jsonl").is_file() and (run_dir / "record.json").is_file()
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert canary.encode() not in path.read_bytes(), path.name

    # Every model request: system instruction, contents and tool declarations.
    sent = [
        r for m in (models.planner, models.coder, models.reviewer) for r in m.requests
    ]
    assert sent
    for request in sent:
        assert canary not in _request_text(request)

    # Every span and span event, ADK's included.
    spans = exporter.get_finished_spans()
    assert "issue_to_pr.run" in {span.name for span in spans}
    for span in spans:
        assert canary not in _span_text(span), span.name

    # Every log record at DEBUG and above (httpx logs each request).
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
