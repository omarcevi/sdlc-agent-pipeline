"""The human approval of a live run: what the approver is shown, what they answer,
and the terminal approver used in Week 2C.

The live graph's `human_gate` pauses the run with an `ApprovalRequest`; the driver
hands it to an `Approver` and resumes the run with the `ApprovalDecision`. The
decision carries the hash of the patch the approver was shown, so an approval binds
to that patch and nothing else (`route_approval` and `open_pr` check it).
"""

import asyncio
import hashlib
import importlib
import sys
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel, StrictBool

MAX_PATCH_LINES = 400
MAX_LINE_CHARS = 500  # per shown line, escapes included
MAX_FILES = 100  # file names listed in the diff stat
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
    An empty `approver` means no one decided (the approval timed out). `approved`
    must be a real boolean: a malformed answer such as "true" fails validation
    instead of approving."""

    approved: StrictBool
    approver: str
    patch_sha256: str
    note: str | None = None


Approver = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


def _load_termios() -> Any:
    try:
        return importlib.import_module("termios")
    except ImportError:  # not POSIX: type-ahead is not discarded
        return None


termios: Any = _load_termios()

# Shown escaped besides newline and tab: categories Cc (controls), Cf (format:
# bidi overrides, zero-width characters, tags), Zl and Zp (U+2028, U+2029), and Cs
# (lone surrogates: a stdout with surrogateescape writes U+DC80..U+DCFF as raw
# bytes, U+DC9B as 0x9B, the C1 control sequence introducer).
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs"})
# The Default_Ignorable_Code_Point characters outside category Cf (Unicode 15.1,
# DerivedCoreProperties.txt). They render as nothing, so text can hide in them.
_IGNORABLE_RANGES = (
    (0x034F, 0x034F),  # combining grapheme joiner
    (0x115F, 0x1160),  # Hangul choseong and jungseong fillers
    (0x17B4, 0x17B5),  # Khmer inherent vowels
    (0x180B, 0x180F),  # Mongolian free variation selectors (and the vowel separator)
    (0x2065, 0x2065),  # reserved
    (0x3164, 0x3164),  # Hangul filler
    (0xFE00, 0xFE0F),  # variation selectors
    (0xFFA0, 0xFFA0),  # halfwidth Hangul filler
    (0xFFF0, 0xFFF8),  # reserved
    (0xE0000, 0xE0FFF),  # tags, variation selectors supplement, reserved
)


def _escaped(char: str) -> bool:
    if char == "\t":
        return False
    if unicodedata.category(char) in _ESCAPED_CATEGORIES:
        return True
    code = ord(char)
    return any(low <= code <= high for low, high in _IGNORABLE_RANGES)


def _shown_line(line: str) -> str:
    """One line as the terminal shows it: every character that could move the
    cursor, erase or hide text shown as an escape, and the line cut once it reaches
    MAX_LINE_CHARS shown characters."""
    shown: list[str] = []
    width = 0
    for index, char in enumerate(line):
        piece = f"\\u{{{ord(char):04x}}}" if _escaped(char) else char
        if width + len(piece) > MAX_LINE_CHARS:
            shown.append(f"[{len(line) - index} more characters]")
            break
        shown.append(piece)
        width += len(piece)
    return "".join(shown)


def _visible(text: str) -> str:
    """`text` as the terminal shows it, line by line (see `_shown_line`), so a patch
    or a body cannot change what the approver sees or flood the terminal."""
    return "\n".join(_shown_line(line) for line in text.split("\n"))


def _discard_type_ahead(stream: TextIO) -> None:
    """Drop whatever was typed into the terminal while the run was going, so only
    an answer typed after the prompt counts. A stream that is not a terminal, or a
    platform without termios, is left alone."""
    if termios is None:
        return
    try:
        if not stream.isatty():
            return
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return
    termios.tcflush(fd, termios.TCIFLUSH)


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
        for path in request.files[:MAX_FILES]:
            self._show(f"  {path}")
        if len(request.files) > MAX_FILES:
            self._show(f"  [{len(request.files) - MAX_FILES} more files]")
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
        self._show()
        # Repeated last, so the decision's context is on screen even when the
        # patch and the body have scrolled past.
        self._show("Deciding on:")
        self._show(f"  Repository: {request.repo}")
        self._show(f"  Planned branch: {request.branch}")
        self._show(f"  Files: {len(request.files)}")
        self._show(f"  Patch SHA-256: {shown_sha256}")
        self._stdout.flush()
        _discard_type_ahead(self._stdin)
        print(_visible(PROMPT), end="", file=self._stdout, flush=True)
        answer = await asyncio.to_thread(self._stdin.readline)
        return ApprovalDecision(
            approved=answer.strip() == APPROVE_WORD,
            approver=self.login,
            patch_sha256=shown_sha256,
        )
