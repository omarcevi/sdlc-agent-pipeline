"""Check every task is well-formed: visible tests pass at base+plant, hidden tests
fail there, and everything passes with the reference solution.

Hidden tests must be self-contained (no reliance on the repo's conftest): scoring
runs them from a directory outside the repo, cut off from its conftest files and
pytest config (see bench/score.py). This module runs on the host because it only
executes code we authored: the base repo, the plant and the reference solution.
"""

import sys
import tempfile
from pathlib import Path

from app.task_store import TaskSpec, list_tasks, materialize, task_dir
from bench._pytest import run_pytest


def validate_task(task: TaskSpec) -> list[str]:
    problems: list[str] = []
    directory = task_dir(task.task_id)
    with tempfile.TemporaryDirectory() as tmp:
        planted = materialize(task, Path(tmp) / "planted", with_hidden_tests=True)
        if not run_pytest(planted, "tests"):
            problems.append("visible tests fail at base+plant")
        if task.category == "trap":
            if (directory / "hidden_tests").exists():
                problems.append("trap tasks must not have hidden tests")
            return problems
        if (
            not (directory / "hidden_tests").is_dir()
            or not (directory / "solution").is_dir()
        ):
            return [*problems, "non-trap tasks need hidden_tests/ and solution/"]
        if run_pytest(planted, "hidden_tests"):
            problems.append("hidden tests already pass at base+plant")
        solved = materialize(
            task, Path(tmp) / "solved", with_solution=True, with_hidden_tests=True
        )
        if not run_pytest(solved):
            problems.append("tests fail with the reference solution")
    return problems


def main() -> int:
    failures = 0
    for task in list_tasks():
        problems = validate_task(task)
        print(f"{task.task_id}: {'ok' if not problems else '; '.join(problems)}")
        failures += bool(problems)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
