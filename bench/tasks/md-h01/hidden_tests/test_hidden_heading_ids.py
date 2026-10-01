import re

from mdlite import to_html, toc

NBSP = "\u00a0"


def ids(text):
    return re.findall(r'<h[1-6] id="([^"]*)"', to_html(text))


def test_issue_example_keeps_the_words_apart():
    text = "## Délai de 10" + NBSP + "jours"
    assert to_html(text) == (
        '<h2 id="délai-de-10-jours">Délai de 10' + NBSP + "jours</h2>\n"
    )
    assert toc(text) == [(2, "Délai de 10" + NBSP + "jours", "délai-de-10-jours")]


def test_every_kind_of_whitespace_separates_words():
    text = "\n\n".join(
        [
            "# Prix" + NBSP + "HT",
            "# A\tB",
            "# Thin\u2009space",
            "# Narrow\u202fspace",
            "# 東京\u3000大阪",
            "# Mixed " + NBSP + "-\t run",
            "# Pourquoi" + NBSP + "?",
        ]
    )
    expected = [
        "prix-ht",
        "a-b",
        "thin-space",
        "narrow-space",
        "東京-大阪",
        "mixed-run",
        "pourquoi",
    ]
    assert ids(text) == expected
    assert [slug for _, _, slug in toc(text)] == expected


def test_duplicates_count_across_kinds_of_space():
    text = "## Prix HT\n\n## Prix" + NBSP + "HT\n\n## Prix\tHT"
    assert ids(text) == ["prix-ht", "prix-ht-1", "prix-ht-2"]
    assert [slug for _, _, slug in toc(text)] == ["prix-ht", "prix-ht-1", "prix-ht-2"]


def test_heading_text_and_other_slug_rules_are_unchanged():
    assert to_html("# Prix" + NBSP + "HT *net*") == (
        '<h1 id="prix-ht-net">Prix' + NBSP + "HT <em>net</em></h1>\n"
    )
    assert toc("# A" + NBSP + "`b`") == [(1, "A" + NBSP + "b", "a-b")]
    assert to_html("10" + NBSP + "km") == "<p>10" + NBSP + "km</p>\n"
    assert to_html("#" + NBSP + "Foo") == "<p>#" + NBSP + "Foo</p>\n"
    assert ids("# snake_case_name\n# C++ & C#\n# Café -- Ünï\n# !!!") == [
        "snakecasename",
        "c-c",
        "café-ünï",
        "section",
    ]
