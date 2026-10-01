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
from app.approval import PROMPT
from app.models import RoleModels
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


@pytest.fixture
def cli(bench, tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_REPOS", REPO)
    monkeypatch.setenv("LIVE_ALLOWED_USERS", "owner")
    monkeypatch.setattr(live_cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(live_cli, "enable_cloud_trace", lambda: False)
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
    assert re.fullmatch(r"live-widgets-7-\d{8}T\d{6}Z", run_dir.name)
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


def test_ctrl_c_before_the_prompt_stops_the_run(cli, capsys):
    out = Screen("pipeline · issue:", lambda: signal.raise_signal(signal.SIGINT))
    code = live_cli.main(
        [*ARGS, "--run-id", "stopped"], stdin=Terminal("approve\n"), stdout=out
    )
    assert code == 130
    assert "interrupted" in one_error_line(capsys)
    assert PROMPT not in out.getvalue() and cli.server.pulls == []
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert not (cli.runs / LOCK).exists()


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
