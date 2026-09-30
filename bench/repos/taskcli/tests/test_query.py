from datetime import date

from taskcli.models import Priority, Task
from taskcli.query import filter_tasks, overdue, sort_tasks


def make(task_id, **kwargs):
    return Task(id=task_id, title=f"t{task_id}", **kwargs)


def test_filter_hides_done_by_default():
    tasks = [make(1), make(2, done=True)]
    assert [t.id for t in filter_tasks(tasks)] == [1]
    assert [t.id for t in filter_tasks(tasks, include_done=True)] == [1, 2]


def test_sort_by_due_when_all_have_dates():
    tasks = [make(1, due=date(2026, 10, 5)), make(2, due=date(2026, 10, 1))]
    assert [t.id for t in sort_tasks(tasks, "due")] == [2, 1]


def test_sort_by_priority_high_first():
    tasks = [make(1, priority=Priority.LOW), make(2, priority=Priority.HIGH), make(3)]
    assert [t.id for t in sort_tasks(tasks, "priority")] == [2, 3, 1]


def test_overdue():
    tasks = [
        make(1, due=date(2026, 9, 1)),
        make(2, due=date(2026, 12, 1)),
        make(3, due=date(2026, 9, 1), done=True),
    ]
    assert [t.id for t in overdue(tasks, date(2026, 9, 30))] == [1]
