"""The human approval gate: ADK's pause and resume, the gate and router nodes, the
live graph, the driver's pause-and-resume, and the terminal approver."""

import asyncio
import hashlib
import io
import json
import os
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from google.adk.agents import LlmAgent
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.runners import InMemoryRunner
from google.adk.workflow import FunctionNode, Workflow
from google.genai import types
from pydantic import BaseModel, ValidationError

from app import approval, driver, github_client, pr_text
from app.approval import ApprovalDecision, ApprovalRequest, TerminalApprover
from app.driver import RunCrashed, run_pipeline
from app.environment import registry
from app.environment.base import ExecResult, InfraError
from app.github_client import GitHubConfigError, GitHubError, Issue, PullRequest
from app.models import RoleModels
from app.nodes import finish, intake
from app.nodes.finish import open_pr
from app.nodes.gate import human_gate, route_approval
from app.nodes.intake import fetch_live_issue
from app.nodes.verify import DIFF_CMD, NUMSTAT_CMD, TEST_CMD
from app.pipeline import INFRA_RETRY, build_workflow
from app.schemas import PatchResult, Plan, Review, RunRecord, RunRequest
from tests.fakes import FakeEnvironment, FakeLlm, json_out, text
from tests.unit._constants import DIFF

REPO = "acme/widgets"
ISSUE_URL = f"https://github.com/{REPO}/issues/7"
DIGEST = hashlib.sha256(DIFF.encode()).hexdigest()

# --- ADK's resume behaviour, pinned on a minimal workflow ----------------------------


class _Answer(BaseModel):
    ok: bool


async def test_resume_does_not_rerun_completed_nodes():
    runs: dict[str, int] = {"first": 0, "gate": 0, "last": 0}
    seen: list = []

    def first(node_input: str):
        runs["first"] += 1
        return Event(output="go", state={"sandbox_starts": runs["first"]})

    def gate(node_input):
        runs["gate"] += 1
        yield Event(message="waiting")
        yield RequestInput(
            interrupt_id="approve-r1",
            message="approve?",
            payload={"patch": "x"},
            response_schema=_Answer,
        )

    def last(node_input):
        runs["last"] += 1
        seen.append(node_input)
        return Event(output="done", state={"finished": True})

    model = FakeLlm([text("model says hi")])
    agent = LlmAgent(name="agent", model=model, instruction="say hi")
    first_node, gate_node = FunctionNode(func=first), FunctionNode(func=gate)
    workflow = Workflow(
        name="mini",
        edges=[
            ("START", first_node),
            (first_node, agent),
            (agent, gate_node),
            (gate_node, FunctionNode(func=last)),
        ],
    )
    runner = InMemoryRunner(agent=workflow, app_name="app")
    session = await runner.session_service.create_session(app_name="app", user_id="u")

    first_pass = [
        event
        async for event in runner.run_async(
            user_id="u",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="start")]),
        )
    ]
    pending = [
        part.function_call
        for event in first_pass
        if event.long_running_tool_ids
        for part in ((event.content.parts or []) if event.content else [])
        if part.function_call
        and part.function_call.name == "adk_request_input"
        and part.function_call.id in event.long_running_tool_ids
    ]
    assert [call.id for call in pending] == ["approve-r1"]
    assert pending[0].args["payload"] == {"patch": "x"}
    assert runs == {"first": 1, "gate": 1, "last": 0}
    assert model.calls == 1

    resume = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id="approve-r1", name="adk_request_input", response={"ok": True}
                )
            )
        ],
    )
    second_pass = [
        event
        async for event in runner.run_async(
            user_id="u", session_id=session.id, new_message=resume
        )
    ]
    assert second_pass
    assert runs == {"first": 1, "gate": 1, "last": 1}
    assert model.calls == 1
    assert seen == [{"ok": True}]
    final = await runner.session_service.get_session(
        app_name="app", user_id="u", session_id=session.id
    )
    assert final.state["sandbox_starts"] == 1 and final.state["finished"] is True


# --- the live graph --------------------------------------------------------------------


def _models() -> RoleModels:
    return RoleModels(planner=FakeLlm([]), coder=FakeLlm([]), reviewer=FakeLlm([]))


def _edges(workflow: Workflow) -> list[tuple]:
    return [(e.from_node.name, e.to_node.name, e.route) for e in workflow.graph.edges]


