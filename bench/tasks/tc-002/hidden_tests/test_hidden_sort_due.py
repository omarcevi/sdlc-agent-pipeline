from datetime import date

from taskcli.cli import main
from taskcli.models import Task
from taskcli.query import sort_tasks


def test_undated_tasks_sort_last():
    tasks = [
        Task(id=1, title="none"),
        Task(id=2, title="late", due=date(2026, 10, 5)),
        Task(id=3, title="soon", due=date(2026, 10, 1)),
    ]
    assert [t.id for t in sort_tasks(tasks, "due")] == [3, 2, 1]


def test_cli_list_sort_due(tmp_path, capsys):
    path = str(tmp_path / "t.json")
    main(["--file", path, "add", "none"])
    main(["--file", path, "add", "late", "--due", "2026-10-05"])
    main(["--file", path, "add", "soon", "--due", "2026-10-01"])
    capsys.readouterr()
    main(["--file", path, "list", "--sort", "due"])
    out = capsys.readouterr().out
    assert out.index("soon") < out.index("late") < out.index("none")
