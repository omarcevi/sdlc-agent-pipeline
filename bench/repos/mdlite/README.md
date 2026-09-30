# mdlite

A small Markdown-to-HTML library with no dependencies. It supports a deliberately
limited dialect of Markdown, described by the rules below.

    import mdlite

    mdlite.to_html("# Hello *world*")   # '<h1 id="hello-world">Hello <em>world</em></h1>\n'
    mdlite.toc("# A\n## B")             # [(1, 'A', 'a'), (2, 'B', 'b')]

    python -m mdlite notes.md           # prints the HTML (use `-` to read standard input)

`to_html(text: str) -> str` and `toc(text: str) -> list[tuple[int, str, str]]`
(level, plain text, slug) are the public API. The converter is a pure function of its
input: same text, same output.

## Layout

| Module | Job |
|---|---|
| `nodes.py` | AST dataclasses (block and inline nodes) |
| `blocks.py` | Block parser: text to a `Document` of block nodes |
| `inline.py` | Inline parser: one block's text to inline nodes; `plain_text` |
| `render.py` | AST to HTML |
| `escape.py` | HTML escaping and URL scheme checks (the only place that does either) |
| `slug.py` | Heading slugs and duplicate handling |
| `toc.py` | Table of contents |
| `cli.py`, `__main__.py` | `python -m mdlite <file>` |

The renderer and the table of contents both call the inline parser for heading text
and both take their slugs from `slug.Slugger`, so the ids in the HTML always match
the table of contents.

## Rules