def test_live_graph_edges():
    live = build_workflow(_models(), live=True)
    bench = build_workflow(_models())
    assert live.name == "issue_to_pr"
    assert _edges(live) == [
        *_edges(bench),
        ("deliver_patch", "human_gate", None),
        ("human_gate", "route_approval", None),
        ("route_approval", "open_pr", "approved"),
        ("route_approval", "report_failure", "rejected"),
    ]
    nodes = {node.name: node for node in live.graph.nodes}
    fetch = nodes["fetch_issue"]
    assert isinstance(fetch, FunctionNode) and fetch._func is fetch_live_issue
    assert fetch.retry_config is INFRA_RETRY
    assert nodes["open_pr"]._func is open_pr
    assert nodes["open_pr"].retry_config is INFRA_RETRY
    gate = nodes["human_gate"]
    assert gate._func is human_gate and gate.rerun_on_resume is False
    # One node per function: the gate follows the bench graph's own deliver_patch,
    # and a rejection ends in the same report_failure as every other failure.
    names = [node.name for node in live.graph.nodes]
    assert len(names) == len(set(names))


# --- the gate ------------------------------------------------------------------------

LIVE_ISSUE = {
    "task_id": f"{REPO}#7",
    "run_id": "Run_7",
    "repo": REPO,
    "title": "Parser loses #3 rows @someone",
    "body": "b",
    "mode": "live",
    "issue_number": 7,
    "base_ref": "demo/md-001",
    "base_sha": "a" * 40,
    "base_tree_sha": "b" * 40,
    "html_url": ISSUE_URL,
}
LIVE_DIFF = {
    "unified_diff": DIFF,
    "files": ["mini.py"],
    "insertions": 1,
    "deletions": 1,
}
GATE_STATE = {
    "plan": {"summary": "fix add"},
    "patch": {"summary": "fixed add"},
    "test_report": {"passed": True, "exit_code": 0, "failed_tests": []},
    "review": {"verdict": "approve", "comments": [], "must_fix": ["keep it small"]},
    "budget": {"cost_usd": 0.0123, "tool_calls": 9, "models": ["fake-model"]},
}


def test_gate_yields_a_status_then_the_approval_request(bench):
    items = list(
        human_gate(
            {"outcome": "patch_written"},
            issue=LIVE_ISSUE,
            diff=LIVE_DIFF,
            patch_sha256=DIGEST,
            **GATE_STATE,
        )
    )
    assert len(items) == 2
    status, request_input = items
    assert isinstance(status, Event)
    assert status.content.parts[0].text == "waiting for human approval"
    assert isinstance(request_input, RequestInput)
    assert request_input.interrupt_id == "approve-Run_7"
    assert request_input.response_schema is ApprovalDecision
    assert request_input.message
    request = ApprovalRequest.model_validate(request_input.payload)
    assert request == ApprovalRequest(
        run_id="Run_7",
        repo=REPO,
        issue_number=7,
        issue_url=ISSUE_URL,
        base_ref="demo/md-001",
        branch=pr_text.branch_name(7, "Run_7"),
        pr_title=pr_text.pr_title(LIVE_ISSUE["title"]),
        pr_body=pr_text.pr_body(
            run_id="Run_7",
            issue_number=7,
            diff=LIVE_DIFF,
            patch_sha256=DIGEST,
            **GATE_STATE,
        ),
        files=["mini.py"],
        insertions=1,
        deletions=1,
        patch_path=str(bench / "runs" / "Run_7" / "patch.diff"),
        patch_sha256=DIGEST,
        tests_passed=True,
        test_exit_code=0,
        review_verdict="approve",
        review_must_fix=["keep it small"],
        cost_usd=0.0123,
        tool_calls=9,
    )
    # The body shown is the one deliver_patch rendered, without an approver yet.
    assert "approved by" not in request.pr_body


def test_one_helper_renders_the_body_shown_and_the_body_published(bench):
    *_, request_input = human_gate(
        None, issue=LIVE_ISSUE, diff=LIVE_DIFF, patch_sha256=DIGEST, **GATE_STATE
    )
    shown = ApprovalRequest.model_validate(request_input.payload).pr_body
    assert finish.patch_hash(LIVE_DIFF) == DIGEST
    assert shown == finish.published_body(LIVE_ISSUE, LIVE_DIFF, **GATE_STATE)
    published = finish.published_body(
        LIVE_ISSUE, LIVE_DIFF, approver="octocat", **GATE_STATE
    )
    assert published.replace(", approved by @octocat", "") == shown != published


def test_gate_refuses_a_hash_that_is_not_the_diffs(bench):
    with pytest.raises(RuntimeError, match="patch hash"):
        list(
            human_gate(
                None,
                issue=LIVE_ISSUE,
                diff=LIVE_DIFF,
                patch_sha256="0" * 64,
                **GATE_STATE,
            )
        )


# --- route_approval ------------------------------------------------------------------


def _decide(**over) -> ApprovalDecision:
    fields = {"approved": True, "approver": "octocat", "patch_sha256": DIGEST}
    return ApprovalDecision(**{**fields, **over})


def test_an_approval_of_this_patch_routes_approved_and_passes_the_decision_on():
    decision = _decide()
    event = route_approval(decision, DIGEST)
    assert event.actions.route == "approved"
    # open_pr reads the decision from its node input, the router's output.
    assert event.output == decision
    assert "failure" not in (event.actions.state_delta or {})


