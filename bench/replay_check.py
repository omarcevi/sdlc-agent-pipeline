"""Leak check for public replay files (design section 5.5).

Standard library only, with no import from ``app`` or ``bench``, so CI can run
it with a bare ``python3``. A hit is reported as ``<file>: <JSON path>: <rule>``
and nothing printed, raised or returned contains the matched text.

Documented limits (not detected): lower-case 32-hex secrets, upper-case 64-hex
or 12-hex container ids, URL-encoded paths and addresses, look-alike
(homoglyph) letters, and a secret diluted below 4.0 bits per character by a
long low-entropy run next to it. A file with a repeated JSON key is refused. The invisible-character second
scan covers Cf (format) characters only. A string that is exactly a bench run id
(or ``<run-id>.json``) is exempt from the ``high-entropy`` rule, and from no other.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
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

_HOST_PATH = re.compile(
    r"[/\\]{1,2}(?:Users|home|root)[/\\]{1,2}"
    r"|[/\\]{1,2}var[/\\]{1,2}folders[/\\]{1,2}"
    r"|[A-Za-z]:[/\\]{1,2}(?i:users)[/\\]{1,2}"
)
_GITHUB = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_")
_GOOGLE = re.compile(r"AIza[0-9A-Za-z_-]{35}|ya29\.")
_DOTTED = re.compile(r"[A-Za-z0-9_.-]+")
_PRIVATE_KEY = re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
_OTHER = re.compile(
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|xox[abprs]-"
    r"|sk-[A-Za-z0-9]{20,}|sk-(?:proj|ant)-[A-Za-z0-9_-]{20,}"
)
_INVISIBLE_MARKER = re.compile(r"<U\+[0-9A-Fa-f]{4,6}>")
_RUN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[._%+-]*[A-Za-z0-9][A-Za-z0-9._%+-]*@"
    r"([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"
)
_SANDBOX = re.compile(
    r"itp-[0-9a-fA-F]{12}|(?<![A-Za-z0-9])[0-9a-f]{64}(?![A-Za-z0-9])"
)
_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
# A run id is a public identifier by design (index.json, file names), and some trip
# the entropy rule. A string that is exactly a run id, or its file name, is exempt
# from `high-entropy` only. Its free parts are two lower-case letters and digits, so
# it cannot carry a mixed-case token; every other rule still applies to it.
_RUN_ID_SHAPE = re.compile(
    r"[a-z]{2}-\d{3}-(?:multi|single|review)-(?:flash|pro|mixed)(?:-rp-\d{2})?"
    r"-r\d+-\d{8}T\d{6}Z(?:\.json)?",
    re.ASCII,
)
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

    `_` counts as a boundary, so `<id>_cloudbuild` is still a match, and so does
    a literal escape (`\\n`, `\\t`, ...) before the value, as in `repr()` output.
    """
    return re.compile(
        r"(?:(?<![A-Za-z0-9])|(?<=\\[nrtbf]))" + re.escape(value) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
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


def _jwt(text: str) -> bool:
    """`eyJ<base64url>` then a segment starting `eyJ`, found in linear time."""
    for run in _DOTTED.finditer(text):
        if "." not in run.group():
            continue
        segs = run.group().split(".")
        for a, b in pairwise(segs):
            if b.startswith("eyJ") and "eyJ" in a[:-1]:
                return True
    return False


def _gcp_resource(text: str) -> bool:
    return bool(re.search(r"projects/(?!<project>)", text)) or (
        ".iam.gserviceaccount.com" in text
    )


def _strip_invisible(text: str) -> str:
    """``text`` without Cf (format, zero-width) characters and ``<U+XXXX>`` markers."""
    if text.isascii() and "<U+" not in text:
        return text
    text = _INVISIBLE_MARKER.sub("", text)
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def scan_text(
    text: str, exact: Sequence[str] = (), *, only: Collection[str] | None = None
) -> list[str]:
    """The rules hit by ``text``, in RULES order, each once.

    The text is scanned twice: as given, and with invisible characters removed,
    so a token split by a zero-width character is still caught. ``only`` limits
    the rules that run.
    """
    plain = _rules_hit(text, exact, only)
    stripped = _strip_invisible(text)
    if stripped == text:
        return plain
    both = set(plain) | set(_rules_hit(stripped, exact, only))
    return [rule for rule in RULES if rule in both]


def _rules_hit(
    text: str, exact: Sequence[str], only: Collection[str] | None
) -> list[str]:
    tests = {
        "host-path": lambda: bool(_HOST_PATH.search(text)),
        "project": lambda: any(exact_pattern(v).search(text) for v in exact),
        "gcp-resource": lambda: _gcp_resource(text),
        "github-token": lambda: bool(_GITHUB.search(text)),
        "google-key": lambda: bool(_GOOGLE.search(text)),
        "jwt": lambda: _jwt(text),
        "private-key": lambda: bool(_PRIVATE_KEY.search(text)),
        "other-token": lambda: bool(_OTHER.search(text)),
        "high-entropy": lambda: (
            _RUN_ID_SHAPE.fullmatch(text) is None and _high_entropy(text)
        ),
        "email": lambda: _email(text),
        "sandbox": lambda: bool(_SANDBOX.search(text)),
    }
    return [r for r in RULES if (only is None or r in only) and tests[r]()]


def _key_segments(key: str, index: int, exact: Sequence[str]) -> tuple[str, str, bool]:
    """(path of the key itself, path prefix of its value, whether the key hits).

    A key that hits is never printed: it is numbered by its position in the
    object (``{key:2}`` for the key, ``{value:2}`` for what it holds).
    """
    if scan_text(key, exact):
        return f"{{key:{index}}}", f"{{value:{index}}}", True
    if _PLAIN_KEY.fullmatch(key):
        return f".{key}", f".{key}", False
    segment = f"[{json.dumps(key)}]"
    return segment, segment, False


def scan_value(
    value: object,
    *,
    file: str,
    exact: Sequence[str] = (),
    allow: Collection[tuple[str, str]] = (),
) -> list[Hit]:
    """Every string and every object key of ``value``, with its JSON path.

    Iterative, so a deeply nested value cannot overflow the stack.
    """
    hits: list[Hit] = []

    def report(path: str, text: str) -> None:
        for rule in scan_text(text, exact):
            if rule not in UNCLEARABLE and (path, rule) in allow:
                continue
            hits.append(Hit(file, path, rule))

    stack: list[tuple[object, str]] = [(value, "$")]
    while stack:
        node, path = stack.pop()
        if isinstance(node, str):
            report(path, node)
        elif isinstance(node, dict):
            children = []
            for i, (key, child) in enumerate(node.items()):
                key_path, value_path, key_hit = _key_segments(str(key), i, exact)
                if key_hit:
                    report(path + key_path, str(key))
                children.append((child, path + value_path))
            stack.extend(reversed(children))
        elif isinstance(node, (list, tuple)):
            stack.extend(
                (child, f"{path}[{i}]") for i, child in reversed(list(enumerate(node)))
            )
    return hits


class _DuplicateKeyError(ValueError):
    pass


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, val in pairs:
        if key in out:
            raise _DuplicateKeyError
        out[key] = val
    return out


def _load_json(path: Path) -> tuple[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
        return text, json.loads(text, object_pairs_hook=_no_duplicates)
    except _DuplicateKeyError:
        raise ReplayFileError(f"{path.name}: duplicate key") from None
    except (OSError, ValueError, RecursionError):
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


def _expand(paths: Sequence[Path]) -> list[tuple[Path, str]]:
    """(file, name shown in reports): relative to the scanned directory."""
    files: list[tuple[Path, str]] = []
    for p in paths:
        if p.is_dir():
            found = sorted(p.rglob("*.json"))
            files.extend((f, f.relative_to(p).as_posix()) for f in found)
        else:
            files.append((p, p.name))
    return files


def check_paths(paths: Sequence[Path], *, exact: Sequence[str] = ()) -> list[Hit]:
    """Scan every file; directories expand to every ``*.json`` under them.

    ``allow`` comes from the ``index.json`` beside each file, when there is one.
    Each file is decoded and every string scanned, then its raw text is scanned
    with the unclearable rules only, under path ``$``. Hits name the file
    relative to the scanned directory. No file at all is an error.
    """
    files = _expand(paths)
    if not files:
        raise ReplayFileError("no replay files found")
    hits: list[Hit] = []
    allow_by_dir: dict[Path, dict[str, frozenset[tuple[str, str]]]] = {}
    for path, shown in files:
        index = path.parent / "index.json"
        if path.parent not in allow_by_dir:
            allow_by_dir[path.parent] = _read_allow(index) if index.is_file() else {}
        text, data = _load_json(path)
        allow = allow_by_dir[path.parent].get(path.name, frozenset())
        hits += scan_value(data, file=shown, exact=exact, allow=allow)
        hits += [
            Hit(shown, "$", rule) for rule in scan_text(text, exact, only=_RAW_RULES)
        ]
    return hits


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [Path(a) for a in args] or [DEFAULT_DIR]
    exact = exact_values()
    if not exact:
        print(EXACT_RULES_OFF)
    try:
        hits = check_paths(paths, exact=exact)
        count = len(_expand(paths))
    except ReplayFileError as err:
        print(err, file=sys.stderr)
        return 2
    for hit in hits:
        print(hit)
    print(f"{count} files checked, {len(hits)} problems")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
