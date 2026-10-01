"""Leak check for public replay files (design section 5.5).

Standard library only, with no import from ``app`` or ``bench``, so CI can run
it with a bare ``python3``. A hit is reported as ``<file>: <JSON path>: <rule>``
and nothing printed, raised or returned contains the matched text.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

RULES = (
    "host-path",
    "project",
    "gcp-resource",
    "github-token",
    "google-key",
    "jwt",
    "private-key",
    "other-token",
    "high-entropy",
    "email",
    "sandbox",
)
UNCLEARABLE = frozenset({"host-path", "project", "private-key"})
ALLOWED_EMAIL_DOMAINS = frozenset(
    {"example.com", "example.org", "example.net", "example.invalid"}
)
EXACT_RULES_OFF = (
    "exact-value rules off: GOOGLE_CLOUD_PROJECT and REPLAY_REDACT are unset"
)
DEFAULT_DIR = Path("web/public/replays")

_HOST_PATH = re.compile(r"/Users/|/home/|/root/|/var/folders/|C:\\+Users\\")
_GITHUB = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_")
_GOOGLE = re.compile(r"AIza[0-9A-Za-z_-]{35}|ya29\.")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ")
_PRIVATE_KEY = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")
_OTHER = re.compile(r"AKIA[0-9A-Z]{16}|xox[abprs]-|(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}")
_RUN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@"
    r"([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"
)
_SANDBOX = re.compile(
    r"itp-[0-9a-fA-F]{12}|(?<![A-Za-z0-9])[0-9a-f]{64}(?![A-Za-z0-9])"
)
_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
_RAW_RULES = tuple(r for r in RULES if r in UNCLEARABLE)


class ReplayFileError(Exception):
    """A file could not be read or is not JSON. The message names the file only."""


@dataclass(frozen=True)
class Hit:
    file: str
    path: str
    rule: str

    def __str__(self) -> str:
        return f"{self.file}: {self.path}: {self.rule}"


def project_value(environ: Mapping[str, str] = os.environ) -> str:
    """GOOGLE_CLOUD_PROJECT, stripped; empty when unset."""
    return environ.get("GOOGLE_CLOUD_PROJECT", "").strip()


def redact_values(environ: Mapping[str, str] = os.environ) -> tuple[str, ...]:
    """Each comma-separated REPLAY_REDACT entry, stripped; empty ones dropped."""
    parts = (p.strip() for p in environ.get("REPLAY_REDACT", "").split(","))
    return tuple(p for p in parts if p)


def exact_values(environ: Mapping[str, str] = os.environ) -> tuple[str, ...]:
    project = project_value(environ)
    return ((project,) if project else ()) + redact_values(environ)


def exact_pattern(value: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive: the value is not flanked by [A-Za-z0-9].

    `_` counts as a boundary, so `<id>_cloudbuild` is still a match.
    """
    return re.compile(
        r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", re.IGNORECASE
    )


def _entropy(run: str) -> float:
    n = len(run)
    return -sum(c / n * math.log2(c / n) for c in Counter(run).values())


def _high_entropy(text: str) -> bool:
    for m in _RUN.finditer(text):
        run = m.group()
        if (
            any(c.isupper() for c in run)
            and any(c.islower() for c in run)
            and any(c.isdigit() for c in run)
            and _entropy(run) > 4.0
        ):
            return True
    return False


def _email(text: str) -> bool:
    return any(
        m.group(1).lower() not in ALLOWED_EMAIL_DOMAINS for m in _EMAIL.finditer(text)
    )


def _gcp_resource(text: str) -> bool:
    return bool(re.search(r"projects/(?!<project>)", text)) or (
        ".iam.gserviceaccount.com" in text
    )


def scan_text(text: str, exact: Sequence[str] = ()) -> list[str]:
    """The rules hit by ``text``, in RULES order, each once."""
    tests = {
        "host-path": lambda: bool(_HOST_PATH.search(text)),
        "project": lambda: any(exact_pattern(v).search(text) for v in exact),
        "gcp-resource": lambda: _gcp_resource(text),
        "github-token": lambda: bool(_GITHUB.search(text)),
        "google-key": lambda: bool(_GOOGLE.search(text)),
        "jwt": lambda: bool(_JWT.search(text)),
        "private-key": lambda: bool(_PRIVATE_KEY.search(text)),
        "other-token": lambda: bool(_OTHER.search(text)),
        "high-entropy": lambda: _high_entropy(text),
        "email": lambda: _email(text),
        "sandbox": lambda: bool(_SANDBOX.search(text)),
    }
    return [rule for rule in RULES if tests[rule]()]


