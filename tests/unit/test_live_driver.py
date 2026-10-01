"""The driver's side of a live run's public reply (decision 10A): one failure comment
for a run whose outcome the driver produced (a classified exception, a crash), with
a public reason that says what happened and nothing more; none for a refused or
cancelled run, and none on top of `report_failure`'s. Also how the ask step treats
Ctrl-C, how a cancelled run still gets its record, and the reasons a failed
delivery leaves in record.json."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from google.genai import errors as genai_errors
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import driver, pr_text
from app.approval import ApprovalDecision, ApprovalRequest
from app.driver import (
    PUBLIC_AGENT,
    PUBLIC_CRASH,
    PUBLIC_INFRA,
    RunCrashed,
    _public_reason,
    run_pipeline,
)
from app.environment.base import InfraError
from app.github_client import GitHubClient
from app.models import RoleModels
from app.nodes import intake
from app.pipeline import build_workflow
from app.pr_text import marker
from app.schemas import RunRecord, RunRequest
from tests.fakes import FakeEnvironment, FakeLlm, call, json_out, raises
from tests.unit.archives import build
from tests.unit.delivery_fakes import REPO
from tests.unit.live_rest import (
    ISSUE,
    PLAN,
    PULL_URL,
    HangingLlm,
    LiveGitHubRest,
    live_request,
    live_workflow,
    serve,
    source_archive,
    use_sandbox,
)

GITHUB_TEXT = "zebra-github-says"
HOME_PATH = "/Users/someone"
PROJECT = "owner-private-proj-4711"
COMMENTS = f"/issues/{ISSUE}/comments"


@dataclass
class Live:
    server: LiveGitHubRest
    env: FakeEnvironment
    runs: Path
    attempts: list  # every call of the driver's own comment step

    @property
    def stored(self) -> dict:
        return json.loads((self.runs / "r-live" / "record.json").read_text())

    @property
    def record_text(self) -> str:
        return (self.runs / "r-live" / "record.json").read_text()

    def failure_comments(self) -> list[dict]:
        tag = marker("r-live", "failure")
        return [c for c in self.server.comments if tag in c["body"]]


def spy_driver_comments(monkeypatch) -> list:
    attempts: list = []
    real = driver._comment_on_failure

    async def spy(*args, **kwargs):
        attempts.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(driver, "_comment_on_failure", spy)
    return attempts


@pytest.fixture
def live(bench, monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_REPOS", REPO)
    monkeypatch.setenv("LIVE_ALLOWED_USERS", "owner")
    server = LiveGitHubRest(source_archive(tmp_path))
    serve(monkeypatch, server)
    env = use_sandbox(monkeypatch)
    attempts = spy_driver_comments(monkeypatch)
    return Live(server=server, env=env, runs=bench / "runs", attempts=attempts)


def approve(request: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(
        approved=True, approver="octocat", patch_sha256=request.patch_sha256
    )


def reject(request: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(
        approved=False, approver="octocat", patch_sha256=request.patch_sha256
    )


def answering(decide):
    async def approver(request: ApprovalRequest) -> ApprovalDecision:
        return decide(request)

    return approver


class Waiting:
    """An approver that never answers; `asked` is set when it is asked."""

    def __init__(self) -> None:
        self.asked = asyncio.Event()

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        self.asked.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def run_live(approver=None, models: RoleModels | None = None, **kwargs):
    return await run_pipeline(
        live_request(), workflow=live_workflow(models), approver=approver, **kwargs
    )


def planner_only(*steps) -> RoleModels:
    return RoleModels(
        planner=FakeLlm(list(steps)), coder=FakeLlm([]), reviewer=FakeLlm([])
    )


async def until(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        await asyncio.sleep(0.01)


# --- decision 10A: the driver comments when it produced the outcome -------------------


async def test_budget_failure_on_a_live_run_comments_once(live, monkeypatch):
    monkeypatch.setenv("RUN_BUDGET_USD", "0.001")
    models = planner_only(call("list_dir", path="."), json_out(PLAN))
    record = await run_live(answering(approve), models)
    assert (record.outcome, record.failure_kind) == ("failed", "budget")
    assert live.server.posted_comments() == 1
    [comment] = live.failure_comments()
    assert record.reason in comment["body"]  # numbers only: repeated as it is
    assert "Outcome: `failed`" in comment["body"]
    assert record.comment_posted is True and live.stored["comment_posted"] is True
    assert live.server.pulls == []


async def test_infra_failure_after_intake_comments_once(live):
    record = await run_live(None)  # reaches the gate with no approver: infra
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert live.server.posted_comments() == 1
    # One of the driver's own fixed reasons: repeated as it is.
    assert "approval needed but no approver" in live.failure_comments()[0]["body"]
    assert live.stored["comment_posted"] is True


async def test_a_crashed_live_run_comments_once_and_still_crashes(live):
    # An empty script: the planner's call fails with an AssertionError, a bug.
    with pytest.raises(RunCrashed) as crashed:
        await run_live(answering(approve), planner_only())
    assert isinstance(crashed.value.__cause__, AssertionError)
    assert live.server.posted_comments() == 1
    body = live.failure_comments()[0]["body"]
    assert PUBLIC_CRASH in body and "AssertionError" not in body
    assert crashed.value.record.comment_posted is True
    assert live.stored["failure_kind"] == "infra" and live.stored["comment_posted"]
    assert live.stored["reason"].startswith("unhandled AssertionError")


async def test_a_driver_classified_agent_failure_comments_once(live):
    error = genai_errors.ClientError(
        400,
        {
            "error": {
                "code": 400,
                "message": "Request contains an invalid argument: zebra-request",
                "status": "INVALID_ARGUMENT",
            }
        },
    )
    record = await run_live(answering(approve), planner_only(raises(error)))
    assert (record.outcome, record.failure_kind) == ("failed", "agent")
    assert record.reason.startswith("model rejected the request: 400")
    assert live.server.posted_comments() == 1
    body = live.failure_comments()[0]["body"]
    assert PUBLIC_AGENT in body and "zebra-request" not in body
    assert "zebra-request" in live.record_text
    assert record.comment_posted is True


def _vertex_403() -> genai_errors.ClientError:
    resource = (
        f"//aiplatform.googleapis.com/projects/{PROJECT}/locations/us-central1/"
        "publishers/google/models/gemini-3.8-flash"
    )
    return genai_errors.ClientError(
        403,
        {
            "error": {
                "code": 403,
                "message": f"Permission 'aiplatform.endpoints.predict' denied on "
                f"resource '{resource}' (or it may not exist).",
                "status": "PERMISSION_DENIED",
            }
        },
    )


def _sandbox_down(monkeypatch) -> RoleModels:
    async def docker_down():
        raise InfraError(
            "sandbox fake-1 unavailable: Cannot connect to the Docker daemon at "
            f"unix://{HOME_PATH}/.docker/run/docker.sock. Is the docker daemon running?"
        )

    monkeypatch.setattr(intake, "start_environment", docker_down)
    return planner_only()


def _crash_with_a_path(monkeypatch) -> RoleModels:
    missing = FileNotFoundError(
        2, "No such file or directory", f"{HOME_PATH}/projects/secret-plan.txt"
    )
    return planner_only(raises(missing))


def _model_forbidden(monkeypatch) -> RoleModels:
    return planner_only(raises(_vertex_403()))


@pytest.mark.parametrize(
    ("setup", "public", "secret"),
    [
        (_sandbox_down, PUBLIC_INFRA, HOME_PATH),
        (_crash_with_a_path, PUBLIC_CRASH, HOME_PATH),
        (_model_forbidden, PUBLIC_INFRA, PROJECT),
    ],
    ids=["sandbox-home-path", "crash-host-path", "vertex-project-id"],
)
async def test_the_public_comment_says_what_happened_and_nothing_more(
    live, monkeypatch, setup, public, secret
):
    models = setup(monkeypatch)
    try:
        await run_live(answering(approve), models)
    except RunCrashed:
        pass
    assert live.server.posted_comments() == 1
    body = live.failure_comments()[0]["body"]
    assert public in body
    assert secret not in body and "/Users/" not in body
    # The record keeps the full reason for the owner.
    assert secret in live.record_text


@pytest.mark.parametrize(
    ("kind", "reason", "crashed", "public"),
    [
        ("budget", "run cost $1.002 reached the $1.00 cap", False, None),
        ("budget", "run exceeded 1500 s wall clock", False, None),
        ("agent", "malformed model output: zebra", False, PUBLIC_AGENT),
        ("infra", "approval needed but no approver", False, None),
        ("infra", "workflow ended without an outcome", False, None),
        ("infra", "GitHub refused the pull request", False, None),
        ("infra", "GitHub was unavailable while opening the pull request", False, None),
        ("infra", "resumed run exceeded 300 s wall clock", False, None),
        ("infra", "InfraError: docker at /Users/someone", False, PUBLIC_INFRA),
        ("infra", "unhandled OSError: /Users/someone", True, PUBLIC_CRASH),
    ],
)
def test_public_reason(kind, reason, crashed, public):
    record = RunRecord(
        task_id="t", run_id="r", outcome="failed", failure_kind=kind, reason=reason
    )
    assert _public_reason(record, crashed) == (public or reason)


async def test_comment_failure_does_not_hide_a_crash(live, caplog):
    live.server.fail_comments = True  # GitHub answers 500: post_failure_comment logs
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RunCrashed) as crashed:
            await run_live(answering(approve), planner_only())
    assert isinstance(crashed.value.__cause__, AssertionError)
    assert crashed.value.record.comment_posted is False
    assert live.stored["reason"].startswith("unhandled AssertionError")
    assert live.stored["comment_posted"] is False
    assert "could not post the failure comment" in caplog.text


async def test_an_unexpected_comment_error_is_logged_and_hides_nothing(
    live, monkeypatch, caplog
):
    async def broken(*args, **kwargs):
        raise RuntimeError("comment helper bug")

    monkeypatch.setattr(driver, "post_failure_comment", broken)
    with caplog.at_level(logging.WARNING, logger="app.driver"):
        with pytest.raises(RunCrashed) as crashed:
            await run_live(answering(approve), planner_only())
    assert isinstance(crashed.value.__cause__, AssertionError)
    assert live.stored["comment_posted"] is False
    assert "RuntimeError" in caplog.text

    # A classified failure keeps its outcome too.
    monkeypatch.setenv("RUN_BUDGET_USD", "0.001")
    budget = planner_only(call("list_dir", path="."), json_out(PLAN))
    record = await run_live(answering(approve), budget)
    assert (record.outcome, record.failure_kind) == ("failed", "budget")
    assert record.comment_posted is False


async def test_a_stalled_comment_is_cut_off(live, monkeypatch):
    monkeypatch.setattr(driver, "COMMENT_TIMEOUT_S", 0.2)
    live.server.stall_comments = True
    record = await asyncio.wait_for(run_live(None), timeout=10)
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.comment_posted is False


async def test_a_cancel_during_the_failure_comment_still_sets_the_span(
    live, monkeypatch
):
    posting = asyncio.Event()

    async def stalled(*args, **kwargs):
        posting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(driver, "post_failure_comment", stalled)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # No approver: the run ends at the gate as infra, and the driver comments.
    task = asyncio.ensure_future(run_live(None, tracer=provider.get_tracer("test")))
    await asyncio.wait_for(posting.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The record was written before the comment.
    assert (live.stored["outcome"], live.stored["failure_kind"]) == ("failed", "infra")
    assert live.stored["comment_posted"] is False
    [root] = [s for s in exporter.get_finished_spans() if s.name == "issue_to_pr.run"]
    assert (root.attributes["outcome"], root.attributes["failure_kind"]) == (
        "failed",
        "infra",
    )


async def test_a_refused_live_run_never_comments(live, tmp_path):
    # The issue is fetched, then provisioning refuses the archive (it holds a link).
    live.server.archive = build(
        tmp_path / "linked.tar.gz",
        [
            ("acme-widgets-aaaa", None),
            ("acme-widgets-aaaa/mini.py", b"x = 1\n"),
            ("acme-widgets-aaaa/evil", ("sym", "/etc/passwd")),
        ],
    ).read_bytes()
    record = await run_live(answering(approve))
    assert record.outcome == "refused"
    assert live.server.count("GET", f"/issues/{ISSUE}") == 1  # the issue was fetched
    assert live.server.count("GET", COMMENTS) == 0 and live.attempts == []
    assert record.comment_posted is False


async def test_a_rejected_run_is_commented_on_once_by_report_failure(live):
    record = await run_live(answering(reject))
    assert (record.outcome, record.failure_kind) == ("rejected", "none")
    assert live.server.posted_comments() == 1
    assert "Decision by @octocat." in live.failure_comments()[0]["body"]
    assert record.comment_posted is True
    # report_failure's one listing and post; the driver attempted nothing.
    assert live.server.count("GET", COMMENTS) == 1
    assert live.attempts == []


async def test_an_opened_pull_request_gets_no_failure_comment(live):
    record = await run_live(answering(approve))
    assert (record.outcome, record.pr_url) == ("pr_opened", PULL_URL)
    assert live.server.comments == [] and live.attempts == []


@pytest.mark.parametrize("ending", ["budget", "crash"])
async def test_a_bench_run_never_comments(bench, monkeypatch, ending):
    built: list = []

    def recording(**kwargs):
        built.append(kwargs)
        raise AssertionError("a bench run built a GitHub client")

    monkeypatch.setattr(GitHubClient, "from_token_file", staticmethod(recording))
    attempts = spy_driver_comments(monkeypatch)
    use_sandbox(monkeypatch)
    if ending == "budget":
        monkeypatch.setenv("RUN_BUDGET_USD", "0.001")
        models = planner_only(call("list_dir", path="."), json_out(PLAN))
    else:
        models = planner_only()
    request = RunRequest(task_id="t-1", run_id="r-bench")
    try:
        record = await run_pipeline(request, workflow=build_workflow(models))
    except RunCrashed as crashed:
        record = crashed.record
    assert record.outcome == "failed" and record.comment_posted is False
    assert built == [] and attempts == []


# --- ruling 1: Ctrl-C at the prompt is a rejection -----------------------------------


async def test_ctrl_c_at_the_prompt_is_a_rejection(live):
    async def interrupted(request):
        raise KeyboardInterrupt

    record = await run_live(interrupted)
    assert (record.outcome, record.failure_kind) == ("rejected", "none")
    assert record.reason == "approval cancelled"
    assert live.server.pulls == []
    assert live.server.posted_comments() == 1
    assert "Decision by" not in live.failure_comments()[0]["body"]
    assert live.stored["reason"] == "approval cancelled"


async def test_a_prompt_cancelled_inside_the_ask_is_a_rejection(live):
    # How the CLI turns Ctrl-C into a rejection: it cancels only the prompt's task.
    async def prompt_cancelled(request):
        prompt = asyncio.ensure_future(asyncio.Event().wait())
        asyncio.get_running_loop().call_soon(prompt.cancel)
        await prompt
        raise AssertionError("unreachable")

    record = await run_live(prompt_cancelled)
    assert (record.outcome, record.reason) == ("rejected", "approval cancelled")
    assert live.server.posted_comments() == 1
    assert live.server.pulls == []


async def test_an_outer_cancellation_while_waiting_propagates_after_the_record(live):
    approver = Waiting()
    task = asyncio.ensure_future(run_live(approver))
    await asyncio.wait_for(approver.asked.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (live.stored["outcome"], live.stored["reason"]) == (
        "rejected",
        "approval cancelled",
    )
    assert live.server.posted_comments() == 1
    assert live.server.pulls == []


# --- a cancelled run still gets its record ----------------------------------------------


async def test_a_cancel_during_the_first_pass_leaves_a_record(live):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    planner = HangingLlm()
    models = RoleModels(planner=planner, coder=FakeLlm([]), reviewer=FakeLlm([]))
    task = asyncio.ensure_future(
        run_live(answering(approve), models, tracer=provider.get_tracer("test"))
    )
    await asyncio.wait_for(planner.called.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (live.stored["outcome"], live.stored["failure_kind"]) == ("failed", "infra")
    assert live.stored["reason"] == "run cancelled"
    assert live.env.closed  # the sandbox was released
    # No comment for a cancelled run.
    assert live.server.count("GET", COMMENTS) == 0 and live.attempts == []
    # The root span carries the record's outcome.
    [root] = [s for s in exporter.get_finished_spans() if s.name == "issue_to_pr.run"]
    assert (root.attributes["outcome"], root.attributes["failure_kind"]) == (
        "failed",
        "infra",
    )


async def test_a_cancel_during_the_resume_after_approval_leaves_a_record(live):
    live.server.stall_after_pull = True
    task = asyncio.ensure_future(run_live(answering(approve)))
    await until(lambda: live.server.pulls)  # GitHub created it; no answer comes
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (live.stored["outcome"], live.stored["failure_kind"]) == ("failed", "infra")
    assert live.stored["reason"] == (
        "run cancelled; the pull request may have been opened"
    )
    assert live.server.count("GET", COMMENTS) == 0 and live.attempts == []


async def test_a_second_cancel_during_the_resume_leaves_a_record(live):
    approver = Waiting()
    live.server.stall_comments = True  # report_failure's comment never completes
    task = asyncio.ensure_future(run_live(approver))
    await asyncio.wait_for(approver.asked.wait(), timeout=10)
    task.cancel()  # at the ask: a rejection, then the resume
    await until(lambda: live.server.count("GET", COMMENTS) > 0)
    task.cancel()  # during the resume
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (live.stored["outcome"], live.stored["reason"]) == (
        "failed",
        "run cancelled",
    )
    assert live.attempts == []


async def test_a_crash_in_the_resume_after_a_cancel_surfaces_as_the_crash(
    live, monkeypatch
):
    def broken_template(**kwargs):
        raise RuntimeError("template bug")

    monkeypatch.setattr(pr_text, "failure_comment", broken_template)
    approver = Waiting()
    task = asyncio.ensure_future(run_live(approver))
    await asyncio.wait_for(approver.asked.wait(), timeout=10)
    task.cancel()  # at the ask: report_failure then crashes on its comment
    with pytest.raises(RunCrashed) as crashed:
        await task
    assert isinstance(crashed.value.__cause__, RuntimeError)
    assert live.stored["reason"].startswith("unhandled RuntimeError")


# --- ruling 2: no GitHub response text in the record ----------------------------------


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (503, {}),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}),
    ],
)
async def test_github_unavailable_at_delivery_has_a_fixed_reason(live, status, headers):
    live.server.pulls_error = (status, GITHUB_TEXT, headers)
    record = await run_live(answering(approve))
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.reason == "GitHub was unavailable while opening the pull request"
    assert GITHUB_TEXT not in live.record_text
    assert "/pulls" not in live.record_text
    # The driver's comment carries the same fixed reason, not GitHub's text.
    [comment] = live.failure_comments()
    assert record.reason in comment["body"] and GITHUB_TEXT not in comment["body"]


# --- ruling 3: the resume cap after an approval ----------------------------------------


async def test_resume_cap_after_an_approval_says_the_pr_may_be_open(live, monkeypatch):
    monkeypatch.setattr(driver, "RESUME_TIMEOUT_S", 0.3)
    live.server.stall_after_pull = True
    record = await run_live(answering(approve))
    assert len(live.server.pulls) == 1  # GitHub opened it; its answer never came
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.reason == (
        "resumed run exceeded 0.3 s wall clock; the pull request may have been opened"
    )
    assert live.stored["reason"] == record.reason
    # The pull request, if it was opened, is the reply: no failure comment.
    assert live.server.count("GET", COMMENTS) == 0 and live.attempts == []
    assert record.comment_posted is False


async def test_resume_cap_after_a_rejection_does_not_mention_a_pr(live, monkeypatch):
    monkeypatch.setattr(driver, "RESUME_TIMEOUT_S", 0.3)
    monkeypatch.setattr(driver, "COMMENT_TIMEOUT_S", 0.3)
    live.server.stall_comments = True
    record = await asyncio.wait_for(run_live(answering(reject)), timeout=10)
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.reason == "resumed run exceeded 0.3 s wall clock"
    assert live.server.pulls == []
    assert len(live.attempts) == 1  # the driver tried; GitHub never answered
