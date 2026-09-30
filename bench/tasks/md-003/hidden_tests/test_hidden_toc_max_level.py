import re

from mdlite import to_html, toc

DOC = """# Guide

Intro text.

## Install

### From source

#### Build flags

## Usage

### Options
"""


def _html_ids(text):
    return re.findall(r'<h[1-6] id="([^"]*)"', to_html(text))


def test_max_level_keeps_only_the_top_levels():
    assert toc(DOC, max_level=2) == [
        (1, "Guide", "guide"),
        (2, "Install", "install"),
        (2, "Usage", "usage"),
    ]
    assert toc(DOC, max_level=1) == [(1, "Guide", "guide")]


def test_without_max_level_every_heading_is_listed():
    everything = [
        (1, "Guide", "guide"),
        (2, "Install", "install"),
        (3, "From source", "from-source"),
        (4, "Build flags", "build-flags"),
        (2, "Usage", "usage"),
        (3, "Options", "options"),
    ]
    assert toc(DOC) == everything
    assert toc(DOC, max_level=6) == everything


def test_slugs_stay_the_html_ids_when_deeper_headings_repeat_a_title():
    text = "# Setup\n\n### Setup\n\n## Setup\n\n### Setup\n\n## Setup"
    assert toc(text, max_level=2) == [
        (1, "Setup", "setup"),
        (2, "Setup", "setup-2"),
        (2, "Setup", "setup-4"),
    ]
    listed = [slug for _, _, slug in toc(text, max_level=2)]
    assert set(listed) <= set(_html_ids(text))


def test_skipped_heading_whose_slug_collides_with_a_later_one():
    text = "### Notes\n\n## Notes\n\n# Notes 1\n\n## notes!"
    ids = _html_ids(text)
    assert ids == ["notes", "notes-1", "notes-1-1", "notes-2"]
    assert toc(text, max_level=2) == [
        (2, "Notes", "notes-1"),
        (1, "Notes 1", "notes-1-1"),
        (2, "notes!", "notes-2"),
    ]


def test_quoted_headings_are_counted_and_listed():
    text = "## FAQ\n\n> # FAQ\n> ### FAQ\n\n# FAQ"
    assert toc(text, max_level=1) == [(1, "FAQ", "faq-1"), (1, "FAQ", "faq-3")]
    assert _html_ids(text) == ["faq", "faq-1", "faq-2", "faq-3"]
