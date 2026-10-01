"""Run the live pipeline on one GitHub issue, approved by a person at this terminal.

uv run python -m app.live --repo OWNER/NAME --issue N --approver LOGIN \
    [--base BRANCH] [--run-id ID] [--quiet]

A run spends model credits and, once approved, opens a real pull request. The CLI
never reads the GitHub token; only the pipeline's own nodes do.

Exit status: 0 when a run finished, whatever its outcome; 2 when the CLI refuses to
start (stdin is not a terminal, an approver or repository that is not allowed, a bad
setting, or another run holding the issue's lock); 1 when the run crashed (its
record is still written); 130 when Ctrl-C stopped the run before the prompt.
"""

import argparse
import asyncio
import os
import queue
import re
import signal
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

from dotenv import load_dotenv
from google.adk.events import Event
from pydantic import ValidationError

from app.approval import ApprovalDecision, ApprovalRequest, Approver, TerminalApprover
from app.driver import RunCrashed, approval_timeout_s, run_pipeline, run_timeout_s
from app.live_config import allowed_users, live_repos
from app.models import RoleModels
from app.nodes.finish import runs_dir
from app.pipeline import build_workflow
from app.schemas import RunRecord, RunRequest
from app.tracing import enable_cloud_trace, flush_traces, trace_explorer_url
from bench.progress import format_event

_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


class LockHeld(Exception):
    """Another live run holds the issue's lock."""


def lock_path(repo: str, issue_number: int) -> Path:
    """`runs/.locks/<owner>__<name>__<n>.lock`, lowercased: repositories are
    matched case-insensitively, so one issue has one lock however it is spelled."""
    owner, name = repo.lower().split("/", 1)
    return runs_dir() / ".locks" / f"{owner}__{name}__{issue_number}.lock"


def _lock_pid(path: Path) -> int | None:
    """The PID in the lock file; None when the file holds no PID. Raises
    FileNotFoundError when there is no lock file."""
    text = path.read_text().strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # it exists, under another user
        return True
    return True


def acquire_lock(path: Path) -> None:
    """Create the lock file (O_CREAT | O_EXCL) holding this process's PID. A lock
    whose process is gone is replaced; a lock held by a live process, or one that
    holds no PID, raises LockHeld."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                pid = _lock_pid(path)
            except FileNotFoundError:
                continue  # released meanwhile: try again
            if pid is None or _alive(pid):
                raise LockHeld(str(path)) from None
            try:
                if _lock_pid(path) == pid:  # still the stale lock just read
                    path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w") as handle:
            handle.write(f"{os.getpid()}\n")
        return
    raise LockHeld(str(path))


def release_lock(path: Path) -> None:
    """Remove the lock if this process holds it."""
    try:
        if _lock_pid(path) == os.getpid():
            path.unlink()
    except FileNotFoundError:
        pass


class TerminalLines:
    """The approver's answer, read from the terminal by a daemon thread.

    TerminalApprover reads in a worker thread of the event loop. A read that is
    still pending when the run ends (the approval timed out, or Ctrl-C) would keep
    that worker blocked, and the interpreter waits for it at exit. Here the worker
    waits on a queue that `release()` answers with end of input, and only the
    daemon thread waits on the terminal. The thread starts at the first read, after
    the prompt is shown, so nothing typed during the run is read early.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lines: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()

    def isatty(self) -> bool:
        return self._stream.isatty()

    def fileno(self) -> int:
        return self._stream.fileno()

    def readline(self) -> str:
        with self._lock:
            if self._reader is None:
                self._reader = threading.Thread(
                    target=self._read, name="approval-input", daemon=True
                )
                self._reader.start()
        return self._lines.get()

    def _read(self) -> None:
        while True:
            try:
                line = self._stream.readline()
            except (OSError, ValueError):  # closed underneath us
                line = ""
            self._lines.put(line)
            if not line:
                return

    def release(self) -> None:
        """Answer a pending read with end of input."""
        self._lines.put("")


class InterruptiblePrompt:
    """Wraps the approver so Ctrl-C at the prompt cancels the prompt only. The
    driver then records a rejection ("approval cancelled"), and the issue gets its
    one comment; Ctrl-C at any other time stops the run."""

    def __init__(self, approver: Approver) -> None:
        self._approver = approver
        self._prompt: asyncio.Future[ApprovalDecision] | None = None

    async def __call__(self, request: ApprovalRequest) -> ApprovalDecision:
        self._prompt = asyncio.ensure_future(self._approver(request))
        try:
            return await self._prompt
        finally:
            self._prompt = None

    def interrupt(self) -> bool:
        """Cancel a pending prompt. False when no prompt is pending."""
        if self._prompt is None or self._prompt.done():
            return False
        self._prompt.cancel()
        return True


def _shown(text: str) -> str:
    """`text` on one line, with every character that is not printable escaped:
    reasons and progress can hold issue or model text."""
    return "".join(
        char if char.isprintable() else f"\\u{{{ord(char):04x}}}" for char in text
    )


