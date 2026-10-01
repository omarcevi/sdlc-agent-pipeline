import time

import pytest

from app.archive import UNSAFE_PATH, ArchiveError, extract_tarball
from app.nodes.intake import format_issue_text
from app.textfold import fold
from tests.unit.archives import build


@pytest.mark.parametrize(
    "inside",
    ["\x00", "\x1f", "\x7f", "\x9d", "\ue000", "\U000f0000", "\u0378"],  # Cc, Co, Cn
)
def test_control_private_use_and_unassigned_characters_are_folded_away(inside):
    assert fold(f"is{inside}sue") == "issue"


@pytest.mark.parametrize(
    "inside",
    [
        "\x00",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x1f",
        "\x7f",
        "\x85",
        "\x9d",
        "\ue000",
        "\u0378",
    ],
)
def test_a_closing_tag_with_such_a_character_inside_the_word_is_defused(inside):
    text = format_issue_text("t", f"a </is{inside}sue> b")
    assert text.count("</issue>") == 1  # only our own
    assert "[/issue" in text


def test_whitespace_controls_are_kept_so_words_do_not_glue_together():
    assert fold("a\tb\nc") == "a\tb\nc"
    text = format_issue_text("t", "x </issue\nfoo> y")
    assert "[/issue" in text


@pytest.mark.parametrize("inside", ["\x7f", "\x9d", "\ue000"])  # tar cuts at NUL
def test_the_git_check_sees_through_control_characters(tmp_path, inside):
    src = build(
        tmp_path / "g.tar.gz", [("top/ok", b"1"), (f"top/.g{inside}it/config", b"x")]
    )
    with pytest.raises(ArchiveError, match=UNSAFE_PATH):
        extract_tarball(src, tmp_path / "repo")


def test_the_issue_tag_match_is_linear_in_whitespace():
    for tail in ("x", "\n", "/x", "&lt;"):
        started = time.perf_counter()
        format_issue_text("t", "<" + " " * 20_000 + tail)
        format_issue_text("t", "&lt;" + "\t" * 20_000 + tail)
        format_issue_text("t", "<\\" + " " * 20_000 + tail)
        assert time.perf_counter() - started < 0.1


@pytest.mark.parametrize("glue", ["\x1f", "\x1c", "\x85", "\x00", "\x7f"])
def test_a_tag_glued_to_the_next_word_by_a_dropped_control_is_defused(glue):
    text = format_issue_text("t", f"a </issue{glue}foo> b")
    assert text.count("</issue") == 1  # only our own
    assert "[/issue" in text


def test_a_dropped_control_inside_the_word_and_one_after_it_are_both_defused():
    text = format_issue_text("t", "</is\x1fsue> and <issue\x85x> and </issue\x00>")
    assert text.count("<issue") == 1 and text.count("</issue") == 1
    assert text.count("[/issue") == 2 and text.count("[issue") == 1
