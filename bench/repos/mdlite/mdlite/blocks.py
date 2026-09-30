"""Block parser: turns text into a `Document` of block nodes.

The parser works on a list of lines and looks at one block at a time. Paragraph,
heading and list-item text stays raw; `mdlite.inline` parses it later. Block quotes
are parsed by calling the block parser again on their unquoted lines.

The rules are numbered R1 to R9 in the README.
"""

import re
from dataclasses import dataclass, field

from mdlite.nodes import (
    Block,
    BlockQuote,
    CodeBlock,
    Document,
    Heading,
    ListBlock,
    ListItem,
    Paragraph,
    ThematicBreak,
)

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$")
_CLOSING_HASHES = re.compile(r"(?:^|[ \t]+)#+$")
_THEMATIC_BREAK = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
_LIST_MARKER = re.compile(r"^( *)([-*+]|\d{1,9}[.)])( +|$)(.*)$")
_LEADING_WHITESPACE = re.compile(r"^[ \t]+")

_MAX_TOP_LEVEL_INDENT = 3
_MAX_MARKER_GAP = 4


def parse(text: str) -> Document:
    """Parse `text` into a document (rule R1 is applied here)."""
    return Document(_parse_blocks(_split_lines(text)))


def _split_lines(text: str) -> list[str]:
    """Normalise line endings and expand leading tabs (rule R1)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [_LEADING_WHITESPACE.sub(_expand_tabs, line) for line in lines]


def _expand_tabs(match: re.Match[str]) -> str:
    return match.group(0).expandtabs(4)


def _is_blank(line: str) -> bool:
    return not line.strip()


# Block starts ----------------------------------------------------------------


def _is_thematic_break(line: str) -> bool:
    return _THEMATIC_BREAK.match(line) is not None


@dataclass
class _Fence:
    """An opening code fence."""

    char: str
    length: int
    indent: int
    info: str


def _open_fence(line: str) -> _Fence | None:
    match = _FENCE.match(line)
    if match is None:
        return None
    marker, info = match.group(2), match.group(3).strip()
    if marker[0] == "`" and "`" in info:
        return None
    return _Fence(marker[0], len(marker), len(match.group(1)), info)


def _closes_fence(line: str, fence: _Fence) -> bool:
    stripped = line.strip()
    if len(line) - len(line.lstrip(" ")) > _MAX_TOP_LEVEL_INDENT:
        return False
    return len(stripped) >= fence.length and stripped == fence.char * len(stripped)


def _interrupts_list_item(line: str) -> bool:
    """True for lines that end a list item's text without being a marker line."""
    return (
        _ATX.match(line) is not None
        or _open_fence(line) is not None
        or _QUOTE.match(line) is not None
        or _is_thematic_break(line)
    )


def _interrupts_paragraph(line: str) -> bool:
    if _interrupts_list_item(line):
        return True
    marker = _marker(line)
    return marker is not None and marker.indent <= _MAX_TOP_LEVEL_INDENT


# Block parsing ---------------------------------------------------------------


def _parse_blocks(lines: list[str]) -> list[Block]:
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_blank(line):
            i += 1
            continue
        fence = _open_fence(line)
        if fence is not None:
            block, i = _parse_fence(lines, i, fence)
        elif (heading := _parse_heading(line)) is not None:
            block, i = heading, i + 1
        elif _is_thematic_break(line):
            block, i = ThematicBreak(), i + 1
        elif _QUOTE.match(line):
            block, i = _parse_quote(lines, i)
        elif (marker := _marker(line)) is not None and (
            marker.indent <= _MAX_TOP_LEVEL_INDENT
        ):
            block, i = _parse_list(lines, i, marker)
        else:
            block, i = _parse_paragraph(lines, i)
        blocks.append(block)
    return blocks


def _parse_heading(line: str) -> Heading | None:
    """An ATX heading (rule R2), or None if the line is not one."""
    match = _ATX.match(line)
    if match is None:
        return None
    text = _CLOSING_HASHES.sub("", (match.group(2) or "").strip()).strip()
    return Heading(level=len(match.group(1)), text=text)


def _parse_fence(lines: list[str], start: int, fence: _Fence) -> tuple[CodeBlock, int]:
    """A fenced code block (rule R5). An unclosed fence runs to the last line."""
    content: list[str] = []
    i = start + 1
    while i < len(lines) and not _closes_fence(lines[i], fence):
        content.append(_strip_indent(lines[i], fence.indent))
        i += 1
    return CodeBlock(info=fence.info, code="\n".join(content)), min(i + 1, len(lines))


