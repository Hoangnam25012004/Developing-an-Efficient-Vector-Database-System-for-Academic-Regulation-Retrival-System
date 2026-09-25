"""Where ingestion state lives, and the merged view of every document.

A document is known from up to four places that can disagree: the file in
data/<group>/, its sidecar (<file>.<ext>.meta.json), the chunks it has in
data/processed/<stem>.jsonl, and any job that is about to change it. The
registry reconciles them so the UI can show one status per document —
including the ones that are uploaded but not yet searchable, which the old
chunk-derived listing could not show at all.

Internal state is kept under data/processed/_ingest/. Every reader of chunk
files globs `processed/*.jsonl` non-recursively, and both the parser and the
group listing skip `processed*` folders, so nothing here can leak into the
index or show up as a group.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .naming import stem_key

SUPPORTED_EXTS = (".pdf", ".docx")


# ─── Paths ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IngestPaths:
    raw_dir: Path
    processed_dir: Path
    ingest_dir: Path
    staging_dir: Path
    jobs_dir: Path
    failed_dir: Path
    tmp_dir: Path
    groups_json: Path
    hash_cache: Path
    index_state: Path

    @classmethod
    def from_cfg(cls, cfg: dict) -> "IngestPaths":
        raw = Path(cfg["data"]["raw_dir"])
        processed = Path(cfg["data"]["processed_dir"])
        ingest = processed / "_ingest"
        return cls(
            raw_dir=raw,
            processed_dir=processed,
            ingest_dir=ingest,
            staging_dir=ingest / "staging",
            jobs_dir=ingest / "jobs",
            failed_dir=ingest / "failed",
            tmp_dir=ingest / "tmp",
            groups_json=raw / "groups.json",
            hash_cache=ingest / "hash_cache.json",
            index_state=ingest / "index_state.json",
        )

    def ensure(self) -> None:
        for d in (self.staging_dir, self.jobs_dir, self.failed_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)


def skip_dir_names(cfg: dict) -> set[str]:
    return {Path(cfg["data"]["processed_dir"]).name, "processed", "processed_v2"}


def is_internal_dir(name: str, cfg: dict | None = None) -> bool:
    """Folders under data/ that are not document groups."""
    if name.startswith((".", "_")) or name.lower().startswith("processed"):
        return True
    return bool(cfg) and name in skip_dir_names(cfg)


# ─── Atomic file writes (Windows-safe) ──────────────────────────────────────

def replace_with_retry(src: Path, dst: Path, attempts: int = 10, delay: float = 0.2) -> None:
    """os.replace, retried: on Windows a reader holding `dst` open makes the
    replace fail with PermissionError for as long as the read lasts."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    replace_with_retry(tmp, path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


# ─── Groups registry (data/groups.json) ─────────────────────────────────────

def load_groups(paths: IngestPaths) -> dict:
    data = read_json(paths.groups_json, {})
    return data if isinstance(data, dict) else {}


def save_groups(paths: IngestPaths, reg: dict) -> None:
    atomic_write_json(paths.groups_json, reg)


# ─── Raw documents and sidecars ─────────────────────────────────────────────

@dataclass(frozen=True)
class DocFile:
    path: Path
    group: str          # first folder under raw_dir; "" at the root
    source: str         # file name — the key used by every index

    @property
    def ext(self) -> str:
        return self.path.suffix.lower()


def sidecar_path(doc_path: Path) -> Path:
    # Same convention the delete endpoint already used: <name>.<ext>.meta.json
    return doc_path.with_name(doc_path.name + ".meta.json")


def load_sidecar(doc_path: Path) -> dict | None:
    data = read_json(sidecar_path(doc_path))
    return data if isinstance(data, dict) else None


def save_sidecar(doc_path: Path, data: dict) -> None:
    atomic_write_json(sidecar_path(doc_path), data)


def _is_document_file(p: Path) -> bool:
    # "~$name.docx" is the lock file Word keeps next to an open document.
    return p.is_file() and p.suffix.lower() in SUPPORTED_EXTS and not p.name.startswith("~$")


