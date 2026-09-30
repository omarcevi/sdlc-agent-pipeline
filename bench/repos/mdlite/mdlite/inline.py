"""Inline parser: turns the raw text of one block into inline nodes.

`parse_inline` scans left to right. Code spans, backslash escapes, links, images and
autolinks are recognised as the scanner meets them; runs of `*` and `_` are kept as
delimiter markers and paired afterwards (`_pair_emphasis`). Because the scanner
consumes a whole code span at once, delimiters inside a code span can never pair with
delimiters outside it.

`plain_text` flattens parsed inlines back to text; the renderer uses it for image alt
text and the table of contents and the slug builder use it for headings.

The rules are numbered R11 to R17 in the README.
"""

import re
import string
from dataclasses import dataclass

from mdlite.escape import url_scheme
from mdlite.nodes import (
    Code,
    Emphasis,
    Image,
    Inline,
    LineBreak,
    Link,
    Strong,
    Text,
)

_ESCAPABLE = frozenset(string.punctuation)
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_BACKTICK_RUN = re.compile(r"`+")
_AUTOLINK_FORBIDDEN = re.compile(r"[\s<]")
_EMAIL = re.compile(r"^[^\s<>@]+@[^\s<>@]+$")
MAX_IMAGE_DEPTH = 10
MAX_EMPHASIS_DEPTH = 20
_MIN_SCHEME = 2
_MAX_SCHEME = 32


@dataclass
class _Delimiter:
    """A run of `*` or `_` waiting to be paired (rule R14)."""

    char: str
    count: int
    can_open: bool
    can_close: bool


_Node = Inline | _Delimiter


def parse_inline(
    text: str, *, allow_links: bool = True, image_depth: int = 0
) -> list[Inline]:
    """Parse inline markup in `text`.

    With `allow_links=False` a `[` or `<` never starts a link or autolink; this is
    how the label of a link is parsed, since links do not nest (rules R15, R17).
    `image_depth` is how many image labels enclose `text`; at `MAX_IMAGE_DEPTH` a
    further `![` is literal (rule R22).
    """
    return _Scanner(text, allow_links, image_depth).scan()


def plain_text(nodes: list[Inline]) -> str:
    """The text of `nodes` without markup: code content, link text, image labels."""
    parts: list[str] = []
    for node in nodes:
        match node:
            case Text(value) | Code(value):
                parts.append(value)
            case Emphasis(children) | Strong(children):
                parts.append(plain_text(children))
            case Link(children) | Image(children):
                parts.append(plain_text(children))
            case LineBreak():
                parts.append("\n")
    return "".join(parts)


def unescape(text: str) -> str:
    """Resolve backslash escapes (rule R11)."""
    return _ESCAPE.sub(r"\1", text)


