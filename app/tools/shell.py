"""Shell tool: runs inside the sandbox (no network, repo root as cwd)."""

from google.adk.tools import ToolContext

from app.environment.base import truncate
from app.tools._common import env_for


async def bash(command: str, tool_context: ToolContext) -> dict:
    """Run a shell command in the repository root inside the sandbox. There is no
    network access. Run the test suite with: python -m pytest -q

    Args:
      command: The shell command to run.
    """
    result = await env_for(tool_context).exec(command)
    return {
        "exit_code": result.exit_code,
        "stdout": truncate(result.stdout),
        "stderr": truncate(result.stderr),
        "timed_out": result.timed_out,
    }
