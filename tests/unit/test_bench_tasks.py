import pytest

from app.task_store import list_tasks
from bench.validate import validate_task

TASKS = list_tasks()


def test_taskcli_tasks_present():
    by_id = {t.task_id: t for t in TASKS}
    for task_id in ("tc-001", "tc-002", "tc-003", "tc-004", "tc-005"):
        assert by_id[task_id].split == "dev"


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.task_id)
def test_task_is_valid(task):
    assert validate_task(task) == []
