from datetime import date

from taskcli.models import Task

SORT_KEYS = ("id", "due", "priority")


def filter_tasks(tasks: list[Task], *, include_done: bool = False) -> list[Task]:
    return [t for t in tasks if include_done or not t.done]


def sort_tasks(tasks: list[Task], key: str = "id") -> list[Task]:
    if key == "id":
        return sorted(tasks, key=lambda t: t.id)
    if key == "due":
        return sorted(tasks, key=lambda t: (t.due or date.min, t.id))
    if key == "priority":
        return sorted(tasks, key=lambda t: (-t.priority.value, t.id))
    raise ValueError(f"unknown sort key: {key}")


def overdue(tasks: list[Task], today: date) -> list[Task]:
    return [t for t in tasks if t.due is not None and t.due < today and not t.done]
