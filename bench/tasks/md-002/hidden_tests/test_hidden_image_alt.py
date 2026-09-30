from mdlite import to_html


def test_ampersand_in_alt_is_escaped_once():
    assert to_html("![Tom & Jerry](/img/tj.png)") == (
        '<p><img src="/img/tj.png" alt="Tom &amp; Jerry"></p>\n'
    )


def test_angle_brackets_in_alt_are_escaped_once():
    assert to_html("![a < b > c](/img/cmp.png)") == (
        '<p><img src="/img/cmp.png" alt="a &lt; b &gt; c"></p>\n'
    )


def test_quotes_in_alt_are_escaped_like_any_attribute():
    html = to_html("![say \"hi\" or 'bye'](/img/q.png)")
    assert html == (
        '<p><img src="/img/q.png" alt="say &quot;hi&quot; or &#39;bye&#39;"></p>\n'
    )


def test_quotes_and_ampersand_together():
    html = to_html("![\"Tom\" & 'Jerry'](/img/tj.png)")
    assert html == (
        '<p><img src="/img/tj.png" alt="&quot;Tom&quot; &amp; &#39;Jerry&#39;"></p>\n'
    )


def test_alt_of_formatted_label_inside_a_link():
    html = to_html('[![*Tom* & "Jerry"](/img/tj.png "T & J")](/cartoons)')
    assert html == (
        '<p><a href="/cartoons"><img src="/img/tj.png" '
        'alt="Tom &amp; &quot;Jerry&quot;" title="T &amp; J"></a></p>\n'
    )


def test_unsafe_image_shows_its_text_escaped_once():
    assert to_html("![Tom & Jerry <3](javascript:alert(1))") == (
        "<p>Tom &amp; Jerry &lt;3</p>\n"
    )
    assert to_html('![say "hi"](data:image/png;base64,AAAA)') == '<p>say "hi"</p>\n'
