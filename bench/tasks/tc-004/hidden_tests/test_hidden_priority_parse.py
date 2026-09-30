import pytest
from taskcli.cli import main
from taskcli.models import Priority, Task


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("high", Priority.HIGH),
        ("HIGH", Priority.HIGH),
        (" Low ", Priority.LOW),
        ("2", Priority.MEDIUM),
        (3, Priority.HIGH),
    ],
)
def test_parse_accepts_names_and_numbers(text, expected):
    assert Priority.parse(text) is expected


@pytest.mark.parametrize("text", ["urgent", "0", "4", ""])
def test_parse_rejects_unknown(text):
    with pytest.raises(ValueError):
        Priority.parse(text)


def test_from_dict_accepts_numeric_priority():
    assert (
        Task.from_dict({"id": 1, "title": "x", "priority": 3}).priority is Priority.HIGH
    )


def test_cli_priority_unchanged(tmp_path, capsys):
    path = str(tmp_path / "t.json")
    assert main(["--file", path, "add", "x", "--priority", "high"]) == 0
    main(["--file", path, "list"])
    assert "!high" in capsys.readouterr().out
