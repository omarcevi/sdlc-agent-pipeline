from mdlite import to_html

GUIDE = (
    "1. Download the archive\n"
    "2. Configure\n"
    "   1. Copy the sample file\n"
    "   2. Edit the paths\n"
    "3. Run\n"
)


def test_issue_example_numbers_the_sub_steps_from_one():
    assert to_html(GUIDE) == (
        "<ol>\n"
        "<li>Download the archive</li>\n"
        "<li>Configure\n"
        "<ol>\n"
        "<li>Copy the sample file</li>\n"
        "<li>Edit the paths</li>\n"
        "</ol>\n"
        "</li>\n"
        "<li>Run</li>\n"
        "</ol>\n"
    )


def test_sub_list_under_any_later_item_starts_at_its_own_number():
    assert to_html("1. a\n2. b\n3. c\n   1. x\n   2. y") == (
        "<ol>\n<li>a</li>\n<li>b</li>\n<li>c\n"
        "<ol>\n<li>x</li>\n<li>y</li>\n</ol>\n</li>\n</ol>\n"
    )
    assert to_html("4) a\n   1) b") == (
        '<ol start="4">\n<li>a\n<ol>\n<li>b</li>\n</ol>\n</li>\n</ol>\n'
    )


def test_a_sub_list_that_starts_elsewhere_keeps_its_start():
    assert to_html("- Advanced\n  5. Tune the cache\n  6. Restart") == (
        "<ul>\n<li>Advanced\n"
        '<ol start="5">\n<li>Tune the cache</li>\n<li>Restart</li>\n</ol>\n'
        "</li>\n</ul>\n"
    )
    assert to_html("1. a\n2. b\n   3. c\n   4. d") == (
        "<ol>\n<li>a</li>\n<li>b\n"
        '<ol start="3">\n<li>c</li>\n<li>d</li>\n</ol>\n</li>\n</ol>\n'
    )
    assert to_html("7. a\n   7. b") == (
        '<ol start="7">\n<li>a\n<ol start="7">\n<li>b</li>\n</ol>\n</li>\n</ol>\n'
    )


def test_top_level_lists_and_bullet_sub_lists_are_unchanged():
    assert to_html("3. x\n4. y\n   - z") == (
        '<ol start="3">\n<li>x</li>\n<li>y\n<ul>\n<li>z</li>\n</ul>\n</li>\n</ol>\n'
    )
    assert to_html("- a\n  - b\n- c") == (
        "<ul>\n<li>a\n<ul>\n<li>b</li>\n</ul>\n</li>\n<li>c</li>\n</ul>\n"
    )
    assert to_html("0. a\n1. b") == '<ol start="0">\n<li>a</li>\n<li>b</li>\n</ol>\n'
