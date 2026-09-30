"""Inline markup: escapes, breaks, code spans, emphasis, links and autolinks."""

import pytest
from mdlite import to_html
from mdlite.inline import parse_inline, plain_text
from mdlite.nodes import Code, Emphasis, Image, LineBreak, Link, Strong, Text


def p(body: str) -> str:
    """The HTML of a one-paragraph document, without the paragraph tags."""
    html = to_html(body)
    assert html.startswith("<p>") and html.endswith("</p>\n"), html
    return html[3:-5]


def test_text_is_escaped():
    """R10: text escapes & < > but leaves quotes; raw html and entities are text."""
    assert p("<b>&amp; \"q\" 'x'</b>") == "&lt;b&gt;&amp;amp; \"q\" 'x'&lt;/b&gt;"


def test_code_span_is_escaped():
    """R10: code spans escape & < >."""
    assert p("`<a> & b`") == "<code>&lt;a&gt; &amp; b</code>"


def test_attribute_values_escape_quotes_too():
    """R10: href and title escape & < > and both kinds of quote."""
    html = p('[x](/a"b\'c&d "t\\"\'<")')
    assert html == '<a href="/a&quot;b&#39;c&amp;d" title="t&quot;&#39;&lt;">x</a>'


def test_backslash_escapes_punctuation():
    """R11: a backslash before ASCII punctuation makes it literal."""
    assert parse_inline("\\*not em\\*") == [Text("*not em*")]
    assert p("\\<b\\> \\[x\\](/u) \\`c\\`") == "&lt;b&gt; [x](/u) `c`"


def test_backslash_before_other_characters_is_literal():
    """R11: before a letter a backslash stays; `\\\\` is one backslash."""
    assert parse_inline("\\a \\\\ \\é") == [Text("\\a \\ \\é")]


def test_backslash_is_not_processed_in_code():
    """R11: code spans, code blocks and info strings keep backslashes."""
    assert p("`\\*`") == "<code>\\*</code>"
    assert to_html("```a\\*b\n\\*\n```") == (
        '<pre><code class="language-a\\*b">\\*\n</code></pre>\n'
    )


def test_escaped_delimiters_do_not_pair():
    """R11: an escaped * or _ never takes part in emphasis."""
    assert p("\\*a*") == "*a*"
    assert p("*a\\*") == "*a*"


def test_soft_and_hard_breaks():
    """R12: a newline is kept; two spaces or a backslash before it make <br>."""
    assert parse_inline("a\nb") == [Text("a\nb")]
    assert parse_inline("a \nb") == [Text("a\nb")]
    assert parse_inline("a  \nb") == [Text("a"), LineBreak(), Text("b")]
    assert parse_inline("a\\\nb") == [Text("a"), LineBreak(), Text("b")]
    assert p("a   \nb") == "a<br>\nb"


def test_breaks_at_the_end_of_a_block():
    """R12: trailing spaces go; a final backslash stays a backslash."""
    assert to_html("a  ") == "<p>a</p>\n"
    assert to_html("a\\") == "<p>a\\</p>\n"
    assert to_html("- a  \n  b") == "<ul>\n<li>a<br>\nb</li>\n</ul>\n"


def test_code_span_delimiters():
    """R13: the span closes at a run of the same length; inner backticks stay."""
    assert parse_inline("``a`b``") == [Code("a`b")]
    assert parse_inline("`a``b`") == [Code("a``b")]
    assert parse_inline("``a`") == [Text("``a`")]


def test_code_span_space_and_newline_handling():
    """R13: newlines become spaces; one space is trimmed from both ends."""
    assert parse_inline("`` `x` ``") == [Code("`x`")]
    assert parse_inline("` a`") == [Code(" a")]
    assert parse_inline("`  `") == [Code("  ")]
    assert parse_inline("`a\nb`") == [Code("a b")]


def test_code_span_contents_are_not_parsed():
    """R13: emphasis, brackets and links inside a span are literal."""
    assert p("`*a* [b](/c)`") == "<code>*a* [b](/c)</code>"


