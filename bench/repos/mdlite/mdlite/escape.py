"""HTML escaping and URL scheme checks.

Every character that reaches the output goes through `escape_text` or `escape_attr`,
and every url goes through `is_safe_url` before it becomes an `href` or `src`. No
other module escapes or checks anything, so a rule change is made here once.
"""

import re

_TEXT_TABLE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
_ATTR_TABLE = str.maketrans(
    {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
)

# Browsers ignore whitespace and control characters inside a url scheme
# ("java\tscript:"), so they are deleted before the scheme is read.
_IGNORED_IN_SCHEME = re.compile(r"[\x00-\x20\x7f]")
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*):")

SAFE_SCHEMES = frozenset({"http", "https", "mailto"})


def escape_text(text: str) -> str:
    """Escape `&`, `<` and `>` for use as element content."""
    return text.translate(_TEXT_TABLE)


def escape_attr(text: str) -> str:
    """Escape `&`, `<`, `>`, `"` and `'` for use inside a quoted attribute value."""
    return text.translate(_ATTR_TABLE)


def url_scheme(url: str) -> str | None:
    """The lowercase scheme of `url`, or None when it has no scheme.

    Whitespace and control characters are ignored first, so `" JaVa\\nScript:x"`
    has the scheme `javascript`.
    """
    match = _SCHEME.match(_IGNORED_IN_SCHEME.sub("", url))
    return match.group(1).lower() if match else None


def is_safe_url(url: str) -> bool:
    """True for urls without a scheme and for http, https and mailto urls."""
    scheme = url_scheme(url)
    return scheme is None or scheme in SAFE_SCHEMES
