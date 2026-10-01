"""The live-run CLI: its refusals, the per-issue lock, what it prints, and how it
ends when the approver never answers or presses Ctrl-C. Whole runs go through the
real driver with a scripted model, a fake sandbox and an in-memory GitHub."""

import io
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from app import live as live_cli
from app import pr_text
from app.approval import PROMPT
from app.models import RoleModels
from app.schemas import RunRecord
from tests.fakes import FakeEnvironment, FakeLlm
from tests.unit.delivery_fakes import REPO
from tests.unit.live_rest import (
    ISSUE,
    PULL_URL,
    LiveGitHubRest,
    scripted,
    serve,
    source_archive,
    use_sandbox,
)

ARGS = ["--repo", REPO, "--issue", str(ISSUE), "--approver", "owner"]
LOCK = Path(".locks") / "demo__widgets__7.lock"


class Terminal(io.StringIO):
    """A stand-in terminal holding what the approver types."""

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise io.UnsupportedOperation("no descriptor")


class OpenTerminal:
    """A terminal nobody types into: readline blocks until `type` or `close`."""

    def __init__(self) -> None:
        read_fd, self._write_fd = os.pipe()
        self._reader = os.fdopen(read_fd, "r")

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise io.UnsupportedOperation("no descriptor")

    def readline(self) -> str:
        return self._reader.readline()

    def type(self, text: str) -> None:
        os.write(self._write_fd, text.encode())

    def close(self) -> None:
        os.close(self._write_fd)
        self._reader.close()


class Screen(io.StringIO):
    """Collects output; calls `on_text()` once, when a write contains `text`."""

    def __init__(
        self, text: str | None = None, on_text: Callable[[], None] | None = None
    ) -> None:
        super().__init__()
        self._text, self._on_text = text, on_text

    def write(self, s: str) -> int:
        written = super().write(s)
        if self._text is not None and self._on_text is not None and self._text in s:
            self._text = None
            self._on_text()
        return written


@dataclass
class Cli:
    server: LiveGitHubRest
    env: FakeEnvironment
    runs: Path

    def record(self, run_id: str) -> dict:
        return json.loads((self.runs / run_id / "record.json").read_text())


