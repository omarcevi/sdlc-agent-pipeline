import pytest
from mdlite import to_html


def test_issue_example_shows_the_address_without_brackets():
    source = "Or install from source: <git+https://github.com/acme/tool.git>"
    assert to_html(source) == (
        "<p>Or install from source: git+https://github.com/acme/tool.git</p>\n"
    )


@pytest.mark.parametrize(
    "url",
    [
        "x-man-page://ls",
        "com.example.app:settings",
        "svn+ssh://svn.example.com/repo",
        "web+app:open",
    ],
)
def test_schemes_with_plus_dot_or_dash_are_autolinks(url):
    assert to_html(f"see <{url}> now") == f"<p>see {url} now</p>\n"


def test_linked_autolinks_are_unchanged():
    assert to_html("<https://example.com/a+b-c.d>") == (
        '<p><a href="https://example.com/a+b-c.d">https://example.com/a+b-c.d</a></p>\n'
    )
    assert to_html("<mailto:dev@example.com>") == (
        '<p><a href="mailto:dev@example.com">mailto:dev@example.com</a></p>\n'
    )


def test_links_and_images_with_such_schemes_are_not_linked():
    assert to_html("[install](git+https://github.com/acme/tool.git)") == (
        "<p>install</p>\n"
    )
    assert to_html("![icon](web+app:icon.png)") == "<p>icon</p>\n"
    assert to_html("[settings](com.example.app:settings)") == "<p>settings</p>\n"