Everything the library does is specified here. "Line" means a line after rule R1. In
the block rules (R1 to R9) "whitespace" and "blank" mean spaces and tabs only; other
characters such as a no-break space are ordinary text there. Elsewhere "space" and
"whitespace" mean any Unicode whitespace character (Python's `str.isspace`), unless a
rule says otherwise (R5, R12, R13 and R15 name the exact characters they mean).

**R1. Input normalisation.** `\r\n` and `\r` become `\n`. Tabs in the leading
whitespace of a line are expanded to 4-column tab stops; tabs elsewhere are left
alone. A final newline ends the last line; it does not start another one. A blank line
is one with only spaces and tabs. The empty document renders as the empty string.

**R2. ATX headings.** A line of 1 to 6 `#`, indented by at most 3 spaces and followed
by a space or a tab (or the end of the line), is a heading of that level. Its text is
the rest of the line, trimmed, minus an optional closing run of `#` that is preceded by
a space or tab (or is the whole text). Seven or more `#`, or anything else after them,
make a paragraph.
There are no setext headings. Output: `<hN id="slug">...</hN>`.

**R3. Paragraphs.** Consecutive non-blank lines form one paragraph, unless a line
starts another block (R2, R4, R5, R6, R7; any list marker interrupts a paragraph).
Leading whitespace of each line and trailing whitespace of the paragraph are removed;
lines are joined by `\n`. There are no indented code blocks: a line indented by 4 or
more spaces is ordinary paragraph text. Output: `<p>...</p>`.

**R4. Thematic breaks.** A line of three or more `-`, `*` or `_` (one kind only, spaces
and tabs allowed between them, at most 3 spaces of indent) is a thematic break,
`<hr>`. This is checked before list markers, so `- - -` is a break, not a list.

**R5. Fenced code blocks.** A line of three or more backticks or tildes opens a fence
(a backtick fence's info string may not contain a backtick). Both the opening and the
closing fence are indented by at most 3 spaces. The fence closes at the first line of
the same character, at least as long, with nothing after it but spaces or tabs; an
unclosed fence runs to the end of the document. Content lines are taken literally
(nothing is parsed), minus up to as many leading spaces as the opening fence was
indented. The first word of the info string (words are separated by spaces and tabs only) becomes `class="language-WORD"`.
Output: `<pre><code ...>` followed by every content line (empty ones too) plus a
newline, then `</code></pre>`; a fence with no content lines has an empty `<code>`.

**R6. Block quotes.** Consecutive lines starting with `>` (at most 3 spaces of indent)
form a block quote. One space after the `>` is removed, and the remaining lines are
parsed as blocks. A line without `>` ends the quote (no lazy continuation).
Output: `<blockquote>`.

**R7. Lists.** A list item starts with a marker at most 3 spaces indented: `-`, `*` or
`+`, or 1 to 9 ASCII digits (`0`-`9`) followed by `.` or `)`; then at least one space
(or the end of the line). Items whose markers are the same kind (same bullet character, or same
delimiter) belong to one list; a different kind ends the list and starts a new one. A
single blank line between items does not end the list, and items are never "loose":
item text is never wrapped in `<p>`, and it is inline text only (`- # h` is an item with
the text `# h`, not a heading). An ordered list starts at its first item's
number; `<ol>` gets `start="N"` only when N is not 1.

**R8. List item continuation.** A non-blank line after an item line that is not a
marker line and does not start another block (R2, R4, R5, R6) continues the item's text,
whatever its indentation; the lines are joined by `\n` like a paragraph. A blank line
followed by anything other than an item of the same list or a nested marker line (R9)
ends the list.

**R9. Nesting.** A marker line indented to at least the column where the current
item's text starts is nested under that item. That column is the marker's indent plus
the marker width plus the number of spaces after the marker, where 0 spaces (an empty
item) and 5 or more spaces count as 1 (`- a` and `1. a` start text at columns 2 and 3;
`-   a` at column 4). All such lines after one item form a single sublist, whose type
is the first nested marker's. Nesting is one level deep: a deeper marker line is an item of
the same sublist. Continuation lines after a nested item continue that nested item.
A marker line indented less than that column is not nested: it is a sibling item if it
is the same kind as the list, otherwise it ends the list.

**R10. HTML escaping.** Text, code spans and code blocks escape `&`, `<` and `>` as
`&amp;`, `&lt;` and `&gt;`. Attribute values (`href`, `src`, `alt`, `title`, `id`, the
code `class`) additionally escape `"` as `&quot;` and `'` as `&#39;`. `&` is always
escaped, so an input entity such as `&amp;` is shown literally. Raw HTML is not
supported: `<b>` in the input is text and comes out escaped.

**R11. Backslash escapes.** A backslash before an ASCII punctuation character yields
that character literally (it is never part of emphasis, code, a link or an autolink
delimiter). A backslash before anything else is a literal backslash. Backslash escapes
are not processed inside code spans, code blocks, info strings or autolinks (R17).

**R12. Line breaks.** A newline inside a paragraph, list item or quote is kept as
`\n` (a soft break), after removing U+0020 spaces before it. Two or more U+0020 spaces,
or a backslash, at the end of a line that is followed by another line of the same block
make a hard break, `<br>` followed by `\n`. At the very end of a block, trailing
spaces and tabs are removed (R3) and a final backslash stays a literal backslash.

**R13. Code spans.** A run of N backticks opens a code span that closes at the next
run of exactly N backticks; with no such run the backticks are literal. Newlines in the
content become spaces, and if the content both starts and ends with a U+0020 space (and is not
only spaces) one space is removed from each end; tabs and other whitespace are kept. The content is escaped (R10) and
nothing inside is parsed: `*`, `_`, `[` and backslashes are literal. A code span
binds tighter than emphasis and links, so delimiters inside it never pair with
delimiters outside it.

**R14. Emphasis and strong.** A run of `*` or `_` can open when followed by a
non-space character and close when preceded by a non-space character (the start and end
of the text count as spaces; a no-break space is a space). For `_`, a run that touches a letter or digit on the
outside cannot open (letter or digit before it) or close (letter or digit after it), so
`snake_case_name` is plain text; `*` may be used inside words. Each closing run pairs
with the nearest open run of the same character before it; two characters on both
sides make `<strong>`, otherwise `<em>`; `***x***` is `<em><strong>x</strong></em>`.
Unpaired delimiters are literal text. Emphasis and strong nest at most 20 levels deep
(R22).

**R15. Links and images.** `[text](url)` and `[text](url "title")` (title in `"` or
`'`) make links; `![alt](url)` makes an image. The url is either `<...>` (may contain
spaces; `\>` is a literal `>`, any other `>` ends it, and an unescaped `<` or a line
break inside makes the link literal text) or a run without whitespace (any Unicode
whitespace) and with balanced parentheses. A space, tab or newline (and nothing else)
may separate the url from the title. Backslash escapes are resolved in url and title. The text may contain emphasis, code spans and images but not
another link or autolink (an inner `[` or `<` is literal). An image's `alt` is the plain text of its
label. Without a `(` right after the closing `]`, the brackets are literal text. Image
labels nest at most 10 levels deep (R22).

**R16. URL safety.** A url has a scheme when, after deleting the characters U+0000 to
U+0020 and U+007F (control characters and ASCII whitespace), it starts with a letter, then letters, digits, `+`, `.` or `-`, then `:`.
Urls without a scheme (relative urls, `#anchors`, `//host/path`) and with the scheme
`http`, `https` or `mailto` (case-insensitive) are safe. Any other scheme is unsafe: a
link with an unsafe url renders as its text alone, without `<a>`, and an image renders
as its alt text alone.

**R17. Autolinks.** `<scheme:rest>`, where the scheme is 2 to 32 characters (R16's
syntax) and the text has no whitespace, `<` or `>`, is a link whose text is the url
itself. Nothing inside is a backslash escape, so the first `>` always ends it.
`<name@host>` (no spaces, exactly one `@`) is a link to `mailto:name@host` showing the
address. R16 applies: an autolink with an unsafe scheme renders as its text.

**R18. Slugs.** A heading's slug is built from its plain text (emphasis markers
dropped, code span content, link text, image alt, escapes resolved): lowercase it,
drop every character that is not a letter, digit, whitespace or `-`, turn each run of
whitespace and `-` into one `-`, and trim `-` from both ends. Letters and digits are
Unicode ones. An empty result becomes `section`.

**R19. Duplicate slugs.** Within one document the first heading keeps its slug; the
k-th repeat gets the suffix `-k` (`intro`, `intro-1`, `intro-2`), skipping any slug
already in use (the headings `a`, `a`, `a-1` get `a`, `a-1`, `a-1-1`).

**R20. Heading ids and table of contents.** Every heading, including those inside block
quotes, gets `id="slug"` by R18 and R19, in document order. `toc(text)` returns one
`(level, plain_text, slug)` per heading in document order with exactly those slugs.
The plain text is the same text R18 starts from.

**R21. Command line.** `python -m mdlite FILE` reads FILE as UTF-8 (`-` is standard
input), writes the HTML to standard output as UTF-8 and exits 0. An unreadable or
undecodable file prints `mdlite: ...` to standard error and exits 1. Wrong arguments
exit 2.

**R22. Nesting limits.** Every input renders; none raises. Block quotes nest at most 20
levels: a `>` line at that depth is ordinary text. It starts no block and interrupts no
paragraph or list item, so it continues them (and is escaped as `&gt;`). Image labels nest at most 10 levels: a deeper `![` is literal text. Emphasis and
strong nest at most 20 levels: a closing run that would pair at a deeper level becomes
literal text for good (it cannot open a later pair either).
