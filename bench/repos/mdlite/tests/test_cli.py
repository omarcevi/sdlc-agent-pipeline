"""The command line and the README itself."""

import ast
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest
from mdlite.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_main_prints_html_for_a_file(tmp_path, capsys):
    """R21: the file is read as UTF-8 and the HTML written to stdout; exit 0."""
    source = tmp_path / "doc.md"
    source.write_bytes("# Café\n\nnaïve *text*\n".encode())
    assert main([str(source)]) == 0
    assert capsys.readouterr().out == (
        '<h1 id="café">Café</h1>\n<p>naïve <em>text</em></p>\n'
    )


def test_main_reads_standard_input_for_a_dash(monkeypatch, capsys):
    """R21: `-` reads standard input."""
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"# In\n")))
    assert main(["-"]) == 0
    assert capsys.readouterr().out == '<h1 id="in">In</h1>\n'


def test_main_reports_a_missing_file(tmp_path, capsys):
    """R21: an unreadable file prints `mdlite: ...` to stderr and exits 1."""
    assert main([str(tmp_path / "missing.md")]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("mdlite: ")


def test_main_reports_invalid_utf8(tmp_path, capsys):
    """R21: an undecodable file also exits 1 with nothing on stdout."""
    source = tmp_path / "bad.md"
    source.write_bytes(b"\xff\xfe# bad")
    assert main([str(source)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("mdlite: ")


def test_main_rejects_wrong_arguments():
    """R21: no file, or too many, is a usage error with exit code 2."""
    for argv in ([], ["a", "b"], ["--nope"]):
        with pytest.raises(SystemExit) as raised:
            main(argv)
        assert raised.value.code == 2


def test_python_dash_m_runs_the_cli(tmp_path):
    """R21: `python -m mdlite FILE` prints the HTML and exits 0."""
    source = tmp_path / "doc.md"
    source.write_text("*hi*\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "mdlite", str(source)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == b"<p><em>hi</em></p>\n"


def test_every_readme_rule_is_named_by_a_test():
    """Every rule R1..Rn in the README appears in at least one test docstring."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    rules = set(re.findall(r"^\*\*(R\d+)\.", readme, flags=re.MULTILINE))
    assert len(rules) >= 10
    named: set[str] = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                named.update(re.findall(r"\bR\d+\b", ast.get_docstring(node) or ""))
    assert rules - named == set()
