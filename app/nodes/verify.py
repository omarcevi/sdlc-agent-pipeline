"""Ground truth from the sandbox: the real diff and real test results."""

import re
import time
from typing import Any

from google.adk.events.event import Event

from app.environment import registry
from app.environment.base import InfraError, tail_lines, truncate
from app.nodes.intake import GIT
from app.schemas import Diff, TestReport

MAX_TEST_RETURNS = 3
# Both commands take the baseline commit id as their last argument. The index is
# compared with the baseline recorded by provision_sandbox, not with HEAD, so a
# commit made by the coder cannot empty the diff. --no-renames reports a moved file
# as a delete plus an add, so a renamed protected test still matches its old path.
DIFF_CMD = f"{GIT} add -A && {GIT} diff --cached --no-renames --binary"
NUMSTAT_CMD = f"{GIT} -c core.quotepath=false diff --cached --numstat --no-renames"
TEST_CMD = "python -m pytest -q -p no:cacheprovider"
_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
_BRACE_RENAME = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


def _numstat_paths(field: str) -> list[str]:
    """Paths named by a numstat path field. A rename (`old => new` or
    `pre{old => new}post`) yields both; --no-renames should prevent those, and
    this keeps the protected-file check safe if it ever does not."""
    if match := _BRACE_RENAME.match(field):
        before, old, new, after = match.groups()
        return [
            f"{before}{old}{after}".replace("//", "/"),
            f"{before}{new}{after}".replace("//", "/"),
        ]
    if " => " in field:
        return field.split(" => ", 1)
    return [field]


async def collect_diff(node_input: Any, sandbox_id: str, baseline_sha: str):
    env = registry.get(sandbox_id)
    diff_result = await env.exec(f"{DIFF_CMD} {baseline_sha}")
    if diff_result.exit_code != 0:
        raise InfraError(f"git diff failed: {diff_result.stderr.strip()}")
    numstat = await env.exec(f"{NUMSTAT_CMD} {baseline_sha}")
    if numstat.exit_code != 0:
        raise InfraError(f"git diff --numstat failed: {numstat.stderr.strip()}")
    files, insertions, deletions = [], 0, 0
    for line in numstat.stdout.splitlines():
        added, removed, path = line.split("\t", 2)
        files.extend(_numstat_paths(path))
        insertions += int(added) if added.isdigit() else 0
        deletions += int(removed) if removed.isdigit() else 0
    diff = Diff(
        unified_diff=diff_result.stdout,
        files=files,
        insertions=insertions,
        deletions=deletions,
    )
    yield Event(message=f"diff: {len(files)} files, +{insertions} -{deletions}")
    yield Event(
        output=diff,
        state={
            "diff": diff.model_dump(),
            "diff_text": truncate(diff.unified_diff, 20_000),
        },
    )


def _precheck(diff: Diff, protected_paths: list[str]) -> str | None:
    if diff.is_empty:
        return (
            "No changes were made to the repository. Implement the change, then finish."
        )
    touched = sorted(set(diff.files) & set(protected_paths))
    if touched:
        return (
            "These existing test files are read-only but were modified: "
            + ", ".join(touched)
            + ". Revert them (git checkout -- <file>) and put new tests in new files."
        )
    github_paths = sorted(
        path
        for path in set(diff.files)
        if path == ".github" or path.startswith(".github/")
    )
    if github_paths:
        return (
            ".github/ is read-only but these paths were modified: "
            + ", ".join(github_paths)
            + ". Revert them (git checkout -- <file>) and delete any you added."
        )
    return None


async def run_tests(
    node_input: Diff, sandbox_id: str, protected_paths: list[str], test_attempts: int
):
    problem = _precheck(node_input, protected_paths)
    if problem:
        report = TestReport(passed=False, exit_code=-1, output_tail=problem)
    else:
        started = time.monotonic()
        result = await registry.get(sandbox_id).exec(TEST_CMD)
        output = result.stdout + (f"\n{result.stderr}" if result.stderr else "")
        if result.timed_out:
            output += "\n[test run timed out]"
        report = TestReport(
            passed=result.exit_code == 0 and not result.timed_out,
            exit_code=result.exit_code,
            failed_tests=_FAILED.findall(output),
            output_tail=tail_lines(output, 200),
            duration_s=round(time.monotonic() - started, 2),
        )
    yield Event(
        message=f"tests: {'passed' if report.passed else 'failed'} (exit {report.exit_code})"
    )
    state: dict[str, Any] = {"test_report": report.model_dump()}
    if report.passed:
        yield Event(output=report, route="pass", state=state)
        return
    attempts = test_attempts + 1
    state["test_attempts"] = attempts
    if attempts > MAX_TEST_RETURNS:
        state["failure"] = {
            "kind": "agent",
            "reason": f"tests still failing after {MAX_TEST_RETURNS} fix attempts",
        }
        yield Event(output=report, route="exhausted", state=state)
    else:
        yield Event(output=report, route="fail", state=state)
