"""Check every task is well-formed: visible tests pass at base+plant, hidden tests
fail there, and everything passes with the reference solution. A tempting task's
shortcut/ must pass the visible tests and fail the hidden ones.

Problems are fixed strings: no test output, test name or test source is ever
printed, so validating a held-out task reveals nothing about it.

Hidden tests must be self-contained (no reliance on the repo's conftest): scoring
runs them from a directory outside the repo, cut off from its conftest files and
pytest config (see bench/score.py). This module runs on the host because it only
executes code we authored: the base repo, the plant and the reference solution.
"""

import sys
import tempfile
from pathlib import Path

from app.task_store import (
    LEVERS,
    TaskSpec,
    list_tasks,
    materialize,
    task_dir,
    test_files,
)
from bench._pytest import run_pytest


def _module_count(solution: Path) -> int:
    """Non-test .py files in solution/."""
    tests = set(test_files(solution))
    return sum(
        1
        for p in solution.rglob("*.py")
        if p.relative_to(solution).as_posix() not in tests
    )


def _metadata_problems(task: TaskSpec, directory: Path) -> list[str]:
    problems: list[str] = []
    has_shortcut = (directory / "shortcut").is_dir()
    if task.tempting and not has_shortcut:
        problems.append("tempting task has no shortcut/")
    if has_shortcut and not task.tempting:
        problems.append("shortcut/ given but tempting is false")
    problems += [f"unknown lever: {name}" for name in task.levers if name not in LEVERS]
    if task.tempting and "shortcut" not in task.levers:
        problems.append("tempting tasks must list the shortcut lever")
    solution = directory / "solution"
    if "multi_file" in task.levers and (
        not solution.is_dir() or _module_count(solution) < 2
    ):
        problems.append("multi_file needs a solution that changes two or more modules")
    return problems


def _has_hidden_test_file(hidden: Path) -> bool:
    return any(not f.endswith("conftest.py") for f in test_files(hidden))


def validate_task(task: TaskSpec) -> list[str]:
    directory = task_dir(task.task_id)
    problems = _metadata_problems(task, directory)
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
        if not _has_hidden_test_file(directory / "hidden_tests"):
            problems.append("non-trap tasks need at least one hidden test file")
        if run_pytest(planted, "hidden_tests"):
            problems.append("hidden tests already pass at base+plant")
        if (directory / "shortcut").is_dir():
            shortcut = materialize(
                task,
                Path(tmp) / "shortcut",
                with_shortcut=True,
                with_hidden_tests=True,
            )
            if not run_pytest(shortcut, "tests"):
                problems.append("shortcut fails the visible tests")
            if run_pytest(shortcut, "hidden_tests"):
                problems.append("shortcut passes the hidden tests")
        solved = materialize(
            task, Path(tmp) / "solved", with_solution=True, with_hidden_tests=True
        )
        if not run_pytest(solved):
            problems.append("tests fail with the reference solution")
    return problems


def main() -> int:
    failures = 0
    for task in list_tasks():
        try:
            problems = validate_task(task)
        except Exception as exc:  # a message or traceback could quote sealed text
            print(f"{task.task_id}: error ({type(exc).__name__})")
            failures += 1
            continue
        print(f"{task.task_id}: {'ok' if not problems else '; '.join(problems)}")
        failures += bool(problems)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
