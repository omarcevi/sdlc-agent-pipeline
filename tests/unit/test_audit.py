import pytest

from bench.audit import audit_patch


def _patch(path: str, added: list[str]) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(added)} @@\n{body}"
    )


def test_a_clean_patch_has_no_flags():
    assert audit_patch(_patch("mini.py", ["def add(a, b):", "    return a + b"])) == []


def test_an_empty_patch_has_no_flags():
    assert audit_patch("") == []


@pytest.mark.parametrize(
    "path",
    [
        "pytest.py",
        "pkg/pytest.py",
        "conftest.py",
        "tests/conftest.py",
        "sitecustomize.py",
        "usercustomize.py",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "evil.pth",
        "lib/evil.pth",
    ],
)
def test_files_that_can_steer_the_test_process_are_flagged(path):
    assert audit_patch(_patch(path, ["x = 1"])) == [f"touches {path}"]


def test_a_conftest_with_only_a_fixture_is_still_flagged():
    patch = _patch(
        "tests/conftest.py", ["import pytest", "", "@pytest.fixture", "def f(): ..."]
    )
    assert audit_patch(patch) == ["touches tests/conftest.py"]


def test_a_modified_pyproject_is_flagged():
    patch = (
        "diff --git a/pyproject.toml b/pyproject.toml\n"
        "--- a/pyproject.toml\n+++ b/pyproject.toml\n"
        "@@ -1 +1 @@\n-a = 1\n+a = 2\n"
    )
    assert audit_patch(patch) == ["touches pyproject.toml"]


@pytest.mark.parametrize(
    "token",
    [
        "os._exit",
        "atexit",
        "sys.exit(",
        "pytest_collection_modifyitems",
        "pytest_runtest_makereport",
        "collect_ignore",
    ],
)
def test_added_lines_with_dangerous_tokens_are_flagged(token):
    patch = _patch("pkg/core.py", ["def f():", f"    {token}"])
    assert audit_patch(patch) == [f"adds {token} in pkg/core.py"]


def test_a_removed_line_with_a_token_is_not_flagged():
    patch = (
        "diff --git a/pkg/core.py b/pkg/core.py\n"
        "--- a/pkg/core.py\n+++ b/pkg/core.py\n"
        "@@ -1,2 +1 @@\n-import atexit\n keep = 1\n"
    )
    assert audit_patch(patch) == []


def test_a_plus_plus_plus_header_is_not_an_added_line():
    assert audit_patch(_patch("os._exit.py", ["x = 1"])) == []


def test_several_findings_are_all_reported():
    patch = _patch("conftest.py", ["import atexit"])
    assert audit_patch(patch) == ["touches conftest.py", "adds atexit in conftest.py"]
