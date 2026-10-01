from collections import Counter

import pytest

from app.task_store import list_tasks
from bench.validate import validate_task

TASKS = list_tasks()

DEV_IDS = [
    *(f"tc-00{n}" for n in range(1, 6)),
    *(f"md-00{n}" for n in range(1, 6)),
    *(f"sr-00{n}" for n in range(1, 6)),
]
HELDOUT_IDS = ["md-h01", "md-h02", "sr-h01", "sr-h02", "sr-h03"]


def test_taskcli_tasks_present():
    by_id = {t.task_id: t for t in TASKS}
    for task_id in ("tc-001", "tc-002", "tc-003", "tc-004", "tc-005"):
        assert by_id[task_id].split == "dev"


def test_task_set_shape():
    """IDs and counts only: this test never reads a task's content."""
    assert len(TASKS) == 20
    by_split = {
        split: sorted(t.task_id for t in TASKS if t.split == split)
        for split in ("dev", "heldout")
    }
    assert by_split == {"dev": sorted(DEV_IDS), "heldout": sorted(HELDOUT_IDS)}
    assert Counter(t.category for t in TASKS) == {
        "bug": 8,
        "feature": 6,
        "refactor": 3,
        "trap": 3,
    }
    tempting = [t for t in TASKS if t.tempting]
    assert len(tempting) == 2
    assert all(t.split == "dev" for t in tempting)
    heldout_categories = {t.category for t in TASKS if t.split == "heldout"}
    assert heldout_categories == {"bug", "feature", "refactor", "trap"}


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.task_id)
def test_task_is_valid(task):
    assert validate_task(task) == []