def _strip_indent(line: str, width: int) -> str:
    """Remove up to `width` leading spaces."""
    removable = len(line) - len(line.lstrip(" "))
    return line[min(width, removable) :]


def _parse_quote(lines: list[str], start: int) -> tuple[BlockQuote, int]:
    """A block quote (rule R6): the unquoted lines are parsed as blocks."""
    inner: list[str] = []
    i = start
    while i < len(lines) and (match := _QUOTE.match(lines[i])) is not None:
        inner.append(match.group(1))
        i += 1
    return BlockQuote(_parse_blocks(inner)), i


def _parse_paragraph(lines: list[str], start: int) -> tuple[Paragraph, int]:
    """A paragraph (rule R3): lines up to a blank line or the start of a block."""
    collected = [lines[start].lstrip()]
    i = start + 1
    while i < len(lines) and not _is_blank(lines[i]):
        if _interrupts_paragraph(lines[i]):
            break
        collected.append(lines[i].lstrip())
        i += 1
    return Paragraph("\n".join(collected).rstrip()), i


# Lists -----------------------------------------------------------------------


@dataclass
class _Marker:
    """A parsed list marker line."""

    indent: int
    kind: str  # the bullet character, or "." / ")" for ordered lists
    number: int  # the item number; 1 for bullets
    offset: int  # the column where the item's text starts
    text: str

    @property
    def ordered(self) -> bool:
        return self.kind in ".)"


def _marker(line: str) -> _Marker | None:
    """The list marker at the start of `line`, or None (thematic breaks excluded)."""
    match = _LIST_MARKER.match(line)
    if match is None or _is_thematic_break(line):
        return None
    indent, token, gap, text = match.groups()
    kind = token[-1] if token[-1] in ".)" else token
    width = len(gap) if 1 <= len(gap) <= _MAX_MARKER_GAP else 1
    number = int(token[:-1]) if kind in ".)" else 1
    return _Marker(len(indent), kind, number, len(indent) + len(token) + width, text)


@dataclass
class _ItemDraft:
    """An item being collected: its text lines and its nested marker lines."""

    marker: _Marker
    lines: list[str] = field(default_factory=list)
    sub_markers: list[_Marker] = field(default_factory=list)
    sub_lines: list[list[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.lines.append(self.marker.text)

    def nest(self, marker: _Marker) -> None:
        self.sub_markers.append(marker)
        self.sub_lines.append([marker.text])

    def continue_with(self, text: str) -> None:
        """Add a continuation line to the last nested item, or to the item itself."""
        (self.sub_lines[-1] if self.sub_lines else self.lines).append(text)

    def build(self) -> ListItem:
        return ListItem(_join(self.lines), self._build_sublist())

    def _build_sublist(self) -> ListBlock | None:
        if not self.sub_markers:
            return None
        first = self.sub_markers[0]
        items = [ListItem(_join(lines)) for lines in self.sub_lines]
        return ListBlock(first.ordered, first.number, items)


def _join(lines: list[str]) -> str:
    return "\n".join(line.lstrip() for line in lines).rstrip()


def _parse_list(lines: list[str], start: int, first: _Marker) -> tuple[ListBlock, int]:
    """A list with its items (rules R7 to R9)."""
    drafts = [_ItemDraft(first)]
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if _is_blank(line):
            # Only another item of this list carries the list across a blank line.
            following = _next_marker(lines, i)
            if following is None or not _continues(following, first, drafts[-1]):
                break
            i = _next_nonblank(lines, i)
            continue
        marker = _marker(line)
        if marker is None:
            if _interrupts_list_item(line):
                break
            drafts[-1].continue_with(line)
        elif marker.indent >= drafts[-1].marker.offset:
            drafts[-1].nest(marker)
        elif marker.kind == first.kind:
            drafts.append(_ItemDraft(marker))
        else:
            break
        i += 1
    items = [draft.build() for draft in drafts]
    return ListBlock(first.ordered, first.number, items), i


def _continues(marker: _Marker, first: _Marker, current: _ItemDraft) -> bool:
    """Whether `marker` (after a blank line) belongs to the list being built."""
    return marker.indent >= current.marker.offset or marker.kind == first.kind


def _next_nonblank(lines: list[str], start: int) -> int:
    i = start
    while i < len(lines) and _is_blank(lines[i]):
        i += 1
    return i


def _next_marker(lines: list[str], start: int) -> _Marker | None:
    """The marker on the first non-blank line at or after `start`, if it has one."""
    i = _next_nonblank(lines, start)
    return _marker(lines[i]) if i < len(lines) else None
