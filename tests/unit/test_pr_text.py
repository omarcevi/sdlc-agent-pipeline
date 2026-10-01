import re

from app import pr_text
from app.pr_text import (
    AUTHOR_EMAIL,
    AUTHOR_NAME,
    branch_name,
    code_span,
    commit_message,
    failure_comment,
    fence,
    marker,
    pr_body,
    pr_title,
)

HOSTILE = [
    "ping @someone now",
    "Fixes #1",
    "see owner/repo#2",
    "<img src=x onerror=alert(1)>",
    "![x](http://example.com/a.png)",
    "[click](http://example.com)",
    "``` closes #3",
    "````````` ~~~~~~~~~~ resolves #4",
    "~~~\nFixes #5\n~~~",
    "```\n</details> @someone\n```\nFixes #6",
    "line\r\n```\r\nFixes #7\r\n@someone\r\n",
    "evil \u202e txt.exe \u200b@someone\u200b \ufeff Fixes \u2066#8",
    "`~" * 1000,
    "`" * 40 + "\n" + "~" * 40 + " Fixes #9",
]
CLOSING = re.compile(
    r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+(?:[\w.-]+/[\w.-]+)?#\d+",
    re.IGNORECASE,
)


def _outside_fences(markdown: str) -> str:
    """The markdown with every fenced block removed, closing fences as CommonMark does."""
    kept: list[str] = []
    opener: str | None = None
    for line in markdown.split("\n"):
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if opener is None:
            match = re.match(r"(`{3,}|~{3,})[^`]*$", stripped) if indent < 4 else None
            if match:
                opener = match.group(1)
            else:
                kept.append(line)
        elif indent < 4 and re.fullmatch(
            re.escape(opener[0]) + f"{{{len(opener)},}}[ \t]*", stripped
        ):
            opener = None
    assert opener is None, "a fence was never closed"
    return "\n".join(kept)


def _diff():
    return {
        "unified_diff": "diff --git a/a.py b/a.py\n",
        "files": ["a.py", "pkg/b.py"],
        "insertions": 7,
        "deletions": 2,
    }


def _body(**over):
    args = {
        "run_id": "run-1",
        "issue_number": 12,
        "diff": _diff(),
        "plan": {"summary": "plan summary"},
        "patch": {"summary": "coder summary"},
        "test_report": {"passed": True, "exit_code": 0, "failed_tests": []},
        "review": {"verdict": "approve", "comments": [], "must_fix": []},
        "budget": {
            "cost_usd": 0.1234,
            "tool_calls": 31,
            "models": ["gemini-3.8-flash"],
        },
        "approver": "octocat",
        "patch_sha256": "ab" * 32,
    }
    args.update(over)
    return pr_body(**args)


def test_model_text_cannot_mention_link_reference_or_close():
    for text in HOSTILE:
        assert _outside_fences(fence(text)) == ""
        body = _body(
            plan={"summary": text},
            patch={"summary": text},
            review={
                "verdict": "request_changes",
                "comments": [
                    {"file": "a.py", "line": 1, "severity": "major", "issue": text}
                ],
                "must_fix": [text],
            },
        )
        outside = _outside_fences(body)
        for token in (
            "@someone",
            "<img",
            "![x]",
            "[click]",
            "owner/repo#2",
            "</details>",
        ):
            assert token not in outside, (token, text)
        assert not CLOSING.search(outside), text
        assert body.count("Proposed fix for #12") == 1


def test_fence_is_longer_than_any_run_inside():
    block = fence("a ``````` b ~~~~~~~~~~~~ c")
    opening = block.split("\n")[0]
    assert len(opening) > 12 and set(opening) <= {"`", "~"}


def test_body_has_the_marker_the_issue_reference_and_the_real_numbers():
    body = _body()
    assert body.startswith(marker("run-1", "pr"))
    assert marker("run-1", "pr") == "<!-- issue-to-pr run=run-1 kind=pr -->"
    assert body.count("Proposed fix for #12") == 1
    assert "+7" in body and "-2" in body
    assert "`a.py`" in body and "`pkg/b.py`" in body
    assert "exit code 0" in body
    assert "0.1234" in body and "31" in body and "gemini-3.8-flash" in body
    assert "ab" * 32 in body
    assert not CLOSING.search(body)
    assert body.rstrip().endswith(
        "Opened by the issue-to-pr pipeline (run `run-1`, model `gemini-3.8-flash`), "
        "approved by @octocat"
    )
    assert AUTHOR_NAME == "issue-to-pr pipeline"
    assert AUTHOR_EMAIL == "issue-to-pr@example.invalid"


def test_failed_tests_are_rendered_from_fields():
    body = _body(
        test_report={
            "passed": False,
            "exit_code": 1,
            "failed_tests": ["tests/test_a.py::test_x", "t`est"],
        }
    )
    assert "exit code 1" in body
    assert "`tests/test_a.py::test_x`" in body
    assert "``t`est``" in body