@pytest.mark.parametrize(
    ("decision", "reason", "approver"),
    [
        (_decide(approved=False), "not approved by octocat", "octocat"),
        (
            _decide(approved=False, note="tests look thin"),
            "not approved by octocat: tests look thin",
            "octocat",
        ),
        (
            _decide(patch_sha256="f" * 64),
            "approval was for a different patch",
            "octocat",
        ),
        (
            _decide(approved=True, patch_sha256=""),
            "approval was for a different patch",
            "octocat",
        ),
        (
            ApprovalDecision(
                approved=False, approver="", patch_sha256="", note="approval timed out"
            ),
            "approval timed out",
            None,
        ),
    ],
)
def test_everything_else_routes_rejected_with_a_reason(decision, reason, approver):
    event = route_approval(decision, DIGEST)
    assert event.actions.route == "rejected"
    assert event.actions.state_delta["failure"] == {
        "kind": "rejected",
        "reason": reason,
    }
    # report_failure reads the approver from state for its "Decision by" line.
    assert event.actions.state_delta["approver"] == approver


# --- the terminal approver -----------------------------------------------------------


def _request(tmp_path, patch: str = DIFF, **over) -> ApprovalRequest:
    path = tmp_path / "patch.diff"
    path.write_bytes(patch.encode())
    fields = {
        "run_id": "Run_7",
        "repo": REPO,
        "issue_number": 7,
        "issue_url": ISSUE_URL,
        "base_ref": "demo/md-001",
        "branch": "issue-to-pr/7-run-7",
        "pr_title": "[issue-to-pr] Parser loses rows",
        "pr_body": "BODY-START\n```\nzebra model text\n```\nBODY-END\n",
        "files": ["mini.py"],
        "insertions": 1,
        "deletions": 1,
        "patch_path": str(path),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "tests_passed": True,
        "test_exit_code": 0,
        "review_verdict": "approve",
        "review_must_fix": [],
        "cost_usd": 0.0123,
        "tool_calls": 9,
    }
    return ApprovalRequest(**{**fields, **over})


PROMPT = 'Type "approve" to open this pull request; anything else rejects: '


@pytest.mark.parametrize(
    ("answer", "approved"),
    [
        ("approve\n", True),
        ("  approve \n", True),
        ("approve", True),
        ("yes\n", False),
        ("Approve it\n", False),
        ("Approve\n", False),
        ("approved\n", False),
        ("\n", False),
        ("", False),
    ],
)
async def test_terminal_approver_requires_the_exact_word(tmp_path, answer, approved):
    out = io.StringIO()
    request = _request(tmp_path)
    approver = TerminalApprover("octocat", stdin=io.StringIO(answer), stdout=out)
    decision = await approver(request)
    assert decision == ApprovalDecision(
        approved=approved, approver="octocat", patch_sha256=request.patch_sha256
    )
    assert out.getvalue().endswith(PROMPT)


async def test_terminal_approver_shows_the_patch_before_model_text(tmp_path):
    out = io.StringIO()
    request = _request(tmp_path)
    approver = TerminalApprover("octocat", stdin=io.StringIO("no\n"), stdout=out)
    await approver(request)
    shown = out.getvalue()
    order = [
        f"Repository: {REPO}",
        ISSUE_URL,
        "demo/md-001",
        "issue-to-pr/7-run-7",
        "exit code 0",
        "Reviewer verdict: approve",
        "1 files, +1 -1",
        "+    return a + b",
        request.patch_path,
        "[issue-to-pr] Parser loses rows",
        "BODY-START",
        "zebra model text",
        "BODY-END",
        "$0.0123",
        "9 tool calls",
        PROMPT,
    ]
    positions = [shown.index(item) for item in order]
    assert positions == sorted(positions)
    assert "model-written" in shown and "code blocks" in shown
    assert "footer" in shown and "approver" in shown


async def test_terminal_approver_shows_the_first_400_lines_then_the_path(tmp_path):
    patch = "".join(f"+line {n}\n" for n in range(1, 451))
    out = io.StringIO()
    request = _request(tmp_path, patch=patch)
    await TerminalApprover("octocat", stdin=io.StringIO(""), stdout=out)(request)
    shown = out.getvalue()
    assert "+line 400\n" in shown and "+line 401\n" not in shown
    assert shown.index("+line 400") < shown.index(request.patch_path)


async def test_terminal_approver_binds_the_decision_to_the_patch_it_showed(tmp_path):
    request = _request(tmp_path)
    (tmp_path / "patch.diff").write_text(DIFF + "+sneaked in\n")
    out = io.StringIO()
    decision = await TerminalApprover(
        "octocat", stdin=io.StringIO("approve\n"), stdout=out
    )(request)
    assert "+sneaked in" in out.getvalue()
    assert decision.approved is True
    assert (
        decision.patch_sha256
        == hashlib.sha256((DIFF + "+sneaked in\n").encode()).hexdigest()
    )
    assert route_approval(decision, request.patch_sha256).actions.route == "rejected"


