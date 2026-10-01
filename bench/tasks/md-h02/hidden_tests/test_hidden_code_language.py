import pytest
from mdlite import to_html
from mdlite.blocks import parse
from mdlite.nodes import BlockQuote, CodeBlock


def only_block(text):
    (block,) = parse(text).children
    return block


@pytest.mark.parametrize(
    ("source", "language"),
    [
        ("```python extra\nprint(1)\n```", "python"),
        ("~~~ruby\nputs 1\n~~~", "ruby"),
        ("```   js  \nx\n```", "js"),
        ("```py\tx\n```", "py"),
        ("```py\u00a0x\n```", "py\u00a0x"),
        ("```a\\*b\n```", "a\\*b"),
        ("~~~ a`b\n~~~", "a`b"),
        ("```c\"d'e&f\n```", "c\"d'e&f"),
    ],
)
def test_language_is_the_word_used_for_the_class(source, language):
    assert only_block(source).language == language


def test_a_fence_without_info_has_no_language():
    assert only_block("```\ncode\n```").language is None
    assert only_block("```   \ncode\n```").language is None
    assert only_block("~~~~\nnever closed").language is None


def test_code_blocks_inside_quotes_have_a_language_too():
    quote = only_block("> ```sh\n> ls -l\n> ```")
    assert isinstance(quote, BlockQuote)
    (code,) = quote.children
    assert (code.language, code.code) == ("sh", "ls -l\n")


def test_html_is_unchanged():
    assert to_html("```python extra\nprint(1)\n```") == (
        '<pre><code class="language-python">print(1)\n</code></pre>\n'
    )
    assert to_html("```c\"d'e&f\n1 < 2\n```") == (
        '<pre><code class="language-c&quot;d&#39;e&amp;f">1 &lt; 2\n</code></pre>\n'
    )
    assert to_html("```py\tx\n```") == '<pre><code class="language-py"></code></pre>\n'
    assert to_html("```py\u00a0x\n```") == (
        '<pre><code class="language-py\u00a0x"></code></pre>\n'
    )
    assert to_html("```\na\n```") == "<pre><code>a\n</code></pre>\n"
    assert to_html("> ~~~ sh\n> ls\n> ~~~") == (
        '<blockquote>\n<pre><code class="language-sh">ls\n</code></pre>\n'
        "</blockquote>\n"
    )


def test_parsed_trees_compare_as_before():
    assert parse("```py x\ncode\n```").children == [CodeBlock("py x", "code\n")]
    assert parse("~~~ a`b\nx\n~~~").children == [CodeBlock("a`b", "x\n")]
    assert parse("> ```\n> a\n> ```").children == [BlockQuote([CodeBlock("", "a\n")])]
    assert CodeBlock("py", "a\n") != CodeBlock("rb", "a\n")
    assert parse("```py\na\n```").children != [CodeBlock("rb", "a\n")]
