"""Runs agents-cli with a longer read timeout on its `/run_sse` call.

agents-cli's client reads the server's event stream with a per-read timeout of 120 s
(`google.agents.cli._adk_client._RUN_SSE_TIMEOUT`). A model call that is silent for
longer (the planner took 126 to 231 s in some runs) makes the client drop the eval
case, and the cancelled case leaves its sandbox to the TTL. This launcher sets the
timeout to 30 s to connect and 1800 s per read, then calls agents-cli's own `main()`
with the given arguments. It changes no installed file.

    uv run python scripts/agents_cli_eval.py eval run --dataset ... --config ...

It must run under agents-cli's own interpreter (the one that has `google.agents.cli`).
Started under any other, it finds that interpreter from the first line of the
`agents-cli` executable on PATH and starts itself again with it. Standard library
only.
"""

import importlib
import os
import shutil
import sys

# (connect, read) seconds for requests; the read bound covers the longest silent
# model call (MODEL_CALL_TIMEOUT_S allows 480 s, twice) with room to spare.
RUN_SSE_TIMEOUT = (30, 1800)


def _tool_python() -> str:
    """The interpreter agents-cli runs under, from its executable's shebang."""
    executable = shutil.which("agents-cli")
    if executable is None:
        raise SystemExit("error: agents-cli is not on PATH")
    with open(executable, encoding="utf-8", errors="replace") as handle:
        first = handle.readline().strip()
    if not first.startswith("#!"):
        raise SystemExit(f"error: cannot find agents-cli's interpreter in {executable}")
    return first[2:].split()[0]


# Set on the one re-exec under agents-cli's own interpreter.
REEXEC_MARKER = "ISSUE_TO_PR_AGENTS_CLI_REEXEC"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    try:
        client = importlib.import_module("google.agents.cli._adk_client")
        cli = importlib.import_module("google.agents.cli.main")
    except ImportError:
        if os.environ.get(REEXEC_MARKER):
            raise  # already under the tool's interpreter: re-executing would loop
        python = _tool_python()
        os.environ[REEXEC_MARKER] = "1"
        os.execv(python, [python, os.path.abspath(__file__), *args])
        return 1  # only reached when execv is replaced (it does not return)
    client._RUN_SSE_TIMEOUT = RUN_SSE_TIMEOUT
    sys.argv = ["agents-cli", *args]
    return cli.main() or 0


if __name__ == "__main__":
    sys.exit(main())
