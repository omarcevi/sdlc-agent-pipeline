import io
import tarfile

import pytest

from app import archive
from app.archive import ArchiveError, extract_tarball


def build(path, entries):
    """entries: (name, bytes | None for a directory | ('link', kind, target))."""
    with tarfile.open(path, "w:gz") as tar:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif isinstance(content, tuple):
                info.type = tarfile.SYMTYPE if content[0] == "sym" else tarfile.LNKTYPE
                info.linkname = content[1]
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return path


def test_top_level_directory_is_stripped(tmp_path):
    src = build(
        tmp_path / "a.tar.gz",
        [
            ("owner-repo-abc123", None),
            ("owner-repo-abc123/pkg", None),
            ("owner-repo-abc123/pkg/mod.py", b"x = 1\n"),
            ("owner-repo-abc123/README.md", b"hi\n"),
        ],
    )
    out = extract_tarball(src, tmp_path / "repo")
    assert out == tmp_path / "repo"
    assert (out / "pkg" / "mod.py").read_text() == "x = 1\n"
    assert (out / "README.md").read_text() == "hi\n"
    assert not (out / "owner-repo-abc123").exists()


def test_two_top_level_entries_or_none_are_refused(tmp_path):
    two = build(tmp_path / "b.tar.gz", [("a/x", b"1"), ("b/y", b"2")])
    with pytest.raises(ArchiveError, match="exactly one top-level"):
        extract_tarball(two, tmp_path / "r1")
    flat = build(tmp_path / "c.tar.gz", [("x.py", b"1")])
    with pytest.raises(ArchiveError, match="exactly one top-level"):
        extract_tarball(flat, tmp_path / "r2")
    empty = build(tmp_path / "d.tar.gz", [])
    with pytest.raises(ArchiveError, match="exactly one top-level"):
        extract_tarball(empty, tmp_path / "r3")


def test_traversal_is_refused(tmp_path):
    src = build(tmp_path / "t.tar.gz", [("top/ok", b"1"), ("top/../../evil", b"x")])
    with pytest.raises(ArchiveError, match=archive.UNSAFE_PATH):
        extract_tarball(src, tmp_path / "repo")
    assert not (tmp_path / "evil").exists()
    assert not (tmp_path / "repo" / "ok").exists()  # nothing extracted before the check


def test_absolute_path_is_refused(tmp_path):
    src = build(tmp_path / "t.tar.gz", [("/etc/evil", b"x")])
    with pytest.raises(ArchiveError, match=archive.UNSAFE_PATH):
        extract_tarball(src, tmp_path / "repo")


def test_links_are_refused(tmp_path):
    for kind in ("sym", "hard"):
        src = build(
            tmp_path / f"{kind}.tar.gz",
            [("top/a", b"1"), ("top/link", (kind, "/etc/passwd"))],
        )
        with pytest.raises(ArchiveError, match=archive.HAS_LINKS):
            extract_tarball(src, tmp_path / f"repo-{kind}")


def test_too_many_files_is_refused(tmp_path):
    src = build(tmp_path / "t.tar.gz", [(f"top/f{i}", b"1") for i in range(5)])
    with pytest.raises(ArchiveError, match=archive.TOO_MANY_FILES):
        extract_tarball(src, tmp_path / "repo", max_files=4)
    extract_tarball(src, tmp_path / "ok", max_files=5)


def test_too_large_is_refused(tmp_path):
    src = build(tmp_path / "t.tar.gz", [("top/a", b"x" * 60), ("top/b", b"y" * 60)])
    with pytest.raises(ArchiveError, match=archive.TOO_LARGE):
        extract_tarball(src, tmp_path / "repo", max_bytes=100)
    extract_tarball(src, tmp_path / "ok", max_bytes=120)


