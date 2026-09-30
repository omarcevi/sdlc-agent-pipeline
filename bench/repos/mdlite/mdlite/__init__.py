"""mdlite: a small Markdown-to-HTML library.

Public API: `to_html(text)` and `toc(text)`. See the README for the rules.
"""

from mdlite.blocks import parse
from mdlite.render import render_document
from mdlite.toc import toc

__all__ = ["to_html", "toc"]


def to_html(text: str) -> str:
    """Convert Markdown `text` to HTML."""
    return render_document(parse(text))
