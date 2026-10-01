"""Folding of text for matching: what a reader would see as the same characters.

Used to find issue delimiters in untrusted text and to recognise `.git` in archive
member names. NFKC maps fullwidth and small-form characters to ASCII; format
characters (zero-width joiners, bidi marks, ...), nonspacing marks (variation
selectors, combining accents), control characters other than whitespace, private-use
and unassigned code points, and the Hangul fillers are dropped, because they are
invisible or ignorable between the letters of a word.
"""

import unicodedata

_IGNORED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cn", "Mn", "Me"})
_IGNORABLE_LETTERS = frozenset("ᅟᅠㅤﾠ")


def normalised(text: str) -> tuple[str, list[int]]:
    """The folded text, and for each of its characters the index it came from in
    `text`."""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        # Whitespace controls (tab, newline, ...) separate words: dropping them would
        # glue `</issue` to the word after it and defeat the `\b` of the tag match.
        if unicodedata.category(char) in _IGNORED_CATEGORIES and not char.isspace():
            continue
        for folded in unicodedata.normalize("NFKC", char):
            if folded in _IGNORABLE_LETTERS:
                continue
            chars.append(folded)
            origin.append(index)
    return "".join(chars), origin


def fold(text: str) -> str:
    return normalised(text)[0]
