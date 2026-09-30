"""Renderer: turns a parsed `Document` into HTML.

Block nodes become lines of HTML that each end in a newline. Running text is parsed
with `mdlite.inline.parse_inline` here, at render time. All escaping and url checks
are delegated to `mdlite.escape`; heading ids come from `mdlite.slug.Slugger`.
"""

import re

from mdlite.escape import escape_attr, escape_text, is_safe_url
from mdlite.inline import parse_inline, plain_text
from mdlite.nodes import (
    Block,
    BlockQuote,
    Code,
    CodeBlock,
    Document,
    Emphasis,
    Heading,
    Image,
    Inline,
    LineBreak,
    Link,
    ListBlock,
    ListItem,
    Paragraph,
    Strong,
    Text,
    ThematicBreak,
)
from mdlite.slug import Slugger


def render_document(document: Document) -> str:
    """Render a whole document (rules R2 to R10, R20)."""
    return _Renderer().blocks(document.children)


def render_inline(nodes: list[Inline]) -> str:
    """Render inline nodes to HTML (rules R10 to R17)."""
    return "".join(_inline(node) for node in nodes)


def _inline(node: Inline) -> str:
    match node:
        case Text(value):
            return escape_text(value)
        case Emphasis(children):
            return f"<em>{render_inline(children)}</em>"
        case Strong(children):
            return f"<strong>{render_inline(children)}</strong>"
        case Code(value):
            return f"<code>{escape_text(value)}</code>"
        case LineBreak():
            return "<br>\n"
        case Link(children, url, title):
            return _link(children, url, title)
        case Image(children, url, title):
            return _image(children, url, title)
    raise TypeError(f"not an inline node: {node!r}")


def _title_attr(title: str | None) -> str:
    return "" if title is None else f' title="{escape_attr(title)}"'


def _link(children: list[Inline], url: str, title: str | None) -> str:
    """A link; an unsafe url leaves just the text (rule R16)."""
    inner = render_inline(children)
    if not is_safe_url(url):
        return inner
    return f'<a href="{escape_attr(url)}"{_title_attr(title)}>{inner}</a>'


def _image(children: list[Inline], url: str, title: str | None) -> str:
    """An image; an unsafe url leaves just the alt text (rule R16)."""
    alt = escape_text(plain_text(children))
    if not is_safe_url(url):
        return alt
    # alt is already escaped above; escaping it again doubled every `&`.
    return f'<img src="{escape_attr(url)}" alt="{alt}"{_title_attr(title)}>'


class _Renderer:
    """Renders blocks; owns the slug state, so make one per document."""

    def __init__(self) -> None:
        self.slugger = Slugger()

    def blocks(self, blocks: list[Block]) -> str:
        return "".join(self.block(block) for block in blocks)

    def block(self, block: Block) -> str:
        match block:
            case Heading(level, text):
                return self.heading(level, text)
            case Paragraph(text):
                return f"<p>{render_inline(parse_inline(text))}</p>\n"
            case CodeBlock(info, code):
                return self.code_block(info, code)
            case ThematicBreak():
                return "<hr>\n"
            case BlockQuote(children):
                return f"<blockquote>\n{self.blocks(children)}</blockquote>\n"
            case ListBlock():
                return self.list_block(block)
        raise TypeError(f"not a block node: {block!r}")

    def heading(self, level: int, text: str) -> str:
        inlines = parse_inline(text)
        slug = self.slugger.unique(plain_text(inlines))
        return (
            f'<h{level} id="{escape_attr(slug)}">{render_inline(inlines)}</h{level}>\n'
        )

    def code_block(self, info: str, code: str) -> str:
        words = re.split(r"[ \t]+", info) if info else []
        css_class = f' class="language-{escape_attr(words[0])}"' if words else ""
        body = escape_text(code)
        return f"<pre><code{css_class}>{body}</code></pre>\n"

    def list_block(self, block: ListBlock) -> str:
        tag = "ol" if block.ordered else "ul"
        start = f' start="{block.start}"' if block.ordered and block.start != 1 else ""
        items = "".join(self.list_item(item) for item in block.items)
        return f"<{tag}{start}>\n{items}</{tag}>\n"

    def list_item(self, item: ListItem) -> str:
        text = render_inline(parse_inline(item.text))
        if item.sublist is None:
            return f"<li>{text}</li>\n"
        return f"<li>{text}\n{self.list_block(item.sublist)}</li>\n"
