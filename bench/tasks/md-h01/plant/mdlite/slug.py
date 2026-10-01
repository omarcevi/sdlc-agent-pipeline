"""Heading slugs and duplicate handling."""

import re

_SEPARATORS = re.compile(r"[\s-]+")
EMPTY_SLUG = "section"


def slugify(text: str) -> str:
    """Turn heading text into a slug (README rule R18).

    Lowercase; keep letters, digits, whitespace and hyphens; collapse each run of
    whitespace and hyphens to one hyphen; trim hyphens from both ends.
    """
    kept = "".join(ch for ch in text.lower() if ch.isalnum() or ch in " -")
    return _SEPARATORS.sub("-", kept).strip("-") or EMPTY_SLUG


class Slugger:
    """Hands out unique slugs for the headings of one document (rule R19).

    Create one per document and call `unique` for each heading in document order.
    """

    def __init__(self) -> None:
        self._used: set[str] = set()
        self._next_suffix: dict[str, int] = {}

    def unique(self, text: str) -> str:
        """The slug for the next heading, suffixed `-k` on the k-th repeat."""
        base = slugify(text)
        suffix = self._next_suffix.get(base, 0)
        candidate = base if suffix == 0 else f"{base}-{suffix}"
        while candidate in self._used:
            suffix += 1
            candidate = f"{base}-{suffix}"
        self._used.add(candidate)
        self._next_suffix[base] = suffix + 1
        return candidate