def test_code_span_binds_tighter_than_emphasis():
    """R13: a delimiter in a span cannot pair with one outside it."""
    assert p("*a `b*` c*") == "<em>a <code>b*</code> c</em>"
    assert p("*a `b* c`") == "*a <code>b* c</code>"
    assert p("`a*` and *b*") == "<code>a*</code> and <em>b</em>"


def test_emphasis_and_strong_forms():
    """R14: * and _ make em; doubled make strong; three make both."""
    assert p("*a*") == "<em>a</em>"
    assert p("_a_") == "<em>a</em>"
    assert p("**a**") == "<strong>a</strong>"
    assert p("__a__") == "<strong>a</strong>"
    assert p("***a***") == "<em><strong>a</strong></em>"
    assert parse_inline("**a *b* c**") == [
        Strong([Text("a "), Emphasis([Text("b")]), Text(" c")])
    ]


def test_delimiters_next_to_spaces_are_literal():
    """R14: a run followed by a space cannot open; one preceded by a space cannot close."""
    assert p("a * b * c") == "a * b * c"
    assert p("*a *") == "*a *"
    assert p("*\u00a0a*") == "*\u00a0a*"  # a no-break space is a space


def test_underscore_does_not_work_inside_words():
    """R14: `_` touching a letter or digit outside cannot open or close."""
    assert p("snake_case_name") == "snake_case_name"
    assert p("a_b_ c") == "a_b_ c"
    assert p("a*b*c") == "a<em>b</em>c"


def test_unbalanced_delimiters_stay_text():
    """R14: leftover delimiter characters are literal; pairing is nearest-first."""
    assert p("**a*") == "*<em>a</em>"
    assert p("*a**") == "<em>a</em>*"
    assert p("*a _b* c_") == "<em>a _b</em> c_"
    assert p("*a") == "*a"


def test_link_forms():
    """R15: `[text](url)`, with an optional title in double or single quotes."""
    assert parse_inline("[a](/u)") == [Link([Text("a")], "/u")]
    assert parse_inline('[a](/u "T")') == [Link([Text("a")], "/u", "T")]
    assert parse_inline("[a](/u 'T')") == [Link([Text("a")], "/u", "T")]
    assert parse_inline("[a]()") == [Link([Text("a")], "")]


def test_link_destinations():
    """R15: `<...>` urls may hold spaces; bare urls may hold balanced parentheses."""
    assert parse_inline("[a](<a b>)") == [Link([Text("a")], "a b")]
    assert parse_inline("[a](<b\\>c>)") == [Link([Text("a")], "b>c")]  # `\\>` is `>`
    assert parse_inline("[a](/x_(y))") == [Link([Text("a")], "/x_(y)")]
    assert parse_inline("[a](/x\\)y)") == [Link([Text("a")], "/x)y")]
    assert parse_inline('[a](/u "t\\"q")') == [Link([Text("a")], "/u", 't"q')]


def test_link_text_holds_markup_but_not_links():
    """R15: emphasis, code and images are allowed; an inner `[` is literal."""
    assert p("[a *b* `c`](/u)") == '<a href="/u">a <em>b</em> <code>c</code></a>'
    assert p("[a [b](/i) c](/o)") == '<a href="/o">a [b](/i) c</a>'
    assert p("[`]`](/u)") == '<a href="/u"><code>]</code></a>'
    assert p("[a\\]b](/u)") == '<a href="/u">a]b</a>'
    assert p("[<http://x>](/u)") == '<a href="/u">&lt;http://x&gt;</a>'
    assert p("[a <b@c.d>](/u)") == '<a href="/u">a &lt;b@c.d&gt;</a>'


def test_brackets_without_a_destination_are_text():
    """R15: no `(` right after `]`, or a broken destination, leaves literal text."""
    assert p("[a] (b)") == "[a] (b)"
    assert p("[a]") == "[a]"
    assert p("[a](b") == "[a](b"
    assert p("[a](b c)") == "[a](b c)"
    assert p('[a](b "t)') == '[a](b "t)'


