"""Folding of text for matching: what a reader would see as the same characters.

Used to find issue delimiters in untrusted text and to recognise `.git` in archive
member names. NFKC maps fullwidth and small-form characters to ASCII; format
characters (zero-width joiners, bidi marks, ...), nonspacing marks (variation
selectors, combining accents), control characters other than tab, line and page breaks, private-use
and unassigned code points, and the Hangul fillers are dropped, because they are
invisible or ignorable between the letters of a word.
"""

import unicodedata

_IGNORED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cn", "Mn", "Me"})
# Controls that render as a word break. Other controls are dropped, including
# U+001C-U+001F (str.isspace() is true for them, but they show as nothing) and U+0085
# (NEL), which most renderers show as nothing too.
_WORD_BREAKS = frozenset("\t\n\v\f\r")
_IGNORABLE_LETTERS = frozenset("ᅟᅠㅤﾠ")


def normalised(text: str, *, controls_as_space: bool = False) -> tuple[str, list[int]]:
    """The folded text, and for each of its characters the index it came from in
    `text`. With `controls_as_space`, an ignored control character (Cc) becomes a
    space instead of vanishing, so it separates the words around it."""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        # Whitespace controls (tab, newline, ...) separate words: dropping them would
        # glue `</issue` to the word after it and defeat the `\b` of the tag match.
        if (
            unicodedata.category(char) in _IGNORED_CATEGORIES
            and char not in _WORD_BREAKS
        ):
            if controls_as_space and unicodedata.category(char) == "Cc":
                chars.append(" ")
                origin.append(index)
            continue
        for folded in unicodedata.normalize("NFKC", char):
            if folded in _IGNORABLE_LETTERS:
                continue
            chars.append(folded)
            origin.append(index)
    return "".join(chars), origin


def fold(text: str) -> str:
    return normalised(text)[0]