def list_raw_documents(cfg: dict) -> list[DocFile]:
    """Every document the parser would read, with the group it belongs to."""
    raw = Path(cfg["data"]["raw_dir"])
    if not raw.is_dir():
        return []
    docs: list[DocFile] = []
    for entry in sorted(raw.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            if is_internal_dir(entry.name, cfg):
                continue
            for p in sorted(entry.rglob("*"), key=lambda q: str(q).lower()):
                if _is_document_file(p) and not any(
                    is_internal_dir(part, cfg) for part in p.relative_to(raw).parts[:-1]
                ):
                    docs.append(DocFile(p, entry.name, p.name))
        elif _is_document_file(entry):
            docs.append(DocFile(entry, "", entry.name))
    return docs


def find_document(cfg: dict, source: str) -> DocFile | None:
    docs = list_raw_documents(cfg)
    for d in docs:
        if d.source == source:
            return d
    folded = source.casefold()
    for d in docs:
        if d.source.casefold() == folded:
            return d
    return None


def group_file_counts(cfg: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in list_raw_documents(cfg):
        counts[d.group] = counts.get(d.group, 0) + 1
    return counts


# ─── Chunk statistics from data/processed/*.jsonl ───────────────────────────

_STATS_CACHE: dict[str, tuple[int, int, dict]] = {}


def _jsonl_file_stats(path: Path) -> dict:
    """Per-source aggregate of one JSONL file (normally a single source)."""
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = c.get("source") or "unknown"
            s = out.get(src)
            if s is None:
                s = out[src] = {
                    "chunk_count": 0,
                    "max_page": 0,
                    "doc_group": c.get("doc_group") or "",
                    "doc_type": c.get("doc_type") or "",
                    "doc_number": c.get("doc_number") or "",
                    "issuing_body": c.get("issuing_body") or "",
                    "language": c.get("language") or "",
                    "file_type": c.get("file_type") or "",
                    "jsonl": path.name,
                }
            s["chunk_count"] += 1
            pg = c.get("page") or 0
            if isinstance(pg, int) and pg > s["max_page"]:
                s["max_page"] = pg
    return out


def jsonl_stats(processed_dir: Path) -> dict[str, dict]:
    """source → chunk statistics, cached per file by (mtime, size)."""
    result: dict[str, dict] = {}
    seen: set[str] = set()
    if not processed_dir.is_dir():
        return result
    for path in sorted(processed_dir.glob("*.jsonl")):
        key = str(path)
        seen.add(key)
        try:
            st = path.stat()
        except OSError:
            continue
        cached = _STATS_CACHE.get(key)
        if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
            stats = cached[2]
        else:
            try:
                stats = _jsonl_file_stats(path)
            except OSError:
                continue
            _STATS_CACHE[key] = (st.st_mtime_ns, st.st_size, stats)
        for src, s in stats.items():
            if src in result:          # two files claiming one source: add up
                result[src]["chunk_count"] += s["chunk_count"]
                result[src]["max_page"] = max(result[src]["max_page"], s["max_page"])
            else:
                result[src] = dict(s)
    for key in list(_STATS_CACHE):
        if key not in seen:
            del _STATS_CACHE[key]
    return result


def jsonl_path_for(processed_dir: Path, source: str) -> Path:
    return processed_dir / (Path(source).stem + ".jsonl")


def sources_in_jsonl(path: Path) -> set[str]:
    try:
        return set(_jsonl_file_stats(path))
    except OSError:
        return set()


def taken_stems(cfg: dict) -> dict[str, DocFile | None]:
    """stem_key → document, for uniqueness checks. JSONL-only sources are
    included too: a new file with the same stem would overwrite their JSONL."""
    taken: dict[str, DocFile | None] = {}
    for d in list_raw_documents(cfg):
        taken.setdefault(stem_key(d.source), d)
    for src in jsonl_stats(Path(cfg["data"]["processed_dir"])):
        taken.setdefault(stem_key(src), None)
    return taken


# ─── Content hashes (duplicate detection) ───────────────────────────────────

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def content_hashes(cfg: dict, paths: IngestPaths) -> dict[str, DocFile]:
    """sha256 → document, for every stored document. Sidecars carry the hash
    of documents uploaded through v2; the rest are hashed once and cached by
    (size, mtime)."""
    cache = read_json(paths.hash_cache, {}) or {}
    changed = False
    out: dict[str, DocFile] = {}
    for d in list_raw_documents(cfg):
        side = load_sidecar(d.path) or {}
        digest = side.get("sha256")
        try:
            st = d.path.stat()
        except OSError:
            continue
        if not digest or side.get("size_bytes") != st.st_size:
            entry = cache.get(str(d.path))
            if entry and entry.get("size") == st.st_size and entry.get("mtime_ns") == st.st_mtime_ns:
                digest = entry["sha256"]
            else:
                digest = sha256_file(d.path)
                cache[str(d.path)] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
                changed = True
        out.setdefault(digest, d)
    if changed:
        try:
            atomic_write_json(paths.hash_cache, cache)
        except OSError:
            pass
    return out


# ─── The merged registry ────────────────────────────────────────────────────

def _job_status_by_source(active_jobs: Iterable[dict]) -> dict[str, tuple[str, dict]]:
    """source → (derived status, job) for documents an unfinished job will touch."""
    out: dict[str, tuple[str, dict]] = {}
    for job in active_jobs:
        running = job.get("status") == "running"
        jtype = job.get("type")
        for item in (job.get("params") or {}).get("items", []):
            src = item.get("source")
            if not src:
                continue
            if jtype == "remove":
                out[src] = ("removing", job)
            else:
                out[src] = ("processing" if running else "queued", job)
    return out


def build_registry(cfg: dict, active_jobs: Iterable[dict] = ()) -> list[dict]:
    paths = IngestPaths.from_cfg(cfg)
    groups = load_groups(paths)
    stats = jsonl_stats(paths.processed_dir)
    job_state = _job_status_by_source(list(active_jobs))

    rows: list[dict] = []
    seen: set[str] = set()
    for d in list_raw_documents(cfg):
        seen.add(d.source)
        side = load_sidecar(d.path)
        st = stats.get(d.source)
        g = groups.get(d.group, {})
        meta = (side or {}).get("metadata") or {}
        try:
            size = d.path.stat().st_size
        except OSError:
            size = 0

        if side:
            status = side.get("status") or ("indexed" if st else "not_indexed")
        else:
            status = "indexed" if st and st["chunk_count"] > 0 else "not_indexed"
        detail = (side or {}).get("status_detail") or {}
        if d.source in job_state:
            status, job = job_state[d.source]
            detail = {"step": (job.get("progress") or {}).get("step"), "job_id": job.get("id")}

        rows.append({
            "source": d.source,
            "group": d.group,
            "file_type": d.ext.lstrip("."),
            "size": size,
            "status": status,
            "status_detail": detail,
            "language": (side or {}).get("language") or (st or {}).get("language") or "",
            "title": meta.get("title") or "",
            "doc_type": (st or {}).get("doc_type") or meta.get("doc_type") or g.get("doc_type", ""),
            "doc_number": (st or {}).get("doc_number") or meta.get("doc_number") or "",
            "issuing_body": (st or {}).get("issuing_body") or meta.get("issuing_body") or g.get("issuing_body", ""),
            "chunk_count": (st or {}).get("chunk_count", 0),
            "max_page": (st or {}).get("max_page", 0),
            "uploaded_at": (side or {}).get("uploaded_at"),
            "indexed_at": (side or {}).get("indexed_at"),
            "original_filename": (side or {}).get("original_filename"),
            "warnings": ((side or {}).get("report") or {}).get("warnings", []),
            "managed": side is not None,
        })

    # Index data without a file: left behind by a delete, or being removed now.
    for src, st in stats.items():
        if src in seen:
            continue
        status = "orphan"
        detail: dict = {}
        if src in job_state:
            status, job = job_state[src]
            detail = {"step": (job.get("progress") or {}).get("step"), "job_id": job.get("id")}
        rows.append({
            "source": src,
            "group": st.get("doc_group") or "",
            "file_type": Path(src).suffix.lower().lstrip("."),
            "size": 0,
            "status": status,
            "status_detail": detail,
            "language": st.get("language") or "",
            "title": "",
            "doc_type": st.get("doc_type") or "",
            "doc_number": st.get("doc_number") or "",
            "issuing_body": st.get("issuing_body") or "",
            "chunk_count": st.get("chunk_count", 0),
            "max_page": st.get("max_page", 0),
            "uploaded_at": None,
            "indexed_at": None,
            "original_filename": None,
            "warnings": [],
            "managed": False,
        })
    return rows