async def test_terminal_approver_shows_control_characters_visibly(tmp_path):
    patch = DIFF + "+x = 1\x1b[2K\r# hidden\n"
    out = io.StringIO()
    request = _request(tmp_path, patch=patch, pr_body="body \x1b]8;;evil\x07 text\n")
    await TerminalApprover("octocat", stdin=io.StringIO(""), stdout=out)(request)
    shown = out.getvalue()
    assert "\x1b" not in shown and "\r" not in shown and "\x07" not in shown
    assert "# hidden" in shown


def test_terminal_approver_needs_a_login():
    with pytest.raises(ValueError, match="login"):
        TerminalApprover("  ")


async def _shown(tmp_path, **over) -> str:
    out = io.StringIO()
    request = _request(tmp_path, **over)
    await TerminalApprover("octocat", stdin=io.StringIO(""), stdout=out)(request)
    return out.getvalue()


async def test_bidi_overrides_and_line_separators_are_shown_escaped(tmp_path):
    shown = await _shown(
        tmp_path,
        patch=DIFF + "+x = 'abc\u202edef'\u2028y = 1\n",
        pr_title="[issue-to-pr] Parser \u202eloses\u2028rows",
    )
    assert "\u202e" not in shown and "\u2028" not in shown
    title = next(line for line in shown.splitlines() if "Planned title" in line)
    assert title == "Planned title: [issue-to-pr] Parser \\u{202e}loses\\u{2028}rows"
    assert "+x = 'abc\\u{202e}def'\\u{2028}y = 1" in shown


# Default_Ignorable_Code_Point characters outside category Cf, Zl and Zp: each
# renders as nothing, or as a line break, in a terminal.
INVISIBLE = [
    "\u034f",
    "\u115f",
    "\u1160",
    "\u17b4",
    "\u17b5",
    "\u180b",
    "\u180c",
    "\u180d",
    "\u180e",
    "\u180f",
    "\u2029",
    "\u2065",
    "\u3164",
    "\ufe0f",
    "\uffa0",
    "\ufff0",
    "\U000e0100",
]


@pytest.mark.parametrize("char", INVISIBLE, ids=lambda c: f"U+{ord(c):04X}")
async def test_invisible_characters_are_shown_escaped(tmp_path, char):
    shown = await _shown(
        tmp_path, patch=DIFF + f"+name{char} = 1\n", pr_body=f"body{char}\n"
    )
    assert char not in shown
    assert f"+name\\u{{{ord(char):04x}}} = 1" in shown
    assert f"body\\u{{{ord(char):04x}}}" in shown


async def test_a_long_line_is_cut(tmp_path):
    shown = await _shown(tmp_path, patch=DIFF + "+" + "x" * 199_999 + "\n")
    line = next(line for line in shown.splitlines() if line.startswith("+xxx"))
    assert line == "+" + "x" * 499 + "[199500 more characters]"
    assert len(shown) < 10_000


async def test_a_long_line_of_escapes_is_cut_on_what_is_shown(tmp_path):
    shown = await _shown(tmp_path, pr_body="\u202e" * 200_000 + "\n")
    escape = "\\u{202e}"  # 8 characters: 62 fit in 500
    line = next(line for line in shown.splitlines() if line.startswith(escape))
    assert line == escape * 62 + f"[{200_000 - 62} more characters]"


async def test_a_long_file_list_is_cut(tmp_path):
    files = [f"pkg/module_{n:04d}.py" for n in range(5000)]
    shown = await _shown(tmp_path, files=files)
    assert "pkg/module_0099.py" in shown and "pkg/module_0100.py" not in shown
    assert "[4900 more files]" in shown
    assert "Diff: 5000 files" in shown


async def test_a_lone_surrogate_is_shown_escaped(tmp_path):
    # Written to a stdout with surrogateescape, U+DC9B would be the raw byte 0x9B,
    # the C1 control sequence introducer of an 8-bit terminal.
    surrogate = chr(0xDC9B)
    escaped = f"\\u{{{ord(surrogate):04x}}}"
    shown = await _shown(
        tmp_path,
        pr_title=f"[issue-to-pr] Parser{surrogate}loses rows",
        pr_body=f"body{surrogate}text\n",
        files=[f"pkg/mod{surrogate}.py"],
    )
    assert surrogate not in shown
    assert f"Planned title: [issue-to-pr] Parser{escaped}loses rows" in shown
    assert f"body{escaped}text" in shown and f"  pkg/mod{escaped}.py" in shown


