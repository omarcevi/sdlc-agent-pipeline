from taskcli.cli import main


def run(tmp_path, *args):
    return main(["--file", str(tmp_path / "tasks.json"), *args])


def test_add_and_list(tmp_path, capsys):
    assert run(tmp_path, "add", "Buy milk", "--due", "2026-10-01", "--tag", "home") == 0
    assert run(tmp_path, "list") == 0
    out = capsys.readouterr().out
    assert "Buy milk" in out and "due 2026-10-01" in out and "#home" in out


def test_done_hides_task(tmp_path, capsys):
    run(tmp_path, "add", "A")
    run(tmp_path, "done", "1")
    capsys.readouterr()
    run(tmp_path, "list")
    assert capsys.readouterr().out == ""


def test_remove_unknown_task(tmp_path):
    assert run(tmp_path, "remove", "9") == 1


def test_invalid_due_date_exit_code(tmp_path):
    assert run(tmp_path, "add", "X", "--due", "someday") == 2
