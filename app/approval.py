"""The human approval of a live run: what the approver is shown, what they answer,
and the terminal approver used in Week 2C.

The live graph's `human_gate` pauses the run with an `ApprovalRequest`; the driver
hands it to an `Approver` and resumes the run with the `ApprovalDecision`. The
decision carries the hash of the patch the approver was shown, so an approval binds
to that patch and nothing else (`route_approval` and `open_pr` check it).
"""

import asyncio
import hashlib
import sys
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel

MAX_PATCH_LINES = 400
APPROVE_WORD = "approve"
PROMPT = f'Type "{APPROVE_WORD}" to open this pull request; anything else rejects: '


class ApprovalRequest(BaseModel):
    """What the approver sees: the pull request exactly as it would be published
    (branch, title, body without the approver footer), the real test result and the
    reviewer's verdict, the patch on disk and its hash, and the run's spend so far."""

    run_id: str
    repo: str
    issue_number: int
    issue_url: str
    base_ref: str
    branch: str
    pr_title: str
    pr_body: str
    files: list[str]
    insertions: int
    deletions: int
    patch_path: str
    patch_sha256: str
    tests_passed: bool
    test_exit_code: int
    review_verdict: str
    review_must_fix: list[str]
    cost_usd: float
    tool_calls: int


class ApprovalDecision(BaseModel):
    """The approver's answer. `patch_sha256` is the hash of the patch they were shown.
    An empty `approver` means no one decided (the approval timed out)."""

    approved: bool
    approver: str
    patch_sha256: str
    note: str | None = None


Approver = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


def _visible(text: str) -> str:
    """`text` with every control or format character except newline and tab shown
    as an escape, so a patch or a body cannot move the cursor, erase lines or
    otherwise change what the terminal shows the approver."""
    return "".join(
        char
        if char in "\n\t" or unicodedata.category(char) not in ("Cc", "Cf")
        else f"\\u{{{ord(char):04x}}}"
        for char in text
    )


class TerminalApprover:
    """Asks the person at the terminal. Only the exact word `approve` (surrounding
    whitespace ignored) approves; anything else, an empty line or end of input
    included, rejects."""

    def __init__(
        self,
        login: str,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
    ) -> None:
        if not login.strip():
            raise ValueError("TerminalApprover needs the approver's login")
        self.login = login.strip()
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout

    def _show(self, text: str = "") -> None:
        print(_visible(text), file=self._stdout)

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        patch = Path(request.patch_path).read_bytes()
        shown_sha256 = hashlib.sha256(patch).hexdigest()
        lines = patch.decode("utf-8", errors="replace").split("\n")
        if lines and lines[-1] == "":
            lines.pop()

        self._show("Pull request awaiting approval")
        self._show(f"Repository: {request.repo}")
        self._show(f"Issue: #{request.issue_number} {request.issue_url}")
        self._show(f"Base branch: {request.base_ref}")
        self._show(f"Planned branch: {request.branch}")
        self._show()
        tests = "passed" if request.tests_passed else "failed"
        self._show(f"Tests: {tests}, exit code {request.test_exit_code}")
        self._show(f"Reviewer verdict: {request.review_verdict}")
        self._show()
        self._show(
            f"Diff: {len(request.files)} files, "
            f"+{request.insertions} -{request.deletions}"
        )
        for path in request.files:
            self._show(f"  {path}")
        self._show(f"Patch SHA-256: {shown_sha256}")
        if len(lines) > MAX_PATCH_LINES:
            self._show(f"--- patch: first {MAX_PATCH_LINES} of {len(lines)} lines ---")
        else:
            self._show(f"--- patch: {len(lines)} lines ---")
        for line in lines[:MAX_PATCH_LINES]:
            self._show(line)
        self._show(f"--- full patch: {request.patch_path} ---")
        self._show()
        self._show(f"Planned title: {request.pr_title}")
        self._show(
            "Planned body (model-written parts are inside code blocks, "
            "where GitHub renders no links or mentions):"
        )
        self._show(request.pr_body.rstrip("\n"))
        self._show("The published body's footer also names you as the approver.")
        self._show()
        self._show(
            f"Run so far: ${request.cost_usd:.4f}, {request.tool_calls} tool calls"
        )
        print(_visible(PROMPT), end="", file=self._stdout, flush=True)
        answer = await asyncio.to_thread(self._stdin.readline)
        return ApprovalDecision(
            approved=answer.strip() == APPROVE_WORD,
            approver=self.login,
            patch_sha256=shown_sha256,
        )