def test_body_and_blocks_are_capped():
    huge = "x" * 100_000
    block = fence(huge)
    assert len(block) < 2_300 and "truncated" in block
    many = [f"dir/{'p' * 150}{i}.py" for i in range(400)]
    body = _body(
        plan={"summary": huge},
        patch={"summary": huge},
        diff={**_diff(), "files": many},
        review={
            "verdict": "request_changes",
            "comments": [
                {"file": "a.py", "line": 1, "severity": "major", "issue": huge}
                for _ in range(50)
            ],
            "must_fix": [huge] * 50,
        },
        test_report={"passed": False, "exit_code": 1, "failed_tests": many},
    )
    assert len(body) <= 20_000
    _outside_fences(body)  # every fence is closed
    assert body.rstrip().endswith("approved by @octocat")
    assert len(fence("é" * 3000)) < 2_300


def test_title_branch_and_commit_message_are_clean():
    title = pr_title("a\nb\r\nc\x07d\x1b")
    assert title.startswith("[issue-to-pr] ") and "\n" not in title
    assert not any(ord(c) < 32 for c in title)
    assert len(pr_title("x" * 500)) == 120
    assert branch_name(7, "r-1") == "issue-to-pr/7-r-1"
    name = branch_name(7, "RUN 1/../Ab_c" + "z" * 100)
    assert re.fullmatch(r"issue-to-pr/7-[a-z0-9-]{1,40}", name)
    assert branch_name(7, "x" * 100) == "issue-to-pr/7-" + "x" * 40
    msg = commit_message(3, "Line one\nline two " + "y" * 200)
    assert msg.startswith("Fix #3: ") and "\n" not in msg and len(msg) <= 72
    assert commit_message(3, "add") == "Fix #3: add"
    # a hostile title must not smuggle another issue reference into the commit
    hostile = commit_message(3, "Fixes #99 and closes owner/r#4")
    assert "#99" not in hostile and "r#4" not in hostile


def test_alternating_fence_characters_cannot_blow_the_caps():
    text = "`~" * 1000
    block = fence(text)
    assert len(block) < 2_300
    assert len(block.split("\n")[0]) <= 17
    assert len(fence("`" * 5000)) < 2_300
    huge = "`~" * 1000
    body = _body(
        plan={"summary": huge},
        patch={"summary": huge},
        review={
            "verdict": "request_changes",
            "comments": [
                {"file": "a.py", "line": 1, "severity": "major", "issue": huge}
            ],
            "must_fix": [huge],
        },
    )
    assert len(body) <= 20_000
    _outside_fences(body)
    assert body.rstrip().endswith("approved by @octocat")


def test_body_blocks_shrink_to_fit_the_cap():
    many_models = [f"model-{i}-" + "m" * 50 for i in range(40)]
    body = _body(
        plan={"summary": "x" * 5000},
        patch={"summary": "y" * 5000},
        review={
            "verdict": "request_changes",
            "comments": [
                {"file": "f" * 300, "line": 1, "severity": "major", "issue": "z" * 3000}
            ]
            * 30,
            "must_fix": ["w" * 3000] * 30,
        },
        budget={"cost_usd": 1, "tool_calls": 1, "models": many_models},
    )
    assert len(body) <= 20_000
    _outside_fences(body)


def test_titles_and_commit_messages_defuse_references_and_mentions():
    for text in (
        "Fix #3",
        "closes GH-99",
        "resolves https://github.com/o/r/issues/5",
        "hello @someone",
        "see owner/repo#4",
    ):
        for out in (pr_title(text), commit_message(1, text)):
            tail = out.removeprefix("[issue-to-pr] ").removeprefix("Fix #1: ")
            assert "#" not in tail and "@" not in tail, out
            assert "GH-" not in tail and "/issues/" not in tail, out
    assert pr_title("a\u202etxt.exe\u200b b").count("\u202e") == 0
    assert "\u200b" not in pr_title("a\u200bb")


def test_invisible_characters_are_stripped_from_spans_and_fences():
    assert code_span("src/\u202etxt.exe") == "`src/ txt.exe`"
    assert "\u200b" not in code_span("a\u200bb") and "\ufeff" not in code_span(
        "\ufeffa"
    )
    assert "\u202e" not in fence("x\u202ey")


def test_unknown_exit_code_is_not_rendered_as_zero():
    body = _body(test_report={"passed": False, "failed_tests": []})
    assert "exit code unknown" in body and "exit code 0" not in body
    body = _body(test_report={"passed": False, "exit_code": None})
    assert "exit code unknown" in body


def test_paths_are_code_spans_with_backticks_escaped():
    assert code_span("a.py") == "`a.py`"
    assert code_span("a`b") == "``a`b``"
    assert code_span("`edge`") == "`` `edge` ``"
    assert code_span("x\ny\x00") == "`x y`"
    assert code_span("a``b`c") == "```a``b`c```"
    assert len(code_span("p" * 1000)) <= 210


def test_failure_comment_is_marked_fenced_and_signed():
    text = failure_comment(
        run_id="run-9",
        outcome="declined",
        reason="@victim Fixes #1 ````",
        plan={"summary": "s"},
        test_report=None,
        approver=None,
        budget={"models": ["m1", "m2"]},
    )
    assert text.startswith(marker("run-9", "failure"))
    outside = _outside_fences(text)
    assert "@victim" not in outside and not CLOSING.search(outside)
    assert "models `m1, m2`" in text
    assert "approved by" not in text
    assert "{run_id}" in pr_text.FOOTER_TEMPLATE.format(
        run_id="{run_id}", label="model", model="m", approval=""
    )
