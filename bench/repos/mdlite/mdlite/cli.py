"""Command line: `python -m mdlite FILE` prints the HTML for a Markdown file."""

import argparse
import sys
from pathlib import Path

from mdlite import to_html

EXIT_OK = 0
EXIT_UNREADABLE = 1


def _read(source: str) -> str:
    """The text of the file `source`, or of standard input for `-` (rule R21)."""
    if source == "-":
        return sys.stdin.buffer.read().decode("utf-8")
    return Path(source).read_bytes().decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the command line; returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="mdlite", description="Convert a Markdown file to HTML."
    )
    parser.add_argument("file", help="the Markdown file, or - for standard input")
    args = parser.parse_args(argv)
    try:
        text = _read(args.file)
    except (OSError, UnicodeDecodeError) as error:
        print(f"mdlite: {error}", file=sys.stderr)
        return EXIT_UNREADABLE
    sys.stdout.buffer.write(to_html(text).encode("utf-8"))
    sys.stdout.buffer.flush()
    return EXIT_OK
