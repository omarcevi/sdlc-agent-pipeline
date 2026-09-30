"""Tool-call guardrails, applied runner-wide as an ADK plugin.

These give the model a clear reason when it tries something disallowed. They
are not the security boundary: the sandbox has no network and no credentials,
and run_tests rejects diffs that touch protected files. Shell tricks such as
command substitution can bypass check_command; that is acceptable because of
those deeper layers.
"""

import posixpath
import re
import shlex
from collections.abc import Iterator
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from app.environment.base import WORKDIR
from app.tools import WRITE_TOOL_NAMES
from app.tools._common import PathError, resolve_repo_path

_OPERATOR = re.compile(r"^[;&|()\n]+$")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_DENIED_PREFIXES = [
    ("git", "push"),
    ("git", "remote"),
    ("curl",),
    ("wget",),
    ("pip", "install"),
    ("pip3", "install"),
    ("python", "-m", "pip"),
    ("python3", "-m", "pip"),
    ("uv", "add"),
    ("uv", "pip"),
]


def _segments(command: str) -> Iterator[list[str]]:
    """Split a shell command into simple commands, respecting quotes."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    segment: list[str] = []
    for token in lexer:
        if _OPERATOR.match(token):
            if segment:
                yield segment
            segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def check_command(command: str) -> str | None:
    """Return a refusal reason, or None if the command is allowed."""
    try:
        segments = list(_segments(command))
    except ValueError:
        return "could not parse the command; simplify its quoting"
    for tokens in segments:
        while tokens and _ENV_ASSIGNMENT.match(tokens[0]):
            tokens.pop(0)
        if not tokens:
            continue
        for prefix in _DENIED_PREFIXES:
            if tuple(tokens[: len(prefix)]) == prefix:
                return f"'{' '.join(prefix)}' is not allowed in the sandbox"
        if tokens[0] == "rm":
            for arg in tokens[1:]:
                if arg.startswith("-"):
                    continue
                target = arg if arg.startswith("/") else posixpath.join(WORKDIR, arg)
                normalized = posixpath.normpath(target)
                if not normalized.startswith(WORKDIR + "/"):
                    return "rm is only allowed on files inside the repository"
    return None


def check_write_path(path: str, protected: frozenset[str]) -> str | None:
    """Return a refusal reason for writing `path`, or None if allowed."""
    try:
        target = resolve_repo_path(path)
    except PathError as exc:
        return str(exc)
    relative = posixpath.relpath(target, WORKDIR)
    if relative == ".github" or relative.startswith(".github/"):
        return ".github/ is read-only"
    if relative in protected:
        return f"{relative} is an existing test file and is read-only; add a new test file instead"
    return None


class GuardrailPlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="guardrails")

    async def before_tool_callback(
        self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any
    ) -> dict | None:
        if tool.name == "bash":
            reason = check_command(str(tool_args.get("command", "")))
        elif tool.name in WRITE_TOOL_NAMES:
            protected = frozenset(tool_context.state.get("protected_paths", []))
            reason = check_write_path(str(tool_args.get("path", "")), protected)
        else:
            return None
        return {"error": f"blocked by guardrail: {reason}"} if reason else None