class _Scanner:
    """One pass over the text of a block."""

    def __init__(self, text: str, allow_links: bool, image_depth: int) -> None:
        self.text = text
        self.allow_links = allow_links
        self.image_depth = image_depth
        self.pos = 0
        self.nodes: list[_Node] = []
        self.buffer: list[str] = []

    def scan(self) -> list[Inline]:
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == "\\":
                self._backslash()
            elif char == "\n":
                self._newline()
            elif char == "`":
                self._code_span()
            elif char in "*_":
                self._delimiter_run()
            elif char == "[" and self.allow_links:
                self._bracket(image=False)
            elif (
                char == "!"
                and self.text.startswith("![", self.pos)
                and self.image_depth < MAX_IMAGE_DEPTH
            ):
                self._bracket(image=True)
            elif char == "<" and self.allow_links:
                self._angle()
            else:
                self._literal(char)
        self._flush()
        return _finish(_pair_emphasis(self.nodes))

    # Helpers -----------------------------------------------------------------

    def _literal(self, chars: str) -> None:
        self.buffer.append(chars)
        self.pos += len(chars)

    def _flush(self) -> None:
        if self.buffer:
            self.nodes.append(Text("".join(self.buffer)))
            self.buffer = []

    def _emit(self, node: _Node) -> None:
        self._flush()
        self.nodes.append(node)

    # Scanner steps -----------------------------------------------------------

    def _backslash(self) -> None:
        """A backslash escape (R11) or a backslash-newline hard break (R12)."""
        following = self.text[self.pos + 1 : self.pos + 2]
        if following and following in _ESCAPABLE:
            self.buffer.append(following)
            self.pos += 2
        elif following == "\n" and self.pos + 2 < len(self.text):
            self._emit(LineBreak())
            self.pos += 2
        else:
            self._literal("\\")

    def _newline(self) -> None:
        """A soft break, or a hard break after two or more spaces (rule R12)."""
        pending = "".join(self.buffer)
        content = pending.rstrip(" ")
        hard = len(pending) - len(content) >= 2 and self.pos + 1 < len(self.text)
        self.buffer = [content] if content else []
        if hard:
            self._emit(LineBreak())
        else:
            self.buffer.append("\n")
        self.pos += 1

    def _code_span(self) -> None:
        """A code span (rule R13), or literal backticks when it never closes."""
        span = _find_code_span(self.text, self.pos)
        if span is None:
            self._literal("`" * _run_length(self.text, self.pos))
            return
        content, end = span
        self._emit(Code(content))
        self.pos = end

    def _delimiter_run(self) -> None:
        """A run of `*` or `_`, classified for pairing (rule R14)."""
        char = self.text[self.pos]
        end = self.pos
        while end < len(self.text) and self.text[end] == char:
            end += 1
        before = self.text[self.pos - 1] if self.pos > 0 else " "
        after = self.text[end] if end < len(self.text) else " "
        can_open = not after.isspace()
        can_close = not before.isspace()
        if char == "_":
            can_open = can_open and not before.isalnum()
            can_close = can_close and not after.isalnum()
        self._emit(_Delimiter(char, end - self.pos, can_open, can_close))
        self.pos = end

    def _bracket(self, *, image: bool) -> None:
        """A link or image (rule R15); the bracket is literal if it does not parse."""
        open_at = self.pos + (1 if image else 0)
        parsed = _parse_link_tail(self.text, open_at)
        if parsed is None:
            self._literal("![" if image else "[")
            return
        label, url, title, end = parsed
        children = parse_inline(
            label,
            allow_links=False,
            image_depth=self.image_depth + (1 if image else 0),
        )
        self._emit(Image(children, url, title) if image else Link(children, url, title))
        self.pos = end

    def _angle(self) -> None:
        """An autolink (rule R17), or a literal `<`."""
        close = self.text.find(">", self.pos + 1)
        candidate = self.text[self.pos + 1 : close] if close != -1 else ""
        if candidate and not _AUTOLINK_FORBIDDEN.search(candidate):
            scheme = url_scheme(candidate)
            if scheme is not None and _MIN_SCHEME <= len(scheme) <= _MAX_SCHEME:
                self._emit(Link([Text(candidate)], candidate))
                self.pos = close + 1
                return
            if _EMAIL.match(candidate):
                self._emit(Link([Text(candidate)], "mailto:" + candidate))
                self.pos = close + 1
                return
        self._literal("<")


# Code spans ------------------------------------------------------------------


def _find_code_span(text: str, start: int) -> tuple[str, int] | None:
    """The code span opening at `start`: (content, index after the closing run).

    The span closes at the next backtick run of exactly the opening length.
    """
    length = _run_length(text, start)
    for run in _BACKTICK_RUN.finditer(text, start + length):
        if len(run.group(0)) == length:
            return _code_content(text[start + length : run.start()]), run.end()
    return None


def _run_length(text: str, start: int) -> int:
    """The number of backticks in the run that starts at `start`."""
    end = start
    while end < len(text) and text[end] == "`":
        end += 1
    return end - start


def _code_content(raw: str) -> str:
    """Newlines become spaces; one space is trimmed from each end if both exist."""
    content = raw.replace("\n", " ")
    if len(content) >= 2 and content[0] == " " and content[-1] == " ":
        if content.strip(" "):
            return content[1:-1]
    return content


# Links -----------------------------------------------------------------------


def _find_label_end(text: str, start: int) -> int | None:
    """The index of the `]` that closes the `[` at `start`.

    Backslash-escaped brackets and brackets inside code spans do not count; other
    brackets must balance.
    """
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "`":
            span = _find_code_span(text, i)
            i = span[1] if span is not None else i + _run_length(text, i)
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _parse_link_tail(
    text: str, open_at: int
) -> tuple[str, str, str | None, int] | None:
    """Parse `[label](destination "title")` starting at the `[` at `open_at`.

    Returns (label source, url, title, index after the closing paren), or None.
    """
    label_end = _find_label_end(text, open_at)
    if label_end is None or text[label_end + 1 : label_end + 2] != "(":
        return None
    destination = _parse_destination(text, label_end + 2)
    if destination is None:
        return None
    url, title, end = destination
    return text[open_at + 1 : label_end], url, title, end


