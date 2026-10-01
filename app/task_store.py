"""Bench task format: bench/tasks/<id>/{task.yaml, plant/, solution/, hidden_tests/}.

A working copy is the clean repo from bench/repos/<repo>, with plant/ copied over it,
then optionally solution/ or shortcut/ (never both) and hidden_tests/.
"""

import os
import shutil
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

_NO_CACHES = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")

LEVERS = frozenset(
    {"multi_file", "distant_symptom", "doc_rule", "regression_risk", "shortcut"}
)


class TaskSpec(BaseModel):
    task_id: str
    repo: str
    # Out of reprs: pytest prints parametrised TaskSpec objects in tracebacks.
    title: str = Field(repr=False)
    body: str = Field(repr=False)
    category: Literal["bug", "feature", "refactor", "trap"]
    difficulty: Literal["easy", "medium", "hard"]
    split: Literal["dev", "heldout"]
    tempting: bool = False
    # Plain strings: bench.validate rejects unknown names, so one bad task.yaml
    # never stops list_tasks().
    levers: list[str] = []


def tasks_dir() -> Path:
    return Path(os.environ.get("BENCH_TASKS_DIR", "bench/tasks"))


def repos_dir() -> Path:
    return Path(os.environ.get("BENCH_REPOS_DIR", "bench/repos"))


def task_dir(task_id: str) -> Path:
    return tasks_dir() / task_id


def load_task(task_id: str) -> TaskSpec:
    data = yaml.safe_load((task_dir(task_id) / "task.yaml").read_text())
    return TaskSpec(task_id=task_id, **data)


def list_tasks() -> list[TaskSpec]:
    return [load_task(p.parent.name) for p in sorted(tasks_dir().glob("*/task.yaml"))]


def _overlay(src: Path, dest: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=_NO_CACHES)


def materialize(
    task: TaskSpec,
    dest: Path,
    *,
    with_solution: bool = False,
    with_hidden_tests: bool = False,
    with_shortcut: bool = False,
) -> Path:
    if with_solution and with_shortcut:
        raise ValueError("with_solution and with_shortcut are mutually exclusive")
    shutil.copytree(repos_dir() / task.repo, dest, ignore=_NO_CACHES)
    _overlay(task_dir(task.task_id) / "plant", dest)
    if with_shortcut:
        _overlay(task_dir(task.task_id) / "shortcut", dest)
    if with_solution:
        _overlay(task_dir(task.task_id) / "solution", dest)
    if with_hidden_tests:
        _overlay(task_dir(task.task_id) / "hidden_tests", dest / "hidden_tests")
    return dest


def test_files(repo_dir: Path) -> list[str]:
    """Repo-relative paths of existing test modules (these become read-only)."""
    found = []
    for path in repo_dir.rglob("*.py"):
        relative = path.relative_to(repo_dir).as_posix()
        name = path.name
        if (
            relative.startswith("tests/")
            or name.startswith("test_")
            or name.endswith("_test.py")
            or name == "conftest.py"
        ):
            found.append(relative)
    return sorted(found)


test_files.__test__ = (
    False  # the name starts with "test"; stop pytest from collecting it
)
