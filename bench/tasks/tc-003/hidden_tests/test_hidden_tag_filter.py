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
