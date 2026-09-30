"""Table of contents: the headings of a document with their slugs (rule R20)."""

from collections.abc import Iterator

from mdlite.blocks import parse
from mdlite.inline import parse_inline, plain_text
from mdlite.nodes import Block, BlockQuote, Heading
from mdlite.slug import Slugger


def iter_headings(blocks: list[Block]) -> Iterator[Heading]:
    """Every heading in document order, including those inside block quotes."""
    for block in blocks:
        if isinstance(block, Heading):
            yield block
        elif isinstance(block, BlockQuote):
            yield from iter_headings(block.children)


def toc(text: str, max_level: int = 6) -> list[tuple[int, str, str]]:
    """The table of contents of `text`: (level, plain text, slug) per heading.

    Only headings of level `max_level` or less are listed. The slugs are the ids
    `mdlite.to_html` puts on the same headings, so every heading takes its slug,
    listed or not (rule R19 numbers duplicates across the whole document).
    """
    slugger = Slugger()
    entries = []
    for heading in iter_headings(parse(text).children):
        title = plain_text(parse_inline(heading.text))
        slug = slugger.unique(title)
        if heading.level <= max_level:
            entries.append((heading.level, title, slug))
    return entries
