from datetime import date

from taskcli.models import Priority, Task
from taskcli.store import TaskStore


def test_missing_file_is_empty(tmp_path):
    assert TaskStore(tmp_path / "none.json").load() == []


def test_roundtrip(tmp_path):
    store = TaskStore(tmp_path / "tasks.json")
    tasks = [
        Task(
            id=1,
            title="a",
            priority=Priority.HIGH,
            due=date(2026, 10, 1),
            tags=["work"],
        )
    ]
    store.save(tasks)
    assert store.load() == tasks


def test_next_id(tmp_path):
    store = TaskStore(tmp_path / "t.json")
    assert store.next_id([]) == 1
    assert store.next_id([Task(id=4, title="x")]) == 5