@pytest.fixture(autouse=True)
def _no_dotenv_no_tracing(monkeypatch):
    """No test here loads the developer's .env (real repositories, logins, cloud
    project) or turns on Cloud Trace."""
    monkeypatch.setattr(live_cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(live_cli, "enable_cloud_trace", lambda: False)


@pytest.fixture
def cli(bench, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_REPOS", REPO)
    monkeypatch.setenv("LIVE_ALLOWED_USERS", "owner")
    monkeypatch.setattr(RoleModels, "from_env", classmethod(lambda cls: scripted()))
    server = LiveGitHubRest(source_archive(tmp_path))
    serve(monkeypatch, server)
    return Cli(server=server, env=use_sandbox(monkeypatch), runs=bench / "runs")


def forbid_runs(monkeypatch) -> None:
    async def forbidden(*args, **kwargs):
        raise AssertionError("the CLI started a run it should have refused")

    monkeypatch.setattr(live_cli, "run_pipeline", forbidden)


def one_error_line(capsys) -> str:
    err = capsys.readouterr().err
    lines = err.splitlines()
    assert len(lines) == 1 and lines[0].startswith("error: "), err
    return lines[0]


# --- refusals ---------------------------------------------------------------------


def test_refuses_without_a_terminal(cli, monkeypatch, capsys):
    forbid_runs(monkeypatch)
    out = io.StringIO()
    assert live_cli.main(ARGS, stdin=io.StringIO("approve\n"), stdout=out) == 2
    assert "terminal" in one_error_line(capsys)
    assert not (cli.runs / LOCK).exists() and cli.server.seen == []


def test_refuses_an_approver_not_allowed(cli, monkeypatch, capsys):
    forbid_runs(monkeypatch)
    args = ["--repo", REPO, "--issue", "7", "--approver", "stranger"]
    assert live_cli.main(args, stdin=Terminal("approve\n"), stdout=io.StringIO()) == 2
    assert "LIVE_ALLOWED_USERS" in one_error_line(capsys)
    assert not (cli.runs / LOCK).exists() and cli.server.seen == []


def test_refuses_a_repo_not_allowed(cli, monkeypatch, capsys):
    forbid_runs(monkeypatch)
    args = ["--repo", "other/repo", "--issue", "7", "--approver", "owner"]
    assert live_cli.main(args, stdin=Terminal("approve\n"), stdout=io.StringIO()) == 2
    assert "LIVE_REPOS" in one_error_line(capsys)
    assert not (cli.runs / ".locks").exists() and cli.server.seen == []


@pytest.mark.parametrize(
    ("extra", "env", "needle"),
    [
        (["--run-id", "../escape"], {}, "--run-id"),
        (["--run-id", "a" * 65], {}, "--run-id"),
        ([], {"APPROVAL_TIMEOUT_S": "0"}, "APPROVAL_TIMEOUT_S"),
        ([], {"RUN_TIMEOUT_S": "-1"}, "RUN_TIMEOUT_S"),
    ],
)
def test_refuses_bad_settings_before_taking_the_lock(
    cli, monkeypatch, capsys, extra, env, needle
):
    forbid_runs(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    code = live_cli.main(
        [*ARGS, *extra], stdin=Terminal("approve\n"), stdout=io.StringIO()
    )
    assert code == 2
    assert needle in one_error_line(capsys)
    assert not (cli.runs / LOCK).exists()


def test_refuses_a_run_id_that_was_used_before(cli, monkeypatch, capsys):
    forbid_runs(monkeypatch)
    earlier = cli.runs / "taken"
    earlier.mkdir(parents=True)
    (earlier / "record.json").write_text("the earlier run's record")
    code = live_cli.main(
        [*ARGS, "--run-id", "taken"], stdin=Terminal("approve\n"), stdout=Screen()
    )
    assert code == 2
    assert "already exists" in one_error_line(capsys)
    assert (earlier / "record.json").read_text() == "the earlier run's record"
    assert sorted(p.name for p in earlier.iterdir()) == ["record.json"]
    assert not (cli.runs / LOCK).exists() and cli.server.seen == []


def test_the_default_run_id_keeps_its_stamp_in_the_branch_and_its_length():
    run_id = live_cli.default_run_id("owner/issue-to-pr-demo-widgets", 7)
    assert re.fullmatch(r"live-\d{8}T\d{6}Z-issue-to-pr-demo-widgets-7", run_id)
    stamp = run_id.split("-")[1].lower()
    # The branch keeps 40 characters of the run id: two runs a second apart differ.
    assert stamp in pr_text.branch_name(7, run_id)
    long = live_cli.default_run_id("owner/" + "n" * 100, 9_999_999)
    assert len(long) <= live_cli.MAX_RUN_ID and live_cli._RUN_ID.fullmatch(long)


def test_the_summary_escapes_what_it_prints(tmp_path):
    record = RunRecord(
        task_id="t",
        run_id="r",
        outcome="pr_opened",
        failure_kind="none",
        reason="a\x1b[2Jb",
        pr_url="https://github.com/o/r/pull/5\x1b]8;;evil\x07",
    )
    shown = "\n".join(live_cli._summary(record, tmp_path))
    assert "\x1b" not in shown and "\x07" not in shown
    assert "pull request: https://github.com/o/r/pull/5\\u{001b}" in shown


# --- the lock -----------------------------------------------------------------------


def _wait_for(condition, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)


def test_a_second_cli_run_on_the_same_issue_is_refused_while_the_lock_is_held(
    cli, capsys
):
    terminal, first_out, result = OpenTerminal(), Screen(), {}

    def first_run() -> None:
        result["code"] = live_cli.main(
            [*ARGS, "--run-id", "first"], stdin=terminal, stdout=first_out
        )

    first = threading.Thread(target=first_run, daemon=True)
    first.start()
    try:
        _wait_for(lambda: PROMPT in first_out.getvalue())  # waiting for its approver
        assert (cli.runs / LOCK).read_text().strip() == str(os.getpid())
        second = live_cli.main(
            [*ARGS, "--run-id", "second"], stdin=Terminal("approve\n"), stdout=Screen()
        )
        assert second == 2
        # The first run's thread may print a library warning to the same stderr.
        errors = [
            line
            for line in capsys.readouterr().err.splitlines()
            if line.startswith("error: ")
        ]
        assert len(errors) == 1 and "holds the lock" in errors[0]
        assert not (cli.runs / "second").exists()
        terminal.type("no\n")
        first.join(timeout=15)
        assert not first.is_alive() and result["code"] == 0
    finally:
        terminal.close()
    assert cli.record("first")["outcome"] == "rejected"
    assert cli.server.count("GET", f"/issues/{ISSUE}") == 1  # one run fetched it
    assert not (cli.runs / LOCK).exists()


def test_stale_lock_is_replaced(cli, monkeypatch):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    lock = cli.runs / LOCK
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{gone.pid}\n")
    held_by: list[str] = []
    real_run = live_cli.run_pipeline

    async def spy(*args, **kwargs):
        held_by.append(lock.read_text().strip())
        return await real_run(*args, **kwargs)

    monkeypatch.setattr(live_cli, "run_pipeline", spy)
    out = Screen()
    assert live_cli.main(ARGS, stdin=Terminal("approve\n"), stdout=out) == 0
    assert held_by == [str(os.getpid())]
    assert PULL_URL in out.getvalue()
    assert not lock.exists()


# --- a whole run --------------------------------------------------------------------


def test_prints_the_pull_request_url(cli):
    out = Screen()
    args = [*ARGS, "--run-id", "cli-1"]
    assert live_cli.main(args, stdin=Terminal("approve\n"), stdout=out) == 0
    shown = out.getvalue()
    assert "pipeline · issue: Parser loses rows" in shown  # live progress
    assert PROMPT in shown
    summary = shown[shown.index(PROMPT) + len(PROMPT) :]
    assert "outcome: pr_opened" in summary
    assert f"pull request: {PULL_URL}" in summary
    assert re.search(r"cost: \$\d+\.\d{4}", summary)
    assert f"run directory: {cli.runs / 'cli-1'}" in summary
    assert cli.record("cli-1")["pr_url"] == PULL_URL
    assert len(cli.server.pulls) == 1
    assert not (cli.runs / LOCK).exists()


def test_quiet_prints_no_progress_and_the_default_run_id_names_the_issue(cli):
    out = Screen()
    assert live_cli.main([*ARGS, "--quiet"], stdin=Terminal("no\n"), stdout=out) == 0
    shown = out.getvalue()
    assert "pipeline ·" not in shown and "outcome: rejected" in shown
    [run_dir] = [p for p in cli.runs.iterdir() if p.name != ".locks"]
    assert re.fullmatch(r"live-\d{8}T\d{6}Z-widgets-7", run_dir.name)
    assert cli.server.posted_comments() == 1


def test_returns_promptly_after_an_approval_timeout_with_stdin_left_open(
    cli, monkeypatch
):
    monkeypatch.setenv("APPROVAL_TIMEOUT_S", "0.3")
    terminal, out, result = OpenTerminal(), Screen(), {}

    def run() -> None:
        result["code"] = live_cli.main(
            [*ARGS, "--run-id", "timeout-1"], stdin=terminal, stdout=out
        )

    started = time.monotonic()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        thread.join(timeout=15)
        assert not thread.is_alive(), "the CLI waited for the terminal"
    finally:
        terminal.close()
    assert result["code"] == 0
    assert time.monotonic() - started < 15
    assert "reason: approval timed out" in out.getvalue()
    assert cli.record("timeout-1")["reason"] == "approval timed out"
    assert cli.server.posted_comments() == 1


def test_ctrl_c_at_the_prompt_rejects_the_run(cli):
    terminal = OpenTerminal()
    out = Screen(PROMPT, lambda: signal.raise_signal(signal.SIGINT))
    try:
        code = live_cli.main([*ARGS, "--run-id", "ctrl-c"], stdin=terminal, stdout=out)
    finally:
        terminal.close()
    assert code == 0
    assert "reason: approval cancelled" in out.getvalue()
    assert cli.record("ctrl-c")["outcome"] == "rejected"
    assert cli.server.posted_comments() == 1 and cli.server.pulls == []
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert not (cli.runs / LOCK).exists()


def test_ctrl_c_before_the_prompt_stops_the_run_with_a_record(cli, capsys):
    out = Screen("pipeline · issue:", lambda: signal.raise_signal(signal.SIGINT))
    code = live_cli.main(
        [*ARGS, "--run-id", "stopped"], stdin=Terminal("approve\n"), stdout=out
    )
    assert code == 130
    assert "interrupted" in one_error_line(capsys)
    assert PROMPT not in out.getvalue() and cli.server.pulls == []
    assert cli.record("stopped")["reason"] == "run cancelled"
    assert "reason: run cancelled" in out.getvalue()
    assert cli.server.posted_comments() == 0
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert not (cli.runs / LOCK).exists()


def _ctrl_c_on(cli: Cli, *requests: tuple[str, str]) -> list[str]:
    """Press Ctrl-C once on each of `requests` ((method, path suffix), in order)
    the run sends to GitHub; that request then waits a moment, so the press is
    handled before the run goes on. Returns the paths it pressed on."""
    pressed: list[str] = []
    waiting = list(requests)

    def hook(method: str, path: str) -> float | None:
        if waiting and method == waiting[0][0] and path.endswith(waiting[0][1]):
            waiting.pop(0)
            pressed.append(path)
            signal.raise_signal(signal.SIGINT)
            return 0.2
        return None

    cli.server.on_request = hook
    return pressed


def test_the_first_ctrl_c_while_delivering_only_warns(cli):
    pressed = _ctrl_c_on(cli, ("POST", "/git/blobs"))
    out = Screen()
    code = live_cli.main(
        [*ARGS, "--run-id", "warned"], stdin=Terminal("approve\n"), stdout=out
    )
    assert pressed and code == 0
    assert live_cli.DELIVERING in out.getvalue()
    assert cli.record("warned")["outcome"] == "pr_opened"
    assert len(cli.server.pulls) == 1


def test_a_second_ctrl_c_while_delivering_stops_the_run_with_a_record(cli, capsys):
    pressed = _ctrl_c_on(cli, ("POST", "/git/blobs"), ("POST", "/git/trees"))
    out = Screen()
    code = live_cli.main(
        [*ARGS, "--run-id", "aborted"], stdin=Terminal("approve\n"), stdout=out
    )
    assert len(pressed) == 2 and code == 130
    assert live_cli.DELIVERING in out.getvalue()
    assert "interrupted" in one_error_line(capsys)
    reason = "run cancelled; the pull request may have been opened"
    assert cli.record("aborted")["reason"] == reason
    assert f"reason: {reason}" in out.getvalue()
    assert cli.server.posted_comments() == 0
    assert not (cli.runs / LOCK).exists()


def test_ctrl_c_at_the_prompt_then_while_delivering_still_delivers(cli):
    terminal = OpenTerminal()
    out = Screen(PROMPT, lambda: signal.raise_signal(signal.SIGINT))
    pressed = _ctrl_c_on(cli, ("GET", "/issues/7/comments"))  # report_failure's
    try:
        code = live_cli.main([*ARGS, "--run-id", "twice"], stdin=terminal, stdout=out)
    finally:
        terminal.close()
    assert pressed and code == 0
    assert live_cli.DELIVERING in out.getvalue()
    assert cli.record("twice")["reason"] == "approval cancelled"
    assert cli.server.posted_comments() == 1


def test_a_crash_exits_1_with_the_record(cli, monkeypatch, capsys):
    broken = RoleModels(planner=FakeLlm([]), coder=FakeLlm([]), reviewer=FakeLlm([]))
    monkeypatch.setattr(RoleModels, "from_env", classmethod(lambda cls: broken))
    out = Screen()
    code = live_cli.main(
        [*ARGS, "--run-id", "crash-1", "--quiet"], stdin=Terminal(""), stdout=out
    )
    assert code == 1
    assert "crashed" in capsys.readouterr().err
    assert "run directory:" in out.getvalue()
    assert cli.record("crash-1")["reason"].startswith("unhandled AssertionError")
    assert not (cli.runs / LOCK).exists()
