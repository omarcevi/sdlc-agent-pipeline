"""Sandbox environment contract. Tools talk to this, never to the host."""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

WORKDIR = "/workspace/repo"
OUTPUT_CAP = 10_000
DEFAULT_TIMEOUT_S = 120.0


class InfraError(RuntimeError):
    """The sandbox or its backend failed. Not the agent's fault; runs are retried."""


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Environment(Protocol):
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
