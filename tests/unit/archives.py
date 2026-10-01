"""Tar archive builders shared by the archive, intake and delivery tests. Not a test
module."""

import io
import tarfile
from pathlib import Path


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


def make_archive(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, content in {"acme-widgets-aaaa/": None, **files}.items():
            if content is None:
                info = tarfile.TarInfo(name.rstrip("/"))
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
                continue
            data = content.encode()
            info = tarfile.TarInfo(f"acme-widgets-aaaa/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path
