import re

import pytest
from mdlite import to_html, toc


def _ids(text):
    return re.findall(r'<h[1-6] id="([^"]*)"', to_html(text))


def test_issue_example():
    assert to_html("## Installing on Linux {#install}") == (
        '<h2 id="install">Installing on Linux</h2>\n'
    )


def test_id_is_used_exactly_as_written_and_toc_agrees():
    text = "# *Fast* start {#Quick_Start-2}\n\n### Step two\t{#step_2}"
    assert to_html(text) == (
        '<h1 id="Quick_Start-2"><em>Fast</em> start</h1>\n'
        '<h3 id="step_2">Step two</h3>\n'
    )
    assert toc(text) == [(1, "Fast start", "Quick_Start-2"), (3, "Step two", "step_2")]


def test_automatic_ids_never_repeat_an_explicit_id_used_earlier():
    text = "# Getting started {#setup}\n\n## Setup\n\n## Setup\n\n## Other"
    assert _ids(text) == ["setup", "setup-1", "setup-2", "other"]
    assert [slug for _, _, slug in toc(text)] == _ids(text)


def test_explicit_ids_inside_block_quotes():
    text = "> ## Quoted {#q}\n\n## Q"
    assert to_html(text) == (
        '<blockquote>\n<h2 id="q">Quoted</h2>\n</blockquote>\n<h2 id="q-1">Q</h2>\n'
    )
    assert toc(text) == [(2, "Quoted", "q"), (2, "Q", "q-1")]


@pytest.mark.parametrize(
    ("source", "html"),
    [
        ("## Title{#x}", '<h2 id="titlex">Title{#x}</h2>\n'),
        ("## Title {#x y}", '<h2 id="title-x-y">Title {#x y}</h2>\n'),
        ("## Title {#x} more", '<h2 id="title-x-more">Title {#x} more</h2>\n'),
        ("## Title {#café}", '<h2 id="title-café">Title {#café}</h2>\n'),
        ("## Title {#}", '<h2 id="title">Title {#}</h2>\n'),
    ],
)
def test_near_misses_stay_heading_text(source, html):
    assert to_html(source) == html


def test_only_headings_take_explicit_ids():
    assert to_html("Some text {#not-an-id}") == "<p>Some text {#not-an-id}</p>\n"
    assert to_html("- item {#x}") == "<ul>\n<li>item {#x}</li>\n</ul>\n"
