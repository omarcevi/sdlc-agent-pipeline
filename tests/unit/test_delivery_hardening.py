"""open_pr against hostile archives and patches: nothing may run on the host, and
nothing may reach GitHub."""

import tarfile
from pathlib import Path

import pytest

from app.archive import UNSAFE_PATH, ArchiveError
from app.nodes import finish
from app.nodes.finish import open_pr
from tests.unit.delivery_fakes import (
    _collect,
    _decision,
    _diff,
    _sha,
    _standard_change,
    archive_with,
    issue_record,
    make_source,
)


def hostile_git_dir(marker: Path) -> dict[str, bytes | None]:
    config = (
        "[core]\n\trepositoryformatversion = 0\n"
        f'[filter "evil"]\n\tclean = touch {marker}\n\tsmudge = touch {marker}\n'
    )
    return {
        ".git": None,
        ".git/HEAD": b"ref: refs/heads/main\n",
        ".git/config": config.encode(),
        ".git/objects": None,
        ".git/refs": None,
        ".gitattributes": b"* filter=evil\n",
    }


def naive_extract(archive: Path, dest: Path, **_kwargs) -> Path:
    """An extractor without the .git check, to test _build_changes on its own."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if "/" in member.name:
                tar.extract(
                    member.replace(name=member.name.split("/", 1)[1], deep=False),
                    dest,
                    filter="data",
                )
    return dest


async def _open(archive, unified, files, github_diff_files=None, **extra):
    return await _collect(
        open_pr(
            _decision(unified),
            issue=issue_record(),
            diff=_diff(
                unified, files if github_diff_files is None else github_diff_files
            ),
            patch_sha256=_sha(unified),
            source_archive=str(archive),
            **extra,
        )
    )


async def test_a_git_directory_in_the_archive_is_refused_and_runs_nothing(
    tmp_path, runs, github
):
    _plain, unified, files = make_source(tmp_path, _standard_change)
    marker = tmp_path / "MARKER"
    archive = archive_with(tmp_path, hostile_git_dir(marker))
    with pytest.raises(ArchiveError, match=UNSAFE_PATH):
        await _open(archive, unified, files)
    assert not marker.exists() and github.requests == []


async def test_a_git_file_in_the_archive_is_refused(tmp_path, runs, github):
    _plain, unified, files = make_source(tmp_path, _standard_change)
    archive = archive_with(tmp_path, {".git": b"gitdir: /somewhere/else\n"})
    with pytest.raises(ArchiveError, match=UNSAFE_PATH):
        await _open(archive, unified, files)
    assert github.requests == []


async def test_build_changes_refuses_a_git_directory_even_if_the_extractor_does_not(
    tmp_path, runs, github, monkeypatch
):
    _plain, unified, files = make_source(tmp_path, _standard_change)
    marker = tmp_path / "MARKER"
    archive = archive_with(tmp_path, hostile_git_dir(marker))
    monkeypatch.setattr(finish, "extract_tarball", naive_extract)
    with pytest.raises(RuntimeError, match=r"\.git"):
        await _open(archive, unified, files)
    assert not marker.exists() and github.requests == []


async def test_build_changes_refuses_a_git_file_even_if_the_extractor_does_not(
    tmp_path, runs, github, monkeypatch
):
    _plain, unified, files = make_source(tmp_path, _standard_change)
    archive = archive_with(tmp_path, {".git": b"gitdir: /somewhere/else\n"})
    monkeypatch.setattr(finish, "extract_tarball", naive_extract)
    with pytest.raises(RuntimeError, match=r"\.git"):
        await _open(archive, unified, files)
    assert github.requests == []


async def test_the_numstat_check_catches_a_github_path_the_diff_files_omit(
    tmp_path, runs, github
):
    def change(work: Path) -> None:
        (work / ".github").mkdir()
        (work / ".github" / "ci.yml").write_text("on: push\n")

    archive, unified, _files = make_source(tmp_path, change)
    with pytest.raises(RuntimeError, match=r"\.github"):
        await _open(archive, unified, [], github_diff_files=["keep.py"])
    assert github.requests == []


async def test_a_patch_that_creates_a_symlink_is_refused(tmp_path, runs, github):
    archive, _unified, _files = make_source(tmp_path, _standard_change)
    link = (
        "diff --git a/link b/link\n"
        "new file mode 120000\n"
        "--- /dev/null\n"
        "+++ b/link\n"
        "@@ -0,0 +1 @@\n"
        "+/etc/passwd\n"
        "\\ No newline at end of file\n"
    )
    with pytest.raises(RuntimeError, match="symbolic link"):
        await _open(archive, link, ["link"])
    assert github.requests == []


async def test_a_patch_that_touches_a_git_path_is_refused(tmp_path, runs, github):
    archive, _unified, _files = make_source(tmp_path, _standard_change)
    evil = (
        "diff --git a/.git/hooks/pre-commit b/.git/hooks/pre-commit\n"
        "new file mode 100755\n"
        "--- /dev/null\n"
        "+++ b/.git/hooks/pre-commit\n"
        "@@ -0,0 +1 @@\n"
        "+touch owned\n"
    )
    with pytest.raises(RuntimeError, match=r"\.git|does not apply"):
        await _open(archive, evil, [".git/hooks/pre-commit"])
    assert github.requests == []


def test_delivery_uses_the_one_archive_extractor():
    from app import archive

    assert finish.extract_tarball is archive.extract_tarball