def _key_segment(key: str, exact: Sequence[str]) -> tuple[str, bool]:
    """The path segment for ``key`` and whether the key itself hits a rule.

    A key that hits is never printed: it becomes ``{key}``.
    """
    if scan_text(key, exact):
        return "{key}", True
    if _PLAIN_KEY.fullmatch(key):
        return f".{key}", False
    return f"[{json.dumps(key)}]", False


def scan_value(
    value: object,
    *,
    file: str,
    exact: Sequence[str] = (),
    allow: Collection[tuple[str, str]] = (),
) -> list[Hit]:
    """Every string and every object key of ``value``, with its JSON path."""
    hits: list[Hit] = []

    def report(path: str, text: str) -> None:
        for rule in scan_text(text, exact):
            if rule not in UNCLEARABLE and (path, rule) in allow:
                continue
            hits.append(Hit(file, path, rule))

    def walk(node: object, path: str) -> None:
        if isinstance(node, str):
            report(path, node)
        elif isinstance(node, dict):
            for key, child in node.items():
                key = str(key)
                segment, key_hit = _key_segment(key, exact)
                if key_hit:
                    report(path + "{key}", key)
                walk(child, path + segment)
        elif isinstance(node, (list, tuple)):
            for i, child in enumerate(node):
                walk(child, f"{path}[{i}]")

    walk(value, "$")
    return hits


def _load_json(path: Path) -> tuple[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
        return text, json.loads(text)
    except (OSError, ValueError):
        raise ReplayFileError(f"{path.name}: cannot be read or is not JSON") from None


def _read_allow(index_path: Path) -> dict[str, frozenset[tuple[str, str]]]:
    """``replays[].file`` -> ``replays[].allow[{path, rule}]`` from index.json."""
    _, data = _load_json(index_path)
    allow: dict[str, frozenset[tuple[str, str]]] = {}
    try:
        for entry in data["replays"]:  # type: ignore[index]
            pairs = frozenset((a["path"], a["rule"]) for a in entry.get("allow", []))
            allow[entry["file"]] = allow.get(entry["file"], frozenset()) | pairs
    except (KeyError, TypeError, AttributeError):
        raise ReplayFileError(f"{index_path.name}: unexpected shape") from None
    return allow


def _expand(paths: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        files.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])
    return files


def check_paths(paths: Sequence[Path], *, exact: Sequence[str] = ()) -> list[Hit]:
    """Scan every file; directories expand to their ``*.json``.

    ``allow`` comes from the ``index.json`` beside each file, when there is one.
    Each file is decoded and every string scanned, then its raw text is scanned
    with the unclearable rules only, under path ``$``.
    """
    hits: list[Hit] = []
    allow_by_dir: dict[Path, dict[str, frozenset[tuple[str, str]]]] = {}
    for path in _expand(paths):
        index = path.parent / "index.json"
        if path.parent not in allow_by_dir:
            allow_by_dir[path.parent] = _read_allow(index) if index.is_file() else {}
        text, data = _load_json(path)
        allow = allow_by_dir[path.parent].get(path.name, frozenset())
        hits += scan_value(data, file=path.name, exact=exact, allow=allow)
        hits += [
            Hit(path.name, "$", rule)
            for rule in scan_text(text, exact)
            if rule in _RAW_RULES
        ]
    return hits


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [Path(a) for a in args] or [DEFAULT_DIR]
    exact = exact_values()
    if not exact:
        print(EXACT_RULES_OFF)
    try:
        files = _expand(paths)
        hits = check_paths(paths, exact=exact)
    except ReplayFileError as err:
        print(err, file=sys.stderr)
        return 2
    for hit in hits:
        print(hit)
    print(f"{len(files)} files checked, {len(hits)} problems")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