def _skip_spaces(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t\n":
        pos += 1
    return pos


def _parse_destination(text: str, pos: int) -> tuple[str, str | None, int] | None:
    """Parse `url`, `<url>` and an optional title up to the closing paren."""
    pos = _skip_spaces(text, pos)
    if text.startswith("<", pos):
        close = _find_angle_end(text, pos)
        if close is None:
            return None
        url, pos = text[pos + 1 : close], close + 1
    else:
        start, depth = pos, 0
        while pos < len(text):
            char = text[pos]
            if char == "\\":
                pos += 2
                continue
            if char.isspace():
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            pos += 1
        url = text[start : min(pos, len(text))]
    pos = _skip_spaces(text, pos)
    title = None
    if pos < len(text) and text[pos] in "\"'":
        title, pos = _parse_title(text, pos)
        if title is None:
            return None
        pos = _skip_spaces(text, pos)
    if not text.startswith(")", pos):
        return None
    return unescape(url), None if title is None else unescape(title), pos + 1


def _find_angle_end(text: str, start: int) -> int | None:
    """The index of the `>` closing the `<...>` url at `start`.

    `\\>` does not close it; a `<` or a newline inside means there is no url.
    """
    i = start + 1
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == ">":
            return i
        if char in "<\n":
            return None
        i += 1
    return None


def _parse_title(text: str, pos: int) -> tuple[str | None, int]:
    """Parse a quoted title starting at `pos`; (None, pos) if it is not closed."""
    quote = text[pos]
    i = pos + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return text[pos + 1 : i], i + 1
        i += 1
    return None, pos


# Emphasis --------------------------------------------------------------------


def _pair_emphasis(nodes: list[_Node]) -> list[_Node]:
    """Pair delimiter runs into Emphasis and Strong nodes (rule R14).

    Each closing run pairs with the nearest earlier opening run of the same
    character. Two characters from each side make Strong, otherwise Emphasis. Runs
    left over after a pairing stay in place and may pair again (`***x***`).
    """
    i = 0
    while i < len(nodes):
        closer = nodes[i]
        opener_at = _find_opener(nodes, i) if _is_closer(closer) else None
        if opener_at is None:
            i += 1
            continue
        opener = nodes[opener_at]
        assert isinstance(opener, _Delimiter) and isinstance(closer, _Delimiter)
        inner = _finish(nodes[opener_at + 1 : i])
        if _emphasis_depth(inner) >= MAX_EMPHASIS_DEPTH:
            closer.can_open = False  # nested too deeply: it stays text for good
            i += 1
            continue
        used = 2 if opener.count >= 2 and closer.count >= 2 else 1
        wrapper = (Strong if used == 2 else Emphasis)(inner)
        opener.count -= used
        closer.count -= used
        replacement: list[_Node] = [opener] if opener.count else []
        replacement.append(wrapper)
        if closer.count:
            replacement.append(closer)
        nodes[opener_at : i + 1] = replacement
        i = opener_at + len(replacement) - (1 if closer.count else 0)
    return nodes


def _emphasis_depth(nodes: list[Inline]) -> int:
    """How deeply Emphasis and Strong nodes are nested in `nodes`."""
    return max(
        (
            1 + _emphasis_depth(node.children)
            for node in nodes
            if isinstance(node, Emphasis | Strong)
        ),
        default=0,
    )


def _is_closer(node: _Node) -> bool:
    return isinstance(node, _Delimiter) and node.can_close


def _find_opener(nodes: list[_Node], closer_at: int) -> int | None:
    closer = nodes[closer_at]
    assert isinstance(closer, _Delimiter)
    for i in range(closer_at - 1, -1, -1):
        node = nodes[i]
        if isinstance(node, _Delimiter) and node.char == closer.char and node.can_open:
            return i
    return None


def _finish(nodes: list[_Node]) -> list[Inline]:
    """Turn unpaired delimiter runs into text and merge neighbouring text nodes."""
    result: list[Inline] = []
    for node in nodes:
        piece = Text(node.char * node.count) if isinstance(node, _Delimiter) else node
        if isinstance(piece, Text):
            if not piece.value:
                continue
            if result and isinstance(result[-1], Text):
                result[-1] = Text(result[-1].value + piece.value)
                continue
        result.append(piece)
    return result
