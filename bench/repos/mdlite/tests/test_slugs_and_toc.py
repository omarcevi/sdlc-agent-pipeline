"""Slugs, duplicate handling, heading ids and the table of contents."""

import pytest
from mdlite import to_html, toc
from mdlite.escape import escape_attr, escape_text, is_safe_url, url_scheme
from mdlite.slug import Slugger, slugify


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Hello, World!", "hello-world"),
        ("  Many   spaces -- and - dashes ", "many-spaces-and-dashes"),
        ("snake_case", "snakecase"),
        ("C++ & C#", "c-c"),
        ("2024 Plan", "2024-plan"),
        ("Café Ünï", "café-ünï"),
        ("日本語", "日本語"),
    ],
)
def test_slugify(text, slug):
    """R18: lowercase, drop punctuation, collapse whitespace and hyphens."""
    assert slugify(text) == slug


def test_empty_slug_becomes_section():
    """R18: a heading with nothing slug-worthy gets the slug `section`."""
    assert slugify("!!!") == "section"
    assert slugify("") == "section"
    assert slugify(" - ") == "section"


def test_slug_uses_plain_text_of_markup():
    """R18: markers are dropped; code, link text, image alt and escapes count."""
    source = "# A *b* `c` [d](/e) ![f](/g) \\* h"
    assert toc(source) == [(1, "A b c d f * h", "a-b-c-d-f-h")]


def test_duplicate_slugs_get_numbered_suffixes():
    """R19: the k-th repeat gets `-k`."""
    slugger = Slugger()
    assert [slugger.unique("Intro") for _ in range(3)] == [
        "intro",
        "intro-1",
        "intro-2",
    ]
    assert slugger.unique("Other") == "other"


def test_suffixed_slugs_skip_names_already_taken():
    """R19: a generated slug never equals one already in use."""
    slugger = Slugger()
    assert [slugger.unique(t) for t in ("a", "a", "a-1")] == ["a", "a-1", "a-1-1"]
    slugger = Slugger()
    assert [slugger.unique(t) for t in ("a-1", "a", "a")] == ["a-1", "a", "a-2"]


def test_duplicate_headings_get_unique_ids_in_html():
    """R19: the ids in the HTML are the deduplicated slugs."""
    html = to_html("# Intro\n## Intro\n### intro!")
    assert 'id="intro"' in html and 'id="intro-1"' in html and 'id="intro-2"' in html


def test_every_heading_has_an_id_including_quoted_ones():
    """R20: ids are assigned in document order, quoted headings included."""
    html = to_html("# A\n\n> ## A\n\n# A")
    assert html == (
        '<h1 id="a">A</h1>\n<blockquote>\n<h2 id="a-1">A</h2>\n</blockquote>\n'
        '<h1 id="a-2">A</h1>\n'
    )


def test_toc_entries():
    """R20: toc lists (level, plain text, slug) in document order."""
    source = "# Title\n\nbody\n\n## Part *one*\n\n### `code` part\n\n## Part one"
    assert toc(source) == [
        (1, "Title", "title"),
        (2, "Part one", "part-one"),
        (3, "code part", "code-part"),
        (2, "Part one", "part-one-1"),
    ]


def test_toc_slugs_match_html_ids():
    """R20: toc slugs are exactly the ids of the rendered headings."""
    source = "# A b\n> # A b\n## [A](/x) B\n##### a-b\n# !!!\n# ???"
    ids = [slug for _, _, slug in toc(source)]
    html = to_html(source)
    assert [f'id="{slug}"' in html for slug in ids] == [True] * len(ids)
    assert ids == ["a-b", "a-b-1", "a-b-2", "a-b-3", "section", "section-1"]
    assert html.index('id="a-b-1"') < html.index('id="a-b-2"')


def test_toc_ignores_non_headings():
    """R20: only headings are listed; code blocks with # lines are not."""
    assert toc("```\n# no\n```\n\ntext\n#no") == []
    assert toc("") == []


def test_escape_functions():
    """R10: the two escape functions differ only in the quote characters."""
    assert escape_text('<a href="x">&\'') == '&lt;a href="x"&gt;&amp;\''
    assert escape_attr('<a href="x">&\'') == "&lt;a href=&quot;x&quot;&gt;&amp;&#39;"


def test_url_scheme_detection():
    """R16: the scheme is read after deleting whitespace and control characters."""
    assert url_scheme("HTTP://x") == "http"
    assert url_scheme(" java\tscript:x") == "javascript"
    assert url_scheme("/a:b") is None
    assert url_scheme("a/b:c") is None
    assert url_scheme("1http://x") is None
    assert url_scheme("") is None


def test_safe_url_decision():
    """R16: only scheme-less, http, https and mailto urls are safe."""
    assert all(
        is_safe_url(u) for u in ("", "/a", "#b", "//h/x", "HtTp://h", "mailto:x")
    )
    assert not any(
        is_safe_url(u)
        for u in (
            "javascript:x",
            "\x01javascript:x",
            "data:x",
            "file:///x",
            "localhost:1",
        )
    )


def test_scheme_detection_deletes_only_ascii_whitespace_and_controls():
    """R16: a non-ASCII space inside the scheme is not deleted, so none is found."""
    assert url_scheme("java\u2000script:x") is None
    assert url_scheme("java\u00a0script:x") is None
    assert url_scheme("java\x00\x7fscript:x") == "javascript"
    assert not is_safe_url("java\x1fscript:x")
