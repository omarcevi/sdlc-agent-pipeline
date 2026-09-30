"""Block structure: what the block parser and the renderer make of whole lines."""

import pytest
from mdlite import to_html
from mdlite.blocks import parse
from mdlite.nodes import (
    BlockQuote,
    CodeBlock,
    Heading,
    ListBlock,
    ListItem,
    Paragraph,
    ThematicBreak,
)


def test_line_endings_and_leading_tabs_are_normalised():
    """R1: CRLF and CR are newlines; leading tabs expand to 4-column stops."""
    assert to_html("a\r\nb\rc") == "<p>a\nb\nc</p>\n"
    assert to_html("\t- a") == "<p>- a</p>\n"  # 4 columns: too deep to be a list
    assert to_html("- a\n\t- b") == (
        "<ul>\n<li>a\n<ul>\n<li>b</li>\n</ul>\n</li>\n</ul>\n"
    )


def test_tabs_after_the_first_character_are_kept():
    """R1: tabs that are not leading whitespace are left alone."""
    assert to_html("a\tb") == "<p>a\tb</p>\n"
    assert to_html("-\ta") == "<p>-\ta</p>\n"


def test_empty_and_blank_documents_render_empty():
    """R1: the empty document (and a blank one) renders as the empty string."""
    assert to_html("") == ""
    assert to_html(" \n\t\n  \n") == ""


def test_atx_heading_levels():
    """R2: one to six hashes make h1 to h6."""
    html = to_html("# One\n\n###### Six")
    assert html == '<h1 id="one">One</h1>\n<h6 id="six">Six</h6>\n'


def test_atx_needs_a_space_and_at_most_six_hashes():
    """R2: seven hashes, or no space after the hashes, make a paragraph."""
    assert to_html("####### Seven") == "<p>####### Seven</p>\n"
    assert to_html("#hashtag") == "<p>#hashtag</p>\n"


def test_atx_closing_hashes_and_indent():
    """R2: a closing run of hashes is dropped; 4 spaces of indent is no heading."""
    assert parse("## Title ##  ").children == [Heading(2, "Title")]
    assert parse("# C#").children == [Heading(1, "C#")]
    assert parse("# #").children == [Heading(1, "")]
    assert parse("#").children == [Heading(1, "")]
    assert parse("   # Three").children == [Heading(1, "Three")]
    assert parse("    # Four").children == [Paragraph("# Four")]


def test_paragraphs_join_lines_and_split_on_blank_lines():
    """R3: consecutive lines form one paragraph, a blank line ends it."""
    assert to_html("one\n   two\n\nthree") == "<p>one\ntwo</p>\n<p>three</p>\n"


def test_blocks_interrupt_paragraphs():
    """R3: headings, fences, quotes, thematic breaks and lists end a paragraph."""
    source = "p1\n# H\np2\n```\nc\n```\np3\n> q\np4\n***\np5\n- i"
    kinds = [type(block).__name__ for block in parse(source).children]
    assert kinds == [
        "Paragraph",
        "Heading",
        "Paragraph",
        "CodeBlock",
        "Paragraph",
        "BlockQuote",
        "Paragraph",
        "ThematicBreak",
        "Paragraph",
        "ListBlock",
    ]


def test_deep_indent_is_paragraph_text_not_code():
    """R3: there are no indented code blocks; leading whitespace is dropped."""
    assert to_html("    not code\n") == "<p>not code</p>\n"


@pytest.mark.parametrize(
    "source", ["---", "***", "___", "- - -", "  * * *", "_ _ _ _", "-----"]
)
def test_thematic_breaks(source):
    """R4: three or more of one of - * _, spaces allowed between, make an hr."""
    assert parse(source).children == [ThematicBreak()]
    assert to_html(source) == "<hr>\n"


@pytest.mark.parametrize("source", ["--", "-*-", "- * _", "    ---x"])
def test_not_thematic_breaks(source):
    """R4: two characters, mixed characters or trailing text are no hr."""
    assert ThematicBreak() not in parse(source).children


def test_fenced_code_is_literal_and_escaped():
    """R5: fence content is taken literally and escaped; info gives the class."""
    source = "```python extra\nprint(1)\n  <x> & *y*\n# not a heading\n```"
    assert to_html(source) == (
        '<pre><code class="language-python">print(1)\n'
        "  &lt;x&gt; &amp; *y*\n# not a heading\n</code></pre>\n"
    )


def test_fence_closing_rules():
    """R5: the closing fence matches the character, is long enough, and is bare."""
    assert parse("````\n```\n````").children == [CodeBlock("", "```")]
    assert parse("~~~\n```\n~~~").children == [CodeBlock("", "```")]
    assert parse("```\na\n``` b\n```").children == [CodeBlock("", "a\n``` b")]
    assert parse("```\n~~~\n```").children == [CodeBlock("", "~~~")]


def test_unclosed_fence_runs_to_the_end():
    """R5: an unclosed fence takes the rest of the document."""
    assert parse("```\nabc\n\ndef").children == [CodeBlock("", "abc\n\ndef")]


def test_fence_indent_is_removed_from_content_lines():
    """R5: content loses up to as many spaces as the opening fence had."""
    source = " ```\n  a\n b\nc\n ```"
    assert parse(source).children == [CodeBlock("", " a\nb\nc")]


def test_empty_fence_and_backtick_info_rule():
    """R5: an empty block has no text; a backtick fence's info has no backtick."""
    assert to_html("```\n```") == "<pre><code></code></pre>\n"
    assert not any(isinstance(b, CodeBlock) for b in parse("``` a`b").children)
    assert parse("~~~ a`b\nx\n~~~").children == [CodeBlock("a`b", "x")]


