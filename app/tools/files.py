"""File tools. Paths are repo-relative; everything runs inside the sandbox."""

import shlex

from google.adk.tools import ToolContext

from app.environment.base import WORKDIR, truncate
from app.tools._common import PathError, env_for, resolve_repo_path

LIST_LIMIT = 200
_NOT_A_DIRECTORY = 3


async def read_file(path: str, tool_context: ToolContext) -> dict:
    """Read a text file from the repository.

    Args:
      path: Repo-relative path, for example "taskcli/cli.py".
    """
    path = path or "."
    try:
        target = resolve_repo_path(path)
    except PathError as exc:
        return {"error": str(exc)}
    try:
        content = await env_for(tool_context).read_file(target)
    except FileNotFoundError:
        return {"error": f"file not found: {path}"}
    except OSError as exc:
        return {"error": f"cannot read {path}: {exc}"}
    return {"path": path, "content": truncate(content)}


async def list_dir(path: str, tool_context: ToolContext) -> dict:
    """List files under a directory, recursively (max 200 entries, .git ignored).
    The result has "truncated": true when there were more entries than the limit.

    Args:
      path: Repo-relative directory; use "." for the whole repository.
    """
    path = path or "."
    try:
        target = resolve_repo_path(path)
    except PathError as exc:
        return {"error": str(exc)}
    quoted = shlex.quote(target)
    # One entry more than the limit is requested, to tell "exactly at the limit"
    # from "cut off".
    command = (
        f"test -d {quoted} || exit {_NOT_A_DIRECTORY}; "
        f"find {quoted} -path '*/.git' -prune -o -type f -print "
        f"| sort | head -n {LIST_LIMIT + 1}"
    )
    result = await env_for(tool_context).exec(command)
    if result.exit_code == _NOT_A_DIRECTORY:
        return {"error": f"{path} does not exist or is not a directory"}
    if result.exit_code != 0:
        return {
            "error": truncate(result.stderr)
            or f"listing {path} failed (exit {result.exit_code})"
        }
    lines = result.stdout.splitlines()
    listing: dict = {
        "entries": [line.removeprefix(WORKDIR + "/") for line in lines[:LIST_LIMIT]]
    }
    if len(lines) > LIST_LIMIT:
        listing["truncated"] = True
    return listing


async def grep(pattern: str, path: str, tool_context: ToolContext) -> dict:
    """Search file contents with a regular expression (ripgrep).

    Args:
      pattern: Regular expression to search for.
      path: Repo-relative file or directory; use "." for the whole repository.
    """
    path = path or "."
    try:
        target = resolve_repo_path(path)
    except PathError as exc:
        return {"error": str(exc)}
    command = (
        f"rg --line-number --no-heading --max-count 50 -e {shlex.quote(pattern)} "
        f"{shlex.quote(target)}"
    )
    result = await env_for(tool_context).exec(command)
    if result.exit_code == 1:
        return {"matches": ""}
    if result.exit_code > 1:
        return {"error": truncate(result.stderr)}
    return {"matches": truncate(result.stdout.replace(WORKDIR + "/", ""))}


async def edit_file(
    path: str, old_string: str, new_string: str, tool_context: ToolContext
) -> dict:
    """Replace exactly one occurrence of old_string with new_string in an existing file.

    Args:
      path: Repo-relative path of the file to edit.
      old_string: Exact text to replace, including indentation. Must match once.
      new_string: Replacement text.
    """
    if not old_string:
        return {"error": "old_string must not be empty; use write_file to create files"}
    path = path or "."
    try:
        target = resolve_repo_path(path)
    except PathError as exc:
        return {"error": str(exc)}
    env = env_for(tool_context)
    try:
        content = await env.read_file(target)
    except FileNotFoundError:
        return {"error": f"file not found: {path}"}
    except OSError as exc:
        return {"error": f"cannot read {path}: {exc}"}
    count = content.count(old_string)
    if count == 0:
        return {
            "error": "old_string not found; re-read the file and copy the exact text"
        }
    if count > 1:
        return {
            "error": f"old_string matches {count} times; include more surrounding context"
        }
    try:
        await env.write_file(target, content.replace(old_string, new_string, 1))
    except OSError as exc:
        return {"error": f"cannot write {path}: {exc}"}
    return {"ok": True, "path": path}


async def write_file(path: str, content: str, tool_context: ToolContext) -> dict:
    """Create a NEW file. Fails if it already exists; use edit_file for existing files.

    Args:
      path: Repo-relative path of the new file.
      content: Full file content.
    """
    path = path or "."
    try:
        target = resolve_repo_path(path)
    except PathError as exc:
        return {"error": str(exc)}
    env = env_for(tool_context)
    if (await env.exec(f"test -e {shlex.quote(target)}")).exit_code == 0:
        return {"error": f"{path} already exists; use edit_file"}
    try:
        await env.write_file(target, content)
    except OSError as exc:
        return {"error": f"cannot write {path}: {exc}"}
    return {"ok": True, "path": path}
