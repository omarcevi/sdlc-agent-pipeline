import argparse
import sys
from datetime import date

from taskcli.dates import parse_due
from taskcli.models import Priority, Task
from taskcli.query import SORT_KEYS, filter_tasks, sort_tasks
from taskcli.store import TaskStore


def format_task(task: Task) -> str:
    mark = "x" if task.done else " "
    parts = [f"[{mark}] {task.id:>3}  {task.title}"]
    if task.priority is not Priority.MEDIUM:
        parts.append(f"!{task.priority.name.lower()}")
    if task.due:
        parts.append(f"due {task.due.isoformat()}")
    if task.tags:
        parts.append(" ".join(f"#{tag}" for tag in task.tags))
    return "  ".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskcli")
    parser.add_argument(
        "--file", help="task file (default: $TASKCLI_FILE or ~/.taskcli.json)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add a task")
    add.add_argument("title")
    add.add_argument("--due")
    add.add_argument(
        "--priority", choices=[p.name.lower() for p in Priority], default="medium"
    )
    add.add_argument("--tag", action="append", default=[])
    listing = sub.add_parser("list", help="list open tasks")
    listing.add_argument("--all", action="store_true", help="include done tasks")
    listing.add_argument("--sort", choices=SORT_KEYS, default="id")
    listing.add_argument("--tag", help="only show tasks with this tag")
    done = sub.add_parser("done", help="mark a task done")
    done.add_argument("id", type=int)
    remove = sub.add_parser("remove", help="delete a task")
    remove.add_argument("id", type=int)
    return parser


def main(argv: list[str] | None = None, today: date | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = TaskStore(args.file)
    tasks = store.load()

    if args.command == "add":
        try:
            due = parse_due(args.due, today) if args.due else None
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        task = Task(
            id=store.next_id(tasks),
            title=args.title,
            priority=Priority[args.priority.upper()],
            due=due,
            tags=args.tag,
        )
        tasks.append(task)
        store.save(tasks)
        print(f"added {task.id}")
        return 0

    if args.command == "list":
        visible = filter_tasks(tasks, include_done=args.all)
        if args.tag:
            visible = [t for t in visible if args.tag in t.tags]
        for task in sort_tasks(visible, args.sort):
            print(format_task(task))
        return 0

    target = next((t for t in tasks if t.id == args.id), None)
    if target is None:
        print(f"error: no task {args.id}", file=sys.stderr)
        return 1
    if args.command == "done":
        target.done = True
        store.save(tasks)
        print(f"done {target.id}")
        return 0
    tasks.remove(target)
    store.save(tasks)
    print(f"removed {target.id}")
    return 0
