"""Ground truth from the sandbox: the real diff and real test results."""

import re
import time
from typing import Any

from google.adk.events.event import Event

from app.environment import registry
from app.environment.base import InfraError, tail_lines, truncate
from app.schemas import Diff, TestReport

MAX_TEST_RETURNS = 3
DIFF_CMD = "git add -A && git diff --cached HEAD"
NUMSTAT_CMD = "git diff --cached --numstat HEAD"
TEST_CMD = "python -m pytest -q -p no:cacheprovider"
_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


async def collect_diff(node_input: Any, sandbox_id: str):
    env = registry.get(sandbox_id)
    diff_result = await env.exec(DIFF_CMD)
    if diff_result.exit_code != 0:
        raise InfraError(f"git diff failed: {diff_result.stderr.strip()}")
    numstat = await env.exec(NUMSTAT_CMD)
    files, insertions, deletions = [], 0, 0
    for line in numstat.stdout.splitlines():
        added, removed, path = line.split("\t", 2)
        files.append(path)
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