async def test_the_decision_is_recapped_right_before_the_prompt(tmp_path):
    files = [f"pkg/module_{n:04d}.py" for n in range(150)]
    patch = "".join(f"+line {n}\n" for n in range(1, 451))
    shown = await _shown(tmp_path, patch=patch, files=files)
    shown_sha256 = hashlib.sha256(patch.encode()).hexdigest()
    recap = (
        "Deciding on:\n"
        f"  Repository: {REPO}\n"
        "  Planned branch: issue-to-pr/7-run-7\n"
        "  Files: 150\n"
        f"  Patch SHA-256: {shown_sha256}\n"
    )
    # Last thing before the prompt, after the run's spend: on screen even when the
    # patch and the body have scrolled past.
    assert shown.endswith(recap + PROMPT)
    assert shown.index("Run so far") < shown.rindex(recap)


async def test_the_recap_names_the_patch_the_decision_binds_to(tmp_path):
    request = _request(tmp_path)
    (tmp_path / "patch.diff").write_text(DIFF + "+sneaked in\n")
    out = io.StringIO()
    decision = await TerminalApprover(
        "octocat", stdin=io.StringIO("approve\n"), stdout=out
    )(request)
    recap = out.getvalue()[out.getvalue().rindex("Deciding on:") :]
    assert f"  Patch SHA-256: {decision.patch_sha256}\n" in recap
    assert request.patch_sha256 not in recap


class FakeTerminal(io.StringIO):
    """A stdin that says it is a terminal, on a made-up file descriptor."""

    def __init__(self, answer: str, out: io.StringIO) -> None:
        super().__init__(answer)
        self.out = out
        self.read_after: list[str] = []

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99

    def readline(self, *args) -> str:
        self.read_after.append(self.out.getvalue())
        return super().readline(*args)


class FakeTermios:
    TCIFLUSH = 0

    def __init__(self, out: io.StringIO) -> None:
        self.out = out
        self.flushes: list[tuple[int, int, str]] = []

    def tcflush(self, fd: int, queue: int) -> None:
        self.flushes.append((fd, queue, self.out.getvalue()))


async def test_type_ahead_is_discarded_on_a_terminal(tmp_path, monkeypatch):
    out = io.StringIO()
    termios = FakeTermios(out)
    monkeypatch.setattr(approval, "termios", termios)
    stdin = FakeTerminal("approve\n", out)
    request = _request(tmp_path)
    decision = await TerminalApprover("octocat", stdin=stdin, stdout=out)(request)
    assert decision.approved is True
    [(fd, queue, printed)] = termios.flushes
    assert (fd, queue) == (99, FakeTermios.TCIFLUSH)
    # Just before the prompt: everything else is shown, the prompt is not yet.
    assert "Run so far" in printed and PROMPT not in printed
    assert stdin.read_after[0].endswith(PROMPT)


async def test_nothing_is_flushed_when_stdin_is_not_a_terminal(tmp_path, monkeypatch):
    out = io.StringIO()
    termios = FakeTermios(out)
    monkeypatch.setattr(approval, "termios", termios)
    request = _request(tmp_path)
    approver = TerminalApprover("octocat", stdin=io.StringIO("approve\n"), stdout=out)
    assert (await approver(request)).approved is True
    assert termios.flushes == []


@pytest.mark.skipif(
    approval.termios is None or not hasattr(os, "openpty"), reason="needs a pty"
)
async def test_type_ahead_on_a_real_terminal_does_not_answer(tmp_path):
    master, slave = os.openpty()
    stdin = open(slave, encoding="utf-8")  # closed in finally
    try:
        os.write(master, b"approve\n")  # typed while the run was going
        await asyncio.sleep(0.05)
        out = io.StringIO()
        asked = asyncio.create_task(
            TerminalApprover("octocat", stdin=stdin, stdout=out)(_request(tmp_path))
        )
        for _ in range(500):
            if out.getvalue().endswith(PROMPT):
                break
            await asyncio.sleep(0.01)
        os.write(master, b"no\n")  # typed after the prompt
        decision = await asyncio.wait_for(asked, 5)
        assert decision.approved is False
    finally:
        stdin.close()
        os.close(master)


async def test_a_platform_without_termios_still_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(approval, "termios", None)
    out = io.StringIO()
    stdin = FakeTerminal("approve\n", out)
    request = _request(tmp_path)
    decision = await TerminalApprover("octocat", stdin=stdin, stdout=out)(request)
    assert decision.approved is True


@pytest.mark.parametrize("loose", ["true", "yes", 1, "1", "on"], ids=repr)
def test_a_decision_needs_a_real_boolean(loose):
    with pytest.raises(ValidationError):
        ApprovalDecision.model_validate(
            {"approved": loose, "approver": "octocat", "patch_sha256": DIGEST}
        )


# --- the driver: pause, ask, resume --------------------------------------------------

