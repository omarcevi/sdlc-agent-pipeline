"""Safe extraction of a repository tarball downloaded from GitHub.

Host-side, so it only ever writes plain files and directories under `dest`: no
links, no special files, no path that leaves `dest`, bounded file count and size.
Error messages are fixed strings (no archive content in them).
"""

import tarfile
from pathlib import Path, PurePosixPath

NOT_AN_ARCHIVE = "the archive cannot be read"
UNSAFE_PATH = "the archive contains an unsafe path"
HAS_LINKS = "the archive contains a link or special file"
TOO_MANY_FILES = "the archive has too many files"
TOO_LARGE = "the archive is too large"
NO_SINGLE_TOP_DIR = "the archive must have exactly one top-level directory"


class ArchiveError(Exception):
    """The archive is unusable or unsafe. The message is one of the fixed strings."""


def _parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or "\x00" in name:
        raise ArchiveError(UNSAFE_PATH)
    parts = tuple(part for part in path.parts if part != ".")
    if ".." in parts:
        raise ArchiveError(UNSAFE_PATH)
    return parts


def extract_tarball(
    archive: Path,
    dest: Path,
    *,
    max_files: int = 5000,
    max_bytes: int = 50_000_000,
) -> Path:
    """Extract `archive` into `dest`, dropping its single top-level directory."""
    archive, dest = Path(archive), Path(dest)
    try:
        tar = tarfile.open(archive, "r:*")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveError(NOT_AN_ARCHIVE) from exc
    with tar:
        try:
            members = []
            total = 0
            for member in tar:
                if len(members) >= max_files:
                    raise ArchiveError(TOO_MANY_FILES)
                if member.issym() or member.islnk():
                    raise ArchiveError(HAS_LINKS)
                if not (member.isreg() or member.isdir()):
                    raise ArchiveError(HAS_LINKS)
                parts = _parts(member.name)
                if not parts:
                    raise ArchiveError(UNSAFE_PATH)
                total += member.size
                if total > max_bytes:
                    raise ArchiveError(TOO_LARGE)
                members.append((member, parts))
            tops = {parts[0] for _, parts in members}
            if len(tops) != 1:
                raise ArchiveError(NO_SINGLE_TOP_DIR)
            # A regular file at the top level is not a directory.
            if any(len(parts) == 1 and member.isreg() for member, parts in members):
                raise ArchiveError(NO_SINGLE_TOP_DIR)
            dest.mkdir(parents=True, exist_ok=True)
            for member, parts in members:
                if len(parts) == 1:
                    continue  # the top-level directory entry itself
                relative = "/".join(parts[1:])
                tar.extract(member.replace(name=relative), dest, filter="data")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise ArchiveError(NOT_AN_ARCHIVE) from exc
    return dest
