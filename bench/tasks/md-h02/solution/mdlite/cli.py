"""Command line: `python -m mdlite FILE` prints the HTML for a Markdown file."""

import argparse
import sys
from pathlib import Path
from typing import BinaryIO, TextIO

from mdlite import to_html

EXIT_OK = 0
EXIT_UNREADABLE = 1


class _Parser(argparse.ArgumentParser):
    """An argument parser that prints its error messages to a given text stream."""

    def __init__(self, stderr: TextIO, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._stderr = stderr

    def _print_message(self, message: str, file: object = None) -> None:
        if file is sys.stderr:
            file = self._stderr
        super()._print_message(message, file)  # type: ignore[arg-type]


def _read(source: str, stdin: BinaryIO) -> str:
    """The text of the file `source`, or of `stdin` for `-` (rule R21)."""
    if source == "-":
        return stdin.read().decode("utf-8")
    return Path(source).read_bytes().decode("utf-8")


def main(
    argv: list[str] | None = None,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the command line; returns the process exit code.

    `stdin` and `stdout` are binary streams and `stderr` a text stream; each one
    that is not given is the process's own, looked up when `main` runs. Wrong
    arguments print a usage message to `stderr` and raise `SystemExit(2)`.
    """
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout.buffer if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = _Parser(
        stderr, prog="mdlite", description="Convert a Markdown file to HTML."
    )
    parser.add_argument("file", help="the Markdown file, or - for standard input")
    args = parser.parse_args(argv)
    try:
        text = _read(args.file, stdin)
    except (OSError, UnicodeDecodeError) as error:
        print(f"mdlite: {error}", file=stderr)
        return EXIT_UNREADABLE
    stdout.write(to_html(text).encode("utf-8"))
    stdout.flush()
    return EXIT_OK