def default_run_id(repo: str, issue_number: int) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"live-{repo.split('/', 1)[1]}-{issue_number}-{stamp}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.live",
        description="Run the pipeline on one GitHub issue; a person approves the "
        "pull request at this terminal.",
    )
    parser.add_argument("--repo", required=True, help="OWNER/NAME, in LIVE_REPOS")
    parser.add_argument("--issue", required=True, type=int, help="issue number")
    parser.add_argument(
        "--approver", required=True, help="your GitHub login, in LIVE_ALLOWED_USERS"
    )
    parser.add_argument("--base", help="base branch (default: the default branch)")
    parser.add_argument("--run-id", help="default: live-<name>-<issue>-<UTC stamp>")
    parser.add_argument(
        "--quiet", action="store_true", help="do not print live progress"
    )
    return parser


def _fail(message: str) -> int:
    print(f"error: {_shown(message)}", file=sys.stderr)
    return 2


def _is_terminal(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def _progress(label: str, stdout: TextIO) -> Callable[[Event], None]:
    def on_event(event: Event) -> None:
        for line in format_event(label, event):
            print(_shown(line), file=stdout, flush=True)

    return on_event


def _summary(record: RunRecord, run_dir: Path) -> list[str]:
    outcome = record.outcome
    if record.failure_kind != "none":
        outcome += f" ({record.failure_kind})"
    return [
        f"outcome: {outcome}",
        f"reason: {_shown(record.reason) or '-'}",
        f"pull request: {record.pr_url or '-'}",
        f"cost: ${record.cost_usd:.4f}, {record.tool_calls} tool calls",
        f"run directory: {run_dir}",
    ]


async def _drive(
    request: RunRequest,
    prompt: InterruptiblePrompt,
    lines: TerminalLines,
    on_event: Callable[[Event], None] | None,
) -> RunRecord:
    """The run, with Ctrl-C routed to the prompt while one is pending. A second
    Ctrl-C after the run was told to stop raises KeyboardInterrupt at once."""
    loop = asyncio.get_running_loop()
    run_task = asyncio.current_task()
    stops = 0

    def on_sigint() -> None:
        nonlocal stops
        if prompt.interrupt():
            return
        stops += 1
        if stops > 1:
            raise KeyboardInterrupt
        if run_task is not None:
            run_task.cancel()

    handled = False
    if threading.current_thread() is threading.main_thread():
        try:
            loop.add_signal_handler(signal.SIGINT, on_sigint)
            handled = True
        except (NotImplementedError, RuntimeError, ValueError):
            pass  # no loop signal handlers here: Ctrl-C stops the run
    try:
        return await run_pipeline(
            request,
            workflow=build_workflow(RoleModels.from_env(), live=True),
            on_event=on_event,
            approver=prompt,
        )
    finally:
        if handled:
            loop.remove_signal_handler(signal.SIGINT)
        lines.release()  # the loop's worker must not wait for the terminal


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    load_dotenv()  # LIVE_REPOS, LIVE_ALLOWED_USERS and the models may live in .env
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if not _is_terminal(stdin):
        return _fail("stdin is not a terminal: a person must type the approval")
    login = args.approver.strip()
    if not login or login.lower() not in allowed_users():
        return _fail(f"approver {login!r} is not in LIVE_ALLOWED_USERS")
    if args.repo.lower() not in live_repos():
        return _fail(f"repository {args.repo!r} is not in LIVE_REPOS")
    if args.run_id is not None and not _RUN_ID.fullmatch(args.run_id):
        return _fail("--run-id may hold only letters, digits, '.', '_' and '-'")
    try:
        request = RunRequest(
            run_id=args.run_id or default_run_id(args.repo, args.issue),
            mode="live",
            repo=args.repo,
            issue_number=args.issue,
            base_ref=args.base,
        )
    except ValidationError as exc:
        return _fail(str(exc.errors()[0]["msg"]))
    try:
        run_timeout_s()
        approval_timeout_s()
    except ValueError as exc:
        return _fail(str(exc))

    lock = lock_path(args.repo, args.issue)
    try:
        acquire_lock(lock)
    except LockHeld:
        return _fail(
            f"another live run holds the lock for {request.subject_id} ({lock})"
        )
    run_dir = runs_dir() / request.run_id
    try:
        tracing_on = enable_cloud_trace()
        lines = TerminalLines(stdin)
        prompt = InterruptiblePrompt(
            TerminalApprover(login, stdin=cast(TextIO, lines), stdout=stdout)
        )
        on_event = None if args.quiet else _progress(request.subject_id, stdout)
        try:
            record = asyncio.run(_drive(request, prompt, lines, on_event))
        except RunCrashed as crashed:
            print("\n".join(_summary(crashed.record, run_dir)), file=stdout)
            print(
                f"error: the run crashed: {_shown(crashed.record.reason)}",
                file=sys.stderr,
            )
            return 1
        except (KeyboardInterrupt, asyncio.CancelledError):
            print(file=stdout)
            print("error: interrupted; the run was stopped", file=sys.stderr)
            return 130
        finally:
            if tracing_on:
                flush_traces()
    finally:
        release_lock(lock)
    print(file=stdout)
    print("\n".join(_summary(record, run_dir)), file=stdout, flush=True)
    if tracing_on:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        print(f"traces: {trace_explorer_url(project)}", file=stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
