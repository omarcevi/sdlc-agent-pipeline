import pytest

from app.task_store import list_tasks
from bench.validate import validate_task

TASKS = list_tasks()


def test_week1_task_set():
    assert [t.task_id for t in TASKS] == [
        "tc-001",
        "tc-002",
        "tc-003",
        "tc-004",
        "tc-005",
    ]
    assert {t.split for t in TASKS} == {"dev"}


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.task_id)
def test_task_is_valid(task):
    assert validate_task(task) == []