def test_images():
    """R15: `![alt](url "title")`; alt is the plain text of the label."""
    assert parse_inline("![a *b*](/i.png)") == [
        Image([Text("a "), Emphasis([Text("b")])], "/i.png")
    ]
    assert p('![a *b*](/i.png "T")') == '<img src="/i.png" alt="a b" title="T">'
    assert p("[![a](/i.png)](/u)") == '<a href="/u"><img src="/i.png" alt="a"></a>'
    assert p("a ! [b](/u)") == 'a ! <a href="/u">b</a>'


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:x",
        "data:text/html,x",
        "vbscript:x",
        "ftp://h",
    ],
)
def test_unsafe_link_renders_as_text_only(url):
    """R16: a link with an unsafe scheme is just its text."""
    assert p(f"[*x*]({url})") == "<em>x</em>"


def test_unsafe_url_hidden_in_angle_brackets_and_images():
    """R16: whitespace inside the scheme is ignored; images fall back to alt text."""
    assert p("[x](<java\tscript:alert(1)>)") == "x"
    assert p("![a <b>](data:image/png;base64,AAAA)") == "a &lt;b&gt;"


@pytest.mark.parametrize(
    "url",
    ["/rel", "#anchor", "a/b:c", "//host/x", "http://h", "HTTPS://h", "mailto:a@b"],
)
def test_safe_urls_render_links(url):
    """R16: no scheme, http, https and mailto (any case) are rendered as links."""
    assert p(f"[x]({url})").startswith(f'<a href="{url}">')


def test_autolinks():
    """R17: `<scheme:rest>` and `<name@host>` become links showing their text."""
    assert p("<https://a.example/x?a=1&b=2>") == (
        '<a href="https://a.example/x?a=1&amp;b=2">https://a.example/x?a=1&amp;b=2</a>'
    )
    assert p("<mailto:a@b.c>") == '<a href="mailto:a@b.c">mailto:a@b.c</a>'
    assert p("<a@b.c>") == '<a href="mailto:a@b.c">a@b.c</a>'


def test_autolink_needs_a_valid_shape():
    """R17: spaces, a one-letter scheme, two @ or nothing inside leave a literal <."""
    assert p("<a b@c>") == "&lt;a b@c&gt;"
    assert p("<x:y>") == "&lt;x:y&gt;"
    assert p("<a@b@c>") == "&lt;a@b@c&gt;"
    assert p("<>") == "&lt;&gt;"
    ok, too_long = "a" * 32 + ":x", "a" * 33 + ":x"  # schemes have 2 to 32 characters
    assert parse_inline(f"<{ok}>") == [Link([Text(ok)], ok)]
    assert parse_inline(f"<{too_long}>") == [Text(f"<{too_long}>")]
    assert p("a < b") == "a &lt; b"


def test_autolink_with_unsafe_scheme_is_text():
    """R17: R16 applies to autolinks too."""
    assert p("<javascript:alert(1)>") == "javascript:alert(1)"


def test_plain_text_flattens_markup():
    """R18: the plain text drops markers and keeps code, link text and alt text."""
    nodes = parse_inline("A *b* **c** `d` [e](/u) ![f](/i) g\\*")
    assert plain_text(nodes) == "A b c d e f g*"


def test_autolinks_do_not_process_escapes():
    """R11: inside `<...>` an autolink keeps backslashes, and any `>` closes it."""
    assert p("<http://a\\*b>") == '<a href="http://a\\*b">http://a\\*b</a>'
    assert p("<http://a\\>b>") == '<a href="http://a\\">http://a\\</a>b&gt;'


def test_images_nest_to_a_fixed_depth():
    """R22: image labels nest 10 levels deep; deeper `![` are text; no input raises."""

    def depth(nodes):
        images = [n for n in nodes if isinstance(n, Image)]
        return 1 + max(depth(i.children) for i in images) if images else 0

    assert depth(parse_inline("![" * 12 + "a" + "](u)" * 12)) == 10
    assert depth(parse_inline("![" * 5 + "a" + "](u)" * 5)) == 5
    assert isinstance(to_html("![" * 50_000 + "a" + "](u)" * 50_000), str)


def test_emphasis_nesting_never_raises():
    """R22: emphasis nests 20 levels deep; leftover delimiters are text."""
    html = to_html("*" * 4_000 + "a" + "*" * 4_000)
    assert html.count("<strong>") == 20
    assert html.startswith("<p>" + "*" * 3_960 + "<strong>")
    assert isinstance(to_html("*_" * 2_000 + "a" + "_*" * 2_000), str)
