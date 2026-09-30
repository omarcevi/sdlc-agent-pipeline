from datetime import date

from taskcli.cli import main
from taskcli.dates import parse_due


def test_iso_datetime_with_offset():
    assert parse_due("2026-10-01T09:30:00+02:00") == date(2026, 10, 1)


def test_iso_datetime_with_z():
    assert parse_due("2026-10-01T23:00:00Z") == date(2026, 10, 1)


def test_naive_iso_datetime():
    assert parse_due("2026-10-01T09:30") == date(2026, 10, 1)


def test_cli_accepts_datetime(tmp_path, capsys):
    path = str(tmp_path / "t.json")
    assert (
        main(["--file", path, "add", "Call bank", "--due", "2026-10-01T09:30:00+02:00"])
        == 0
    )
    main(["--file", path, "list"])
    assert "due 2026-10-01" in capsys.readouterr().out
