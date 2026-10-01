import io
import sys

import pytest
from mdlite.cli import main

DOC = "# Café\n\nnaïve *text*\n"
HTML = '<h1 id="café">Café</h1>\n<p>naïve <em>text</em></p>\n'.encode()


def streams(data=b""):
    return io.BytesIO(data), io.BytesIO(), io.StringIO()


def test_html_for_a_file_goes_to_the_given_stdout(tmp_path, capsys):
    source = tmp_path / "doc.md"
    source.write_bytes(DOC.encode())
    stdin, stdout, stderr = streams()
    assert main([str(source)], stdin=stdin, stdout=stdout, stderr=stderr) == 0
    assert stdout.getvalue() == HTML
    assert stderr.getvalue() == ""
    assert capsys.readouterr() == ("", "")


def test_a_dash_reads_the_given_stdin_as_utf8(capsys):
    stdin, stdout, stderr = streams(DOC.encode())
    assert main(["-"], stdin=stdin, stdout=stdout, stderr=stderr) == 0
    assert stdout.getvalue() == HTML
    stdin, stdout, stderr = streams(b"\xff\xfe# bad")
    assert main(["-"], stdin=stdin, stdout=stdout, stderr=stderr) == 1
    assert stdout.getvalue() == b""
    assert stderr.getvalue().startswith("mdlite: ")
    assert capsys.readouterr() == ("", "")


def test_an_unreadable_file_is_reported_on_the_given_stderr(tmp_path, capsys):
    stdin, stdout, stderr = streams()
    missing = str(tmp_path / "missing.md")
    assert main([missing], stdin=stdin, stdout=stdout, stderr=stderr) == 1
    assert stdout.getvalue() == b""
    assert stderr.getvalue().startswith("mdlite: ")
    assert stderr.getvalue().endswith("\n")
    assert capsys.readouterr() == ("", "")


def test_wrong_arguments_print_usage_on_the_given_stderr(capsys):
    for argv in ([], ["a", "b"], ["--nope"]):
        stdin, stdout, stderr = streams()
        with pytest.raises(SystemExit) as raised:
            main(argv, stdin=stdin, stdout=stdout, stderr=stderr)
        assert raised.value.code == 2
        assert stderr.getvalue().startswith("usage: mdlite")
        assert stdout.getvalue() == b""
    assert capsys.readouterr() == ("", "")


def test_streams_not_given_are_the_process_streams(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(DOC.encode())))
    stdout = io.BytesIO()
    assert main(["-"], stdout=stdout) == 0
    assert stdout.getvalue() == HTML
    source = tmp_path / "doc.md"
    source.write_bytes(DOC.encode())
    assert main([str(source)], stderr=io.StringIO()) == 0
    assert capsys.readouterr().out == HTML.decode()
    assert main([str(tmp_path / "missing.md")], stdout=io.BytesIO()) == 1
    assert capsys.readouterr().err.startswith("mdlite: ")
    with pytest.raises(SystemExit) as raised:
        main([], stdout=io.BytesIO())
    assert raised.value.code == 2
    assert capsys.readouterr().err.startswith("usage: mdlite")
