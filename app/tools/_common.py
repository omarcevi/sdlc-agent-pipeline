"""Shared helpers for agent tools: path confinement and sandbox lookup."""

import posixpath

from google.adk.tools import ToolContext

from app.environment import registry
from app.environment.base import WORKDIR, Environment


class PathError(ValueError):
    """A tool path points outside the repository."""


def resolve_repo_path(path: str) -> str:
    """Map a repo-relative (or /workspace/repo-absolute) path to an absolute
    sandbox path, refusing anything that escapes the repo."""
    if path == WORKDIR or path.startswith(WORKDIR + "/"):
        candidate = path
    elif path.startswith("/"):
        raise PathError(f"absolute paths outside the repo are not allowed: {path}")
    else:
        candidate = posixpath.join(WORKDIR, path)
    normalized = posixpath.normpath(candidate)
    if normalized != WORKDIR and not normalized.startswith(WORKDIR + "/"):
        raise PathError(f"path escapes the repo: {path}")
    return normalized


def env_for(tool_context: ToolContext) -> Environment:
    return registry.get(tool_context.state["sandbox_id"])
