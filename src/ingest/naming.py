"""Filename policy for uploaded documents.

A document's file name is its identity everywhere downstream: it is the
`source` field of every chunk, the Qdrant payload key, the BM25 record key and
the stem of its JSONL file. So the name has to be safe on Windows, stable
under Unicode normalisation, and matchable by the citation linkifier in the UI,
whose pattern is `\\S+\\.pdf` — a space in the name breaks the link.
"""

from __future__ import annotations

import re
import unicodedata

MAX_NAME_CHARS = 120

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
# Windows-forbidden characters and control characters, plus the two quotes
# encodeURIComponent leaves alone — the UI embeds file names in quoted
# onclick handlers, where a stray ' or ` would end the string.
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\'`\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")
_DASHES = re.compile(r"-{2,}")


def split_name(name: str) -> tuple[str, str]:
    """Return (stem, extension-with-dot lowercased).

    Pure string logic on purpose: PureWindowsPath reads "a:b.pdf" as drive
    "a:" plus "b.pdf", which would silently drop part of a user's file name.
    """
    dot = name.rfind(".")
    if dot < 0:
        return name, ""
    if dot == 0:                      # ".pdf": an extension with no stem
        return "", name.lower()
    stem, ext = name[:dot], name[dot:].lower()
    if ext == ".":                    # "report." — trailing dot, no extension
        return stem, ""
    return stem, ext


def safe_filename(original: str, default_stem: str = "tai-lieu") -> str:
    """Make an uploaded file name safe to store and to cite.

    Vietnamese letters are kept (the reader recognises their own file); only
    characters that Windows forbids, control characters and whitespace are
    replaced. The original name is kept separately in the sidecar.
    """
    name = unicodedata.normalize("NFC", original or "")
    # Keep only the last path component, whichever separator the client used.
    name = re.split(r"[\\/]", name)[-1]
    stem, ext = split_name(name)

    stem = _UNSAFE_CHARS.sub("-", stem)
    stem = _WHITESPACE.sub("-", stem)
    stem = _DASHES.sub("-", stem)
    stem = stem.strip("-. ")
    ext = _UNSAFE_CHARS.sub("", _WHITESPACE.sub("", ext))

    if not stem:
        stem = default_stem
    if stem.casefold() in _WINDOWS_RESERVED:
        stem = f"doc-{stem}"

    budget = MAX_NAME_CHARS - len(ext)
    if len(stem) > budget:
        stem = stem[:budget].rstrip("-. ") or default_stem
    return stem + ext


def stem_key(name: str) -> str:
    """Identity used for uniqueness: JSONL files are named after the stem, and
    Windows does not distinguish case, so two names that differ only there (or
    only in extension) would collide on disk."""
    return unicodedata.normalize("NFC", split_name(name)[0]).casefold()


def unique_name(name: str, taken_stems: set[str]) -> str:
    """Append -2, -3, … until the stem is free."""
    stem, ext = split_name(name)
    if stem_key(name) not in taken_stems:
        return name
    n = 2
    while True:
        suffix = f"-{n}"
        candidate_stem = stem[: MAX_NAME_CHARS - len(ext) - len(suffix)] + suffix
        candidate = candidate_stem + ext
        if stem_key(candidate) not in taken_stems:
            return candidate
        n += 1


def slugify_group(label: str, max_len: int = 60) -> str:
    """Folder-safe ASCII slug for a group label (same rule as the API's
    _slugify, plus a length cap so data/<group>/<file> stays under MAX_PATH)."""
    s = (label or "").strip().replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    s = s[:max_len].rstrip("-")
    return s or "group"
