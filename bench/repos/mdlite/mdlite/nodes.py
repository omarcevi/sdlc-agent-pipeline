"""AST node types shared by every other module.

The block parser produces `Document` trees of block nodes. Block nodes that hold
running text (`Paragraph`, `Heading`, `ListItem`) keep it as a raw string; the
inline parser turns that string into inline nodes when it is needed, so the
renderer and the table of contents both go through `mdlite.inline.parse_inline`.

The nodes are plain dataclasses with structural equality, so tests can compare
whole trees.
"""

from dataclasses import dataclass, field

# Inline nodes ----------------------------------------------------------------


@dataclass
class Text:
    """Literal text. The value is unescaped: `<` here means a real `<`."""

    value: str


@dataclass
class Emphasis:
    """`*x*` or `_x_`."""

    children: list["Inline"]


@dataclass
class Strong:
    """`**x**` or `__x__`."""

    children: list["Inline"]


@dataclass
class Code:
    """A code span. The value is the final content, not parsed further."""

    value: str


@dataclass
class Link:
    """A link or autolink. `children` is the link text."""

    children: list["Inline"]
    url: str
    title: str | None = None


@dataclass
class Image:
    """An image. `children` is the label, whose plain text is the alt text."""

    children: list["Inline"]
    url: str
    title: str | None = None


@dataclass
class LineBreak:
    """A hard line break (`<br>`)."""


Inline = Text | Emphasis | Strong | Code | Link | Image | LineBreak

# Block nodes -----------------------------------------------------------------


@dataclass
class Heading:
    """An ATX heading. `text` is the raw inline source of the heading."""

    level: int
    text: str


@dataclass
class Paragraph:
    """A paragraph. `text` is the raw inline source, lines joined by newlines."""

    text: str


@dataclass
class CodeBlock:
    """A fenced code block. `code` has no trailing newline."""

    info: str
    code: str


@dataclass
class ThematicBreak:
    """A horizontal rule."""


@dataclass
class BlockQuote:
    """A block quote holding blocks."""

    children: list["Block"] = field(default_factory=list)


@dataclass
class ListItem:
    """One list item: raw inline text and at most one nested list."""

    text: str
    sublist: "ListBlock | None" = None


@dataclass
class ListBlock:
    """An ordered or unordered list. `start` is the first number (1 if unordered)."""

    ordered: bool
    start: int
    items: list[ListItem] = field(default_factory=list)


Block = Heading | Paragraph | CodeBlock | ThematicBreak | BlockQuote | ListBlock


@dataclass
class Document:
    """The root of a parsed document."""

    children: list[Block] = field(default_factory=list)