def test_a_negative_directory_size_cannot_hide_bytes(tmp_path):
    path = tmp_path / "neg.tar.gz"
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        top = tarfile.TarInfo("top")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)
        sneaky = tarfile.TarInfo("top/d")
        sneaky.type = tarfile.DIRTYPE
        sneaky.pax_headers = {"size": "-100000000"}
        tar.addfile(sneaky)
        data = b"x" * 5000
        big = tarfile.TarInfo("top/big")
        big.size = len(data)
        tar.addfile(big, io.BytesIO(data))
    with pytest.raises(ArchiveError):
        extract_tarball(path, tmp_path / "repo", max_bytes=1000)
    assert not (tmp_path / "repo" / "big").exists()


def test_a_directory_with_a_size_is_refused(tmp_path):
    path = tmp_path / "dsize.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, size in (("top", 0), ("top/d", 10)):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.size = size
            tar.addfile(info)
    with pytest.raises(ArchiveError):
        extract_tarball(path, tmp_path / "repo")


@pytest.mark.parametrize("kind", [tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE])
def test_fifos_and_devices_are_refused(tmp_path, kind):
    path = tmp_path / "special.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        top = tarfile.TarInfo("top")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)
        special = tarfile.TarInfo("top/dev")
        special.type = kind
        tar.addfile(special)
    with pytest.raises(ArchiveError, match=archive.HAS_LINKS):
        extract_tarball(path, tmp_path / "repo")


@pytest.mark.parametrize(
    "name", ["top/.git/config", "top/sub/.GIT/hooks/x", "top/.Git", "top/a/.git"]
)
def test_repository_metadata_components_are_refused(tmp_path, name):
    src = build(tmp_path / "g.tar.gz", [("top/ok", b"1"), (name, b"x")])
    with pytest.raises(ArchiveError, match=archive.UNSAFE_PATH):
        extract_tarball(src, tmp_path / "repo")
    assert not (tmp_path / "repo" / "ok").exists()


def test_similar_names_are_allowed(tmp_path):
    src = build(
        tmp_path / "g.tar.gz",
        [("top/.gitignore", b"x"), ("top/.github/ci.yml", b"y"), ("top/a.git", b"z")],
    )
    out = extract_tarball(src, tmp_path / "repo")
    assert (out / ".gitignore").exists() and (out / ".github" / "ci.yml").exists()


def test_case_folded_duplicates_are_refused(tmp_path):
    src = build(tmp_path / "c.tar.gz", [("top/A.txt", b"1"), ("top/a.txt", b"2")])
    with pytest.raises(ArchiveError, match=archive.UNSAFE_PATH):
        extract_tarball(src, tmp_path / "repo")
    exact = build(tmp_path / "e.tar.gz", [("top/a.txt", b"1"), ("top/a.txt", b"2")])
    with pytest.raises(ArchiveError, match=archive.UNSAFE_PATH):
        extract_tarball(exact, tmp_path / "repo2")


def test_a_huge_extended_header_is_bounded_while_reading(tmp_path):
    path = tmp_path / "long.tar.gz"
    with tarfile.open(path, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        top = tarfile.TarInfo("top")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)
        # A GNU long-name record is read whole into memory by tarfile.
        info = tarfile.TarInfo("top/" + "n" * 3_000_000)
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ArchiveError, match=archive.TOO_LARGE):
        extract_tarball(path, tmp_path / "repo", max_files=10, max_bytes=1000)


def test_a_bomb_is_bounded_by_the_decompressed_size(tmp_path):
    path = tmp_path / "bomb.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        top = tarfile.TarInfo("top")
        top.type = tarfile.DIRTYPE
        tar.addfile(top)
        for i in range(3):
            data = b"\0" * 2_000_000
            info = tarfile.TarInfo(f"top/f{i}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    assert path.stat().st_size < 100_000
    with pytest.raises(ArchiveError, match=archive.TOO_LARGE):
        extract_tarball(path, tmp_path / "repo", max_bytes=1_000_000)


def test_an_unreadable_archive_is_refused(tmp_path):
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a tarball")
    with pytest.raises(ArchiveError, match=archive.NOT_AN_ARCHIVE):
        extract_tarball(bad, tmp_path / "repo")
