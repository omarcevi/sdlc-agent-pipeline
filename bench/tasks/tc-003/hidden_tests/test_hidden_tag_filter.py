from taskcli.cli import main


def _setup(tmp_path):
    path = str(tmp_path / "t.json")
    main(["--file", path, "add", "Write report", "--tag", "work"])
    main(["--file", path, "add", "Buy milk", "--tag", "home"])
    main(["--file", path, "add", "Plan offsite", "--tag", "work", "--tag", "planning"])
    return path


def test_list_filters_by_tag(tmp_path, capsys):
    path = _setup(tmp_path)
    capsys.readouterr()
    assert main(["--file", path, "list", "--tag", "work"]) == 0
    out = capsys.readouterr().out
    assert "Write report" in out and "Plan offsite" in out and "Buy milk" not in out


def test_unknown_tag_lists_nothing(tmp_path, capsys):
    path = _setup(tmp_path)
    capsys.readouterr()
    assert main(["--file", path, "list", "--tag", "nope"]) == 0
    assert capsys.readouterr().out == ""


def test_tag_filter_hides_done_tasks_unless_all(tmp_path, capsys):
    path = _setup(tmp_path)
    main(["--file", path, "done", "1"])
    capsys.readouterr()
    assert main(["--file", path, "list", "--tag", "work"]) == 0
    out = capsys.readouterr().out
    assert "Write report" not in out and "Plan offsite" in out
    assert main(["--file", path, "list", "--tag", "work", "--all"]) == 0
    out = capsys.readouterr().out
    assert "Write report" in out and "Plan offsite" in out and "Buy milk" not in out


def test_tag_filter_combines_with_sort(tmp_path, capsys):
    path = str(tmp_path / "t.json")
    main(["--file", path, "add", "late", "--tag", "work", "--due", "2026-10-05"])
    main(["--file", path, "add", "other", "--tag", "home", "--due", "2026-10-02"])
    main(["--file", path, "add", "soon", "--tag", "work", "--due", "2026-10-01"])
    capsys.readouterr()
    assert main(["--file", path, "list", "--tag", "work", "--sort", "due"]) == 0
    out = capsys.readouterr().out
    assert "other" not in out
    assert out.index("soon") < out.index("late")
