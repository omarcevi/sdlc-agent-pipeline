"""Sandbox environment contract. Tools talk to this, never to the host."""

import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

WORKDIR = "/workspace/repo"
OUTPUT_CAP = 10_000
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_SANDBOX_TTL_S = 1800
# Both backends run each command as `timeout -k 5 <n> sh -c <command>`. timeout(1)
# exits 124 after SIGTERM, or 137 when it had to follow up with SIGKILL.
TIMEOUT_EXIT_CODES = (124, 137)


def timeout_seconds(timeout: float) -> int:
    """The `<n>` of the `timeout` wrapper: whole seconds, rounded up, at least 1."""
    return max(1, math.ceil(timeout))


def sandbox_ttl_s(environ: Mapping[str, str] | None = None) -> int:
    """SANDBOX_TTL_S (default 1800): how long a sandbox may live, on either backend.
    Anything but a positive whole number of seconds raises ValueError."""
    environ = os.environ if environ is None else environ
    raw = environ.get("SANDBOX_TTL_S", str(DEFAULT_SANDBOX_TTL_S))
    if not (raw.isascii() and raw.isdigit() and int(raw) > 0):
        raise ValueError(
            f"SANDBOX_TTL_S must be a positive whole number of seconds, got {raw!r}"
        )
    return int(raw)


class InfraError(RuntimeError):
    """The sandbox or its backend failed. Not the agent's fault; runs are retried."""


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Environment(Protocol):
    """One sandbox.

    Error contract, so that an agent's mistake never looks like a broken sandbox:

    - `InfraError`: the sandbox or its backend failed (daemon down, container
      gone). Any method may raise it. Runs are retried, never blamed on the agent.
    - `read_file` raises `FileNotFoundError` when the file does not exist.
    - `read_file` and `write_file` raise `OSError`, carrying the sandbox's error
      text, for every other failure caused by the path itself: it is a directory,
      its parent is a file, permission is denied. Tools report these to the model
      as tool errors.
    - `exec` does not raise for a failing command. It returns the exit code, with
      `timed_out` set when the command was stopped at its timeout.
    """

    env_id: str

    async def exec(
        self, command: str, *, timeout: float = DEFAULT_TIMEOUT_S, cwd: str = WORKDIR
    ) -> ExecResult: ...

    async def read_file(self, path: str) -> str: ...

    async def write_file(self, path: str, content: str) -> None: ...

    async def upload_dir(self, local_dir: Path, dest: str = WORKDIR) -> None: ...

    async def close(self) -> None: ...


def truncate(text: str, cap: int = OUTPUT_CAP) -> str:
    """Keep the head and tail of long output so errors at either end survive."""
    if len(text) <= cap:
        return text
    head = cap // 2
    tail = cap - head
    omitted = len(text) - cap
    return f"{text[:head]}\n... [{omitted} chars truncated] ...\n{text[-tail:]}"


def tail_lines(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])
