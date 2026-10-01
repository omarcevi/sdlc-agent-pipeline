"""Safe extraction of a repository tarball downloaded from GitHub.

Host-side, so it only ever writes plain files and directories under `dest`: no
links, no special files, no `.git` metadata, no path that leaves `dest`, no names
that differ only by case, bounded file count and size. Only gzip (what GitHub serves)
and plain tar are read. The decompressed stream is bounded while it is read, so
memory use is bounded too: tarfile reads an extended (long-name, pax) header whole,
and such a header costs about 7 times its size in memory, so the worst case is about
7 times the stream limit (roughly 400 MB for the default caps), not unbounded. The
gzip checksum is verified. Error messages are fixed strings (no archive content in
them).
"""

import gzip
import os
import tarfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.textfold import fold

NOT_AN_ARCHIVE = "the archive cannot be read"
UNSAFE_PATH = "the archive contains an unsafe path"
HAS_LINKS = "the archive contains a link or special file"
TOO_MANY_FILES = "the archive has too many files"
TOO_LARGE = "the archive is too large"
NO_SINGLE_TOP_DIR = "the archive must have exactly one top-level directory"
INVALID_MEMBER = "the archive contains an invalid entry"

# What the tar format adds around file data: a header block and padding per member,
# and room for extended (long-name, pax) headers.
_PER_MEMBER_OVERHEAD = 2048
_STREAM_SLACK = 1_048_576


class ArchiveError(Exception):
    """The archive is unusable or unsafe. The message is one of the fixed strings."""


class _Limited:
    """Reads from `source` and refuses to deliver more than `limit` bytes. As a
    context manager it closes `source` and the `raw` file under it."""

    def __init__(self, source: BinaryIO, raw: BinaryIO, limit: int) -> None:
        self._source = source
        self._raw = raw
        self._left = limit

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0 or size > self._left + 1:
            size = self._left + 1
        data = self._source.read(size)
        self._left -= len(data)
        if self._left < 0:
            raise ArchiveError(TOO_LARGE)
        return data

    def __enter__(self) -> "_Limited":
        return self

    def __exit__(self, *exc: object) -> None:
        for handle in (self._source, self._raw):
            handle.close()


def _decompressed(archive: Path, limit: int) -> _Limited:
    raw = archive.open("rb")
    try:
        magic = raw.read(2)
        raw.seek(0)
        source: BinaryIO = raw
        if magic == b"\x1f\x8b":
            source = gzip.GzipFile(fileobj=raw)
    except BaseException:
        raw.close()
        raise
    return _Limited(source, raw, limit)


def _parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or "\x00" in name:
        raise ArchiveError(UNSAFE_PATH)
    parts = tuple(part for part in path.parts if part != ".")
    if ".." in parts:
        raise ArchiveError(UNSAFE_PATH)
    # Repository metadata: a `.git` directory could carry config that makes a later
    # host-side git command run a program.
    if any(fold(part).casefold() == ".git" for part in parts):
        raise ArchiveError(UNSAFE_PATH)
    return parts


@contextmanager
def _open(archive: Path, limit: int) -> Iterator[tuple[tarfile.TarFile, _Limited]]:
    with _decompressed(archive, limit) as stream:
        try:
            tar = tarfile.open(fileobj=stream, mode="r|")  # ty: ignore[no-matching-overload]
        except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
            raise ArchiveError(NOT_AN_ARCHIVE) from exc
        with tar:
            yield tar, stream


def _check_member(member: tarfile.TarInfo) -> tuple[str, ...]:
    if member.issym() or member.islnk():
        raise ArchiveError(HAS_LINKS)
    if not (member.isreg() or member.isdir()):
        raise ArchiveError(HAS_LINKS)
    if member.size < 0 or (member.isdir() and member.size != 0):
        raise ArchiveError(INVALID_MEMBER)
    parts = _parts(member.name)
    if not parts:
        raise ArchiveError(UNSAFE_PATH)
    return parts


def extract_tarball(
    archive: Path,
    dest: Path,
    *,
    max_files: int = 5000,
    max_bytes: int = 50_000_000,
) -> Path:
    """Extract `archive` into `dest`, dropping its single top-level directory.

    Two passes over the bounded stream: the first checks every member and writes
    nothing, the second extracts.
    """
    archive, dest = Path(archive), Path(dest)
    limit = max_bytes + max_files * _PER_MEMBER_OVERHEAD + _STREAM_SLACK
    try:
        with _open(archive, limit) as (tar, stream):
            tops: set[str] = set()
            seen: set[str] = set()
            count = total = 0
            for member in tar:
                if count >= max_files:
                    raise ArchiveError(TOO_MANY_FILES)
                count += 1
                parts = _check_member(member)
                tops.add(parts[0])
                if member.isreg():
                    if len(parts) == 1:
                        raise ArchiveError(NO_SINGLE_TOP_DIR)
                    key = fold("/".join(parts)).casefold()
                    if key in seen:
                        raise ArchiveError(UNSAFE_PATH)
                    seen.add(key)
                    total += member.size
                    if total > max_bytes:
                        raise ArchiveError(TOO_LARGE)
            if len(tops) != 1:
                raise ArchiveError(NO_SINGLE_TOP_DIR)
            while stream.read(65536):  # to the end, so the gzip checksum is checked
                pass
        dest.mkdir(parents=True, exist_ok=True)
        with _open(archive, limit) as (tar, _stream):
            for member in tar:
                parts = _check_member(member)
                if len(parts) == 1:
                    continue  # the top-level directory entry itself
                relative = "/".join(parts[1:])
                tar.extract(member.replace(name=relative), dest, filter="data")
        if _tree_bytes(dest) > max_bytes:
            raise ArchiveError(TOO_LARGE)
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise ArchiveError(NOT_AN_ARCHIVE) from exc
    return dest


def _tree_bytes(root: Path) -> int:
    """Bytes actually written under `root`."""
    total = 0
    for directory, _dirs, files in os.walk(root):
        for name in files:
            total += (Path(directory) / name).stat().st_size
    return total