def test_block_quote_contents_are_blocks():
    """R6: quoted lines lose one `>` and a space and are parsed as blocks."""
    source = "> # T\n> a\n> b\n>\n> - x\n> - y"
    assert to_html(source) == (
        "<blockquote>\n"
        '<h1 id="t">T</h1>\n<p>a\nb</p>\n<ul>\n<li>x</li>\n<li>y</li>\n</ul>\n'
        "</blockquote>\n"
    )


def test_block_quote_ends_without_lazy_continuation():
    """R6: a line without `>` ends the quote; quotes nest."""
    assert to_html("> a\nb") == "<blockquote>\n<p>a</p>\n</blockquote>\n<p>b</p>\n"
    assert parse("> > deep").children == [BlockQuote([BlockQuote([Paragraph("deep")])])]
    assert parse(">no space").children == [BlockQuote([Paragraph("no space")])]


def test_list_markers_and_kinds():
    """R7: same marker kind is one list; another bullet or delimiter is a new list."""
    assert len(parse("- a\n- b").children) == 1
    assert len(parse("- a\n* b").children) == 2
    assert len(parse("* a\n+ b").children) == 2
    assert len(parse("1. a\n2. b").children) == 1
    assert len(parse("1. a\n2) b").children) == 2
    assert len(parse("- a\n1. b").children) == 2


def test_list_needs_a_space_after_the_marker():
    """R7: `-a` is text; a bare `-` is an empty item; 10 digits are too many."""
    assert to_html("-a") == "<p>-a</p>\n"
    assert to_html("-") == "<ul>\n<li></li>\n</ul>\n"
    assert to_html("1234567890. x") == "<p>1234567890. x</p>\n"
    assert to_html("123456789. x") == '<ol start="123456789">\n<li>x</li>\n</ol>\n'


def test_ordered_list_start_number():
    """R7: start is the first item's number and is shown only when not 1."""
    assert to_html("3. x\n4. y") == '<ol start="3">\n<li>x</li>\n<li>y</li>\n</ol>\n'
    assert to_html("1) x") == "<ol>\n<li>x</li>\n</ol>\n"
    assert to_html("0. x") == '<ol start="0">\n<li>x</li>\n</ol>\n'
    assert to_html("5. a\n1. b") == '<ol start="5">\n<li>a</li>\n<li>b</li>\n</ol>\n'


def test_blank_line_between_items_keeps_one_tight_list():
    """R7: a blank line between items does not end the list or add paragraphs."""
    assert to_html("- a\n\n- b") == "<ul>\n<li>a</li>\n<li>b</li>\n</ul>\n"
    assert to_html("- a\n\npara") == "<ul>\n<li>a</li>\n</ul>\n<p>para</p>\n"


def test_list_item_continuation_lines_join_the_item():
    """R8: lines that are not markers or block starts continue the item."""
    assert parse("- a\nb\n      c\n- d").children == [
        ListBlock(False, 1, [ListItem("a\nb\nc"), ListItem("d")])
    ]


def test_block_starts_end_a_list_item():
    """R8: quotes, headings, fences and breaks end the list; so does a stray line."""
    assert to_html("- a\n> q") == (
        "<ul>\n<li>a</li>\n</ul>\n<blockquote>\n<p>q</p>\n</blockquote>\n"
    )
    assert to_html("- a\n# H") == '<ul>\n<li>a</li>\n</ul>\n<h1 id="h">H</h1>\n'
    assert to_html("- a\n---") == "<ul>\n<li>a</li>\n</ul>\n<hr>\n"
    assert to_html("- a\n\n  after a blank") == (
        "<ul>\n<li>a</li>\n</ul>\n<p>after a blank</p>\n"
    )


def test_nested_list_under_an_item():
    """R9: a marker indented to the item's text column nests under the item."""
    assert to_html("- a\n  - b\n  - c\n- d") == (
        "<ul>\n<li>a\n<ul>\n<li>b</li>\n<li>c</li>\n</ul>\n</li>\n<li>d</li>\n</ul>\n"
    )
    assert to_html("1. a\n   - b") == (
        "<ol>\n<li>a\n<ul>\n<li>b</li>\n</ul>\n</li>\n</ol>\n"
    )


def test_nesting_needs_the_text_column():
    """R9: a less indented marker is a sibling (same kind) or ends the list."""
    assert parse("1. a\n  - b").children == [
        ListBlock(True, 1, [ListItem("a")]),
        ListBlock(False, 1, [ListItem("b")]),
    ]
    assert parse("- a\n - b").children == [
        ListBlock(False, 1, [ListItem("a"), ListItem("b")])
    ]
    assert parse("-   a\n  - b").children == [
        ListBlock(False, 1, [ListItem("a"), ListItem("b")])
    ]


def test_sublist_is_one_level_of_one_type():
    """R9: deeper markers join the sublist, whose type is the first one's."""
    sub = ListBlock(True, 1, [ListItem("b"), ListItem("c"), ListItem("d")])
    assert parse("- a\n  1. b\n  - c\n    - d").children == [
        ListBlock(False, 1, [ListItem("a", sub)])
    ]


def test_continuation_after_a_nested_item_and_blank_before_nested():
    """R9: continuation joins the nested item; a blank line may precede nesting."""
    nested = ListBlock(False, 1, [ListItem("b\nmore")])
    assert parse("- a\n  - b\n    more").children == [
        ListBlock(False, 1, [ListItem("a", nested)])
    ]
    nested = ListBlock(False, 1, [ListItem("b")])
    assert parse("- a\n\n  - b").children == [
        ListBlock(False, 1, [ListItem("a", nested)])
    ]