BASE_SHA = "a" * 40
TREE_SHA = "b" * 40
PULL_URL = f"https://github.com/{REPO}/pull/5"
PLAN = Plan(actionable=True, summary="fix add", files_to_inspect=["mini.py"])
PATCH = PatchResult(summary="fixed add")
APPROVE = Review(verdict="approve")
PASS = ExecResult(exit_code=0, stdout="2 passed", stderr="")


def _archive(path: Path, mini: str = "def add(a, b):\n    return a - b\n") -> Path:
    """A source tarball with one top-level directory, as GitHub serves it."""
    with tarfile.open(path, "w:gz") as tar:
        top = tarfile.TarInfo("acme-widgets-aaaa")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)
        for name, content in {
            "mini.py": mini,
            "tests/test_mini.py": "from mini import add\n",
        }.items():
            data = content.encode()
            info = tarfile.TarInfo(f"acme-widgets-aaaa/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


class FakeGitHub:
    """Stands in for GitHubClient at the method level: the reads live intake makes,
    the writes open_pr makes and the comment report_failure leaves. Any other
    method fails the test."""

    def __init__(self, archive: Path) -> None:
        self.archive = archive
        self.calls: list[tuple] = []
        self.errors: dict[str, Exception] = {}
        self.issue = Issue(
            number=7,
            title="Parser loses rows",
            body="the parser drops the last row",
            state="open",
            author="owner",
            labels=("agent-ok",),
            html_url=ISSUE_URL,
        )
        self.commits: list[dict] = []
        self.pulls: list[dict] = []
        self.comments: list[dict] = []

    # Nodes build their client with GitHubClient.from_token_file() and use it as an
    # async context manager.
    def from_token_file(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def __getattr__(self, name):
        raise AssertionError(f"unexpected GitHub call: {name}")

    async def _do(self, name: str, *args) -> None:
        self.calls.append((name, *args))
        if name in self.errors:
            raise self.errors[name]

    async def default_branch(self, repo):
        await self._do("default_branch", repo)
        return "main"

    async def get_issue(self, repo, number):
        await self._do("get_issue", repo, number)
        return self.issue

    async def last_label_actor(self, repo, number, label):
        await self._do("last_label_actor", repo, number, label)
        return "owner"

    async def open_pipeline_prs(self, repo, number):
        await self._do("open_pipeline_prs", repo, number)
        return []

    async def branch_head(self, repo, branch):
        await self._do("branch_head", repo, branch)
        return BASE_SHA, TREE_SHA

    async def download_tarball(self, repo, sha, dest, *, max_bytes=50_000_000):
        await self._do("download_tarball", repo, sha)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.archive.read_bytes())
        return dest

    async def create_commit(self, repo, *, parent_sha, base_tree_sha, changes, **kw):
        await self._do("create_commit", repo)
        self.commits.append(
            {"parent": parent_sha, "paths": [change.path for change in changes]}
        )
        return "c" * 40

    async def ensure_branch(self, repo, branch, sha):
        await self._do("ensure_branch", repo, branch, sha)

    async def open_pull_request(self, repo, *, head, base, title, body):
        await self._do("open_pull_request", repo, head)
        self.pulls.append({"head": head, "base": base, "title": title, "body": body})
        return PullRequest(
            number=5, html_url=PULL_URL, head=head, base=base, state="open", body=body
        )

    async def comment_once(self, repo, number, *, body, marker):
        await self._do("comment_once", repo, number)
        self.comments.append({"number": number, "body": body, "marker": marker})


@dataclass
class Live:
    github: FakeGitHub
    env: FakeEnvironment
    starts: list[str]
    runs: Path

    @property
    def log(self) -> list[dict]:
        path = self.runs / "r-live" / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    @property
    def stored(self) -> dict:
        return json.loads((self.runs / "r-live" / "record.json").read_text())


@pytest.fixture
def live(bench, monkeypatch):
    monkeypatch.setenv("LIVE_REPOS", REPO)
    monkeypatch.setenv("LIVE_ALLOWED_USERS", "owner")
    github = FakeGitHub(_archive(bench / "src.tar.gz"))
    monkeypatch.setattr(intake, "GitHubClient", github)
    monkeypatch.setattr(finish, "GitHubClient", github)

    def no_token():
        raise AssertionError("the token must not be read")

    monkeypatch.setattr(github_client, "load_token", no_token)
    env = FakeEnvironment(
        responses={
            DIFF_CMD: ExecResult(exit_code=0, stdout=DIFF, stderr=""),
            NUMSTAT_CMD: ExecResult(exit_code=0, stdout="1\t1\tmini.py\n", stderr=""),
            TEST_CMD: PASS,
        }
    )
    starts: list[str] = []

    async def start():
        starts.append(env.env_id)
        return env

    monkeypatch.setattr(intake, "start_environment", start)
    return Live(github=github, env=env, starts=starts, runs=bench / "runs")


def _scripted() -> RoleModels:
    return RoleModels(
        planner=FakeLlm([json_out(PLAN)]),
        coder=FakeLlm([json_out(PATCH)]),
        reviewer=FakeLlm([json_out(APPROVE)]),
    )


async def run_live(approver, models: RoleModels | None = None, **kwargs) -> RunRecord:
    return await run_pipeline(
        RunRequest(run_id="r-live", mode="live", repo=REPO, issue_number=7),
        workflow=build_workflow(models or _scripted(), live=True),
        approver=approver,
        **kwargs,
    )


class FakeApprover:
    """Records every request and answers with `decide(request)`."""

    def __init__(self, decide) -> None:
        self.decide = decide
        self.requests: list[ApprovalRequest] = []

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        self.requests.append(request)
        return self.decide(request)


def approve(request: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(
        approved=True, approver="octocat", patch_sha256=request.patch_sha256
    )


def reject(request: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(
        approved=False,
        approver="octocat",
        patch_sha256=request.patch_sha256,
        note="not now",
    )


async def hang(request: ApprovalRequest) -> ApprovalDecision:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


def _request_input_calls(lines: list[dict]) -> list[dict]:
    return [
        line
        for line in lines
        for part in (line.get("content") or {}).get("parts") or []
        if (part.get("function_call") or {}).get("name") == "adk_request_input"
    ]


def _position(lines: list[dict], needle: str) -> int:
    return next(i for i, line in enumerate(lines) if needle in json.dumps(line))


async def test_run_pauses_at_the_gate(live):
    approver = FakeApprover(approve)
    models = _scripted()
    record = await run_live(approver, models)
    assert len(approver.requests) == 1
    request = approver.requests[0]
    assert (request.run_id, request.repo, request.issue_number) == ("r-live", REPO, 7)
    assert request.patch_sha256 == DIGEST and request.tests_passed is True
    assert request.review_verdict == "approve" and request.files == ["mini.py"]
    calls = _request_input_calls(live.log)
    assert len(calls) == 1
    assert calls[0]["long_running_tool_ids"] == ["approve-r-live"]
    # The resume ran nothing before the gate again: one call per agent, one sandbox.
    assert (models.planner.calls, models.coder.calls, models.reviewer.calls) == (
        1,
        1,
        1,
    )
    assert live.starts == [live.env.env_id]
    assert record.outcome == "pr_opened"


async def test_sandbox_is_released_before_the_approver_is_asked(live):
    released: list[bool] = []

    def decide(request):
        released.append(live.env.closed)
        with pytest.raises(InfraError):
            registry.get(live.env.env_id)
        return approve(request)

    await run_live(FakeApprover(decide))
    assert released == [True]


async def test_approved_run_opens_one_pr(live):
    record = await run_live(FakeApprover(approve))
    assert (record.outcome, record.failure_kind) == ("pr_opened", "none")
    assert record.pr_url == PULL_URL and live.stored["pr_url"] == PULL_URL
    assert len(live.github.pulls) == 1
    assert live.github.commits == [{"parent": BASE_SHA, "paths": ["mini.py"]}]
    assert live.github.comments == []
    assert record.comment_posted is False


async def test_gate_request_matches_what_open_pr_publishes(live):
    approver = FakeApprover(approve)
    await run_live(approver)
    request = approver.requests[0]
    pull = live.github.pulls[0]
    assert (pull["head"], pull["base"], pull["title"]) == (
        request.branch,
        request.base_ref,
        request.pr_title,
    )
    assert request.issue_url == ISSUE_URL
    # The body is the same apart from the approver, which the published footer adds.
    assert ", approved by @octocat" in pull["body"]
    assert pull["body"].replace(", approved by @octocat", "") == request.pr_body
    assert (live.runs / "r-live" / "pr_body.md").read_text() == request.pr_body


async def test_rejected_run_opens_nothing_and_comments_once(live):
    record = await run_live(FakeApprover(reject))
    assert (record.outcome, record.failure_kind) == ("rejected", "none")
    assert record.reason == "not approved by octocat: not now"
    assert live.github.pulls == [] and live.github.commits == []
    assert len(live.github.comments) == 1
    comment = live.github.comments[0]
    assert comment["number"] == 7 and "Decision by @octocat." in comment["body"]
    assert record.comment_posted is True and record.pr_url is None
    assert live.stored["outcome"] == "rejected"


async def test_a_loose_approval_fails_closed(live):
    def loose(request):
        return {
            "approved": "true",
            "approver": "octocat",
            "patch_sha256": request.patch_sha256,
        }

    with pytest.raises(RunCrashed) as crashed:
        await run_live(FakeApprover(loose))
    assert isinstance(crashed.value.__cause__, ValidationError)
    assert (crashed.value.record.outcome, crashed.value.record.failure_kind) == (
        "failed",
        "infra",
    )
    assert live.stored["outcome"] == "failed"
    assert live.github.pulls == [] and live.github.commits == []


async def test_approval_for_a_different_patch_is_rejected(live):
    def other_patch(request):
        return ApprovalDecision(
            approved=True, approver="octocat", patch_sha256="0" * 64
        )

    record = await run_live(FakeApprover(other_patch))
    assert (record.outcome, record.failure_kind) == ("rejected", "none")
    assert record.reason == "approval was for a different patch"
    assert live.github.pulls == [] and len(live.github.comments) == 1


async def test_approval_timeout_rejects(live, monkeypatch):
    monkeypatch.setenv("APPROVAL_TIMEOUT_S", "0.05")
    record = await run_live(hang)
    assert (record.outcome, record.failure_kind) == ("rejected", "none")
    assert record.reason == "approval timed out"
    assert record.approval_wait_s >= 0.05
    assert live.github.pulls == [] and len(live.github.comments) == 1


async def test_timeout_comment_mentions_no_one(live, monkeypatch):
    monkeypatch.setenv("APPROVAL_TIMEOUT_S", "0.05")
    await run_live(hang)
    [comment] = live.github.comments
    assert "@" not in comment["body"]
    assert "Decision by" not in comment["body"]


async def test_no_approver_is_an_infra_failure_not_a_hang(live):
    record = await asyncio.wait_for(run_live(None), timeout=10)
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.reason == "approval needed but no approver"
    assert record.patch_path is None and record.approval_wait_s == 0
    assert live.env.closed
    # An infra failure after intake: the driver comments once (decision 10A).
    assert live.github.pulls == [] and len(live.github.comments) == 1
    assert live.stored["reason"] == "approval needed but no approver"


async def test_wait_is_excluded_from_duration(live):
    async def slow(request):
        await asyncio.sleep(0.6)
        return approve(request)

    started = time.monotonic()
    record = await run_live(slow)
    elapsed = time.monotonic() - started
    assert record.outcome == "pr_opened"
    assert record.approval_wait_s >= 0.6
    assert record.duration_s <= elapsed - 0.6 + 0.01
    assert record.duration_s + record.approval_wait_s <= elapsed + 0.02


async def test_resume_events_are_appended_to_the_same_log(live):
    seen: list[str] = []
    record = await run_live(
        FakeApprover(approve), on_event=lambda event: seen.append(event.id)
    )
    assert record.outcome == "pr_opened"
    lines = live.log
    assert [line["id"] for line in lines] == seen
    first_pass = _position(lines, "issue: Parser loses rows")
    gate = _position(lines, "adk_request_input")
    opened = _position(lines, f"pull request opened: {PULL_URL}")
    assert first_pass < gate < opened


@pytest.mark.parametrize(
    "error",
    [
        GitHubError(422, "POST", f"/repos/{REPO}/pulls", "Validation zebra-secret"),
        GitHubConfigError(401, "GET", "/user", "Bad credentials zebra-secret"),
    ],
)
async def test_github_refusal_at_delivery_is_infra(live, error):
    live.github.errors["open_pull_request"] = error
    record = await run_live(FakeApprover(approve))
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.reason == "GitHub refused the pull request"
    assert "zebra-secret" not in (live.runs / "r-live" / "record.json").read_text()


async def test_a_stalled_delivery_is_cut_off_by_the_resume_cap(live, monkeypatch):
    monkeypatch.setattr(driver, "RESUME_TIMEOUT_S", 0.2)

    async def stalled(repo, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(live.github, "open_pull_request", stalled, raising=False)
    record = await run_live(FakeApprover(approve))
    assert (record.outcome, record.failure_kind) == ("failed", "infra")
    assert record.reason == (
        "resumed run exceeded 0.2 s wall clock; the pull request may have been opened"
    )
    assert live.github.pulls == []


async def test_a_delivery_guard_is_still_a_crash_with_a_record(live):
    # The patch does not apply to the pinned source: open_pr's own guard, which
    # earlier nodes make unreachable, so reaching it is a harness bug.
    live.github.archive = _archive(
        live.runs.parent / "other.tar.gz", mini="def add(a, b):\n    return 0\n"
    )
    with pytest.raises(RunCrashed) as crashed:
        await run_live(FakeApprover(approve))
    assert isinstance(crashed.value.__cause__, RuntimeError)
    assert crashed.value.record.reason.startswith("unhandled RuntimeError")
    assert live.stored["failure_kind"] == "infra"
    assert live.github.pulls == []


@pytest.mark.parametrize("value", ["0", "-1", "abc", "", "nan", "inf"])
async def test_bad_approval_timeout_is_an_error_before_the_run(
    live, value, monkeypatch
):
    monkeypatch.setenv("APPROVAL_TIMEOUT_S", value)
    approver = FakeApprover(approve)
    with pytest.raises(ValueError, match="APPROVAL_TIMEOUT_S"):
        await run_live(approver)
    assert live.starts == [] and live.github.calls == [] and approver.requests == []
    assert not (live.runs / "r-live").exists()
