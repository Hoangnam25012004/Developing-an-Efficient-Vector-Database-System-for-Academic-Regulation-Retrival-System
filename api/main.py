"""
FastAPI backend for ARRS, the Academic Regulation Retrieval System.

Endpoints:
  GET  /health        – health check
  POST /chat          – full query (retrieve + rerank + verbatim answer, no LLM)
  POST /retrieve      – retrieve + rerank only (no answer)
  GET  /search        – search with selectable mode (hybrid_rerank|hybrid|dense|sparse)
  GET  /stats         – corpus statistics from processed JSONL files
  GET  /sources       – list source documents with metadata
  GET  /pdf/{filename} – serve PDF file from data dir
  GET  /config        – read config.yaml as JSON
  PUT  /config        – write config.yaml from JSON body
  POST /evaluate      – run evaluation suite
"""

import asyncio
import hashlib
import importlib
import json
import os
import pickle
import re
import shutil
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from qdrant_client import QdrantClient

from src.chat.rag_chain import RAGChain
from src.evaluation.evaluator import run_evaluation
from src.ingest import naming as ing_naming
from src.ingest import registry as ing_reg
from src.ingest import validate as ing_val
from src.ingest.jobs import ACTIVE as JOB_ACTIVE
from src.ingest.jobs import JobRunner, JobStore, now_iso

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.yaml")

app = FastAPI(
    title="ARRS – Academic Regulation Retrieval API",
    description="Hybrid vector + BM25 search, cross-encoder reranking and verbatim answers over Vietnamese university regulations",
    version="2.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
IMG_DIR = Path(__file__).resolve().parent.parent / "img"

if IMG_DIR.is_dir():
    app.mount("/img", StaticFiles(directory=str(IMG_DIR)), name="img")


@app.get("/")
def root():
    """Serve the new static UI."""
    ui_index = UI_DIR / "index.html"
    if ui_index.exists():
        # Disable caching so UI edits are picked up without hard reload during dev.
        return FileResponse(
            str(ui_index),
            media_type="text/html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    raise HTTPException(status_code=404, detail="UI not found")

# ─── Lazy-loaded components ───────────────────────────────────────────────────
_chain: Optional[RAGChain] = None
_chain_lock = threading.Lock()


def get_chain() -> RAGChain:
    global _chain
    if _chain is None:
        with _chain_lock:
            if _chain is None:
                _chain = RAGChain(CONFIG_PATH)
    return _chain


@app.on_event("startup")
def _warm_chain_in_background():
    """Pre-load RAG chain in a background thread so the first /chat request
    doesn't pay the model-loading cost. Server is still ready to serve /health
    and the static UI immediately."""
    threading.Thread(target=get_chain, daemon=True).start()


def _load_cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        raw = f.read()
    # Expand env vars
    for k, v in os.environ.items():
        raw = raw.replace(f"${{{k}}}", v)
    return yaml.safe_load(raw)


# ─── Schemas ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    top_k: Optional[int] = None


class RetrieveRequest(BaseModel):
    query: str


class SourceDoc(BaseModel):
    chunk_id:     Optional[str]  = None
    source:       Optional[str]  = None
    page:         Optional[int]  = None
    text:         str            = ""
    score_rrf:    Optional[float] = None
    score_rerank: Optional[float] = None
    score_dense:  Optional[float] = None
    score_sparse: Optional[float] = None
    doc_group:    Optional[Union[str, int]]  = None
    doc_type:     Optional[str]  = None
    doc_number:   Optional[str]  = None
    issuing_body: Optional[str]  = None
    section_type: Optional[str]  = None
    chapter:      Optional[str]  = None
    chapter_title: Optional[str] = None
    article:      Optional[str]  = None
    article_title: Optional[str] = None
    # Present only for documents uploaded through ingestion (null otherwise).
    khoan:        Optional[str]  = None
    level_labels: Optional[dict] = None
    file_type:    Optional[str]  = None
    language:     Optional[str]  = None


class ChatResponse(BaseModel):
    question: str
    answer:   str
    sources:  list[SourceDoc]
    elapsed:  float = 0.0


class RetrieveResponse(BaseModel):
    query:   str
    results: list[SourceDoc]
    elapsed: float = 0.0


class SearchResponse(BaseModel):
    query:   str
    mode:    str
    top_k:   int
    results: list[SourceDoc]
    elapsed: float = 0.0


def _doc_to_source(d: dict, text_limit: int = 800) -> SourceDoc:
    return SourceDoc(
        chunk_id      = d.get("chunk_id"),
        source        = d.get("source"),
        page          = d.get("page"),
        text          = d.get("text", "")[:text_limit],
        # retrieve() and search keep the internal _score_* keys; the sources of
        # chain.query() carry them as score_rrf / score_rerank.
        score_rrf     = d.get("_score_rrf", d.get("score_rrf")),
        score_rerank  = d.get("_score_rerank", d.get("score_rerank")),
        score_dense   = d.get("_score_dense"),
        score_sparse  = d.get("_score_sparse"),
        doc_group     = d.get("doc_group"),
        doc_type      = d.get("doc_type"),
        doc_number    = d.get("doc_number"),
        issuing_body  = d.get("issuing_body"),
        section_type  = d.get("section_type"),
        chapter       = d.get("chapter"),
        chapter_title = d.get("chapter_title"),
        article       = d.get("article"),
        article_title = d.get("article_title"),
        khoan         = d.get("khoan"),
        level_labels  = d.get("level_labels"),
        file_type     = d.get("file_type"),
        language      = d.get("language"),
    )


# ─── Stats helpers ────────────────────────────────────────────────────────────

def _iter_chunks(cfg: dict | None = None) -> list[dict]:
    """Read all chunks from data/processed/*.jsonl."""
    if cfg is None:
        cfg = _load_cfg()
    proc_dir = Path(cfg["data"]["processed_dir"])
    chunks: list[dict] = []
    for jl in sorted(proc_dir.glob("*.jsonl")):
        with jl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        chunks.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return chunks


# ─── Health ───────────────────────────────────────────────────────────────────

# Every open UI tab polls /health every 30 s; one probe answers them all.
_QDRANT_PROBE_TTL_S = 5.0
_qdrant_probe: dict = {"at": float("-inf"), "ok": False}


def _qdrant_ok() -> bool:
    """True when the configured Qdrant answers and holds the collection.

    Resolves URL and key the way VectorRetriever does, and never touches the
    RAG chain, so it also answers while the models are still loading.
    """
    now = time.monotonic()
    if now - _qdrant_probe["at"] < _QDRANT_PROBE_TTL_S:
        return _qdrant_probe["ok"]
    ok, client = False, None
    try:
        from src.config import load_config

        vs = load_config(CONFIG_PATH)["vector_store"]
        api_key = vs.get("qdrant_api_key") or os.environ.get("QDRANT_API_KEY", "")
        client = QdrantClient(url=vs["qdrant_url"], api_key=api_key or None,
                              timeout=2, check_compatibility=False)
        ok = bool(client.collection_exists(vs["collection_name"]))
    except Exception:
        ok = False
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    _qdrant_probe.update(at=now, ok=ok)
    return ok


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ready": _chain is not None,
        "qdrant": _qdrant_ok(),
        "timestamp": time.time(),
    }


# ─── Chat ─────────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    t0 = time.time()
    try:
        chain = get_chain()
        result = chain.query(req.question)
        return ChatResponse(
            question=result["question"],
            answer=result["answer"],
            sources=[_doc_to_source(s) for s in result["sources"]],
            elapsed=round(time.time() - t0, 3),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Retrieve ────────────────────────────────────────────────────────────────

@app.post("/retrieve", response_model=RetrieveResponse)
def retrieve(req: RetrieveRequest):
    t0 = time.time()
    try:
        chain = get_chain()
        docs = chain.retrieve(req.query)
        return RetrieveResponse(
            query=req.query,
            results=[_doc_to_source(d, 500) for d in docs],
            elapsed=round(time.time() - t0, 3),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Search (multi-mode) ─────────────────────────────────────────────────────

@app.get("/search", response_model=SearchResponse)
def search(
    query: str = Query(..., description="Search query text"),
    mode:  str = Query("hybrid_rerank", description="hybrid_rerank|hybrid|dense|sparse"),
    top_k: int = Query(10, ge=1, le=50, description="Number of results"),
):
    t0 = time.time()
    try:
        cfg   = _load_cfg()
        chain = get_chain()

        if mode == "hybrid_rerank":
            # Full pipeline: hybrid fusion + reranking
            candidates = chain.retriever.search(query)
            docs = chain.reranker.rerank(query, candidates)
            docs = docs[:top_k]

        elif mode == "hybrid":
            # Hybrid RRF fusion, no reranking
            docs = chain.retriever.search(query)
            docs = docs[:top_k]

        elif mode == "dense":
            # Dense vector search only
            from src.retrieval.vector_store import VectorRetriever
            vr = VectorRetriever(cfg)
            docs = vr.search(query, top_k=top_k)

        elif mode == "sparse":
            # BM25 sparse search only
            from src.retrieval.bm25_retriever import BM25Retriever
            br = BM25Retriever(cfg)
            docs = br.search(query, top_k=top_k)

        else:
            raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

        return SearchResponse(
            query=query,
            mode=mode,
            top_k=top_k,
            results=[_doc_to_source(d) for d in docs],
            elapsed=round(time.time() - t0, 3),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Stats ───────────────────────────────────────────────────────────────────

@app.get("/stats")
def stats():
    try:
        cfg    = _load_cfg()
        chunks = _iter_chunks(cfg)

        total_chunks = len(chunks)
        sources_set  = set()
        group_chunks: dict[str, int]   = {}
        group_files:  dict[str, set]   = {}

        for c in chunks:
            src = c.get("source", "unknown")
            grp = c.get("doc_group") or "unknown"
            sources_set.add(src)
            group_chunks[grp] = group_chunks.get(grp, 0) + 1
            if grp not in group_files:
                group_files[grp] = set()
            group_files[grp].add(src)

        return {
            "total_documents": len(sources_set),
            "total_chunks":    total_chunks,
            "groups": {
                grp: {
                    "chunks": group_chunks[grp],
                    "files":  len(group_files[grp]),
                }
                for grp in sorted(group_chunks)
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Sources ─────────────────────────────────────────────────────────────────

@app.get("/sources")
def sources(group: Optional[str] = None):
    try:
        cfg    = _load_cfg()
        chunks = _iter_chunks(cfg)

        # Aggregate per source file
        agg: dict[str, dict] = {}
        for c in chunks:
            src = c.get("source", "unknown")
            if src not in agg:
                agg[src] = {
                    "source":      src,
                    "doc_group":   c.get("doc_group") or "unknown",
                    "doc_type":    c.get("doc_type") or "",
                    "doc_number":  c.get("doc_number") or "",
                    "issuing_body": c.get("issuing_body") or "",
                    "chunk_count": 0,
                    "max_page":    0,
                }
            agg[src]["chunk_count"] += 1
            pg = c.get("page") or 0
            if pg > agg[src]["max_page"]:
                agg[src]["max_page"] = pg

        docs_list = list(agg.values())
        if group:
            docs_list = [d for d in docs_list if d["doc_group"] == group]

        docs_list.sort(key=lambda d: (d["doc_group"], d["source"]))
        return {"documents": docs_list, "total": len(docs_list)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── PDF serving ─────────────────────────────────────────────────────────────

@app.get("/pdf/{filename:path}")
def serve_pdf(filename: str):
    cfg     = _load_cfg()
    raw_dir = Path(cfg["data"]["raw_dir"])
    # Search recursively for the PDF — in document groups only, never in the
    # ingestion staging area or backups, which can hold same-named copies.
    for pdf_path in raw_dir.rglob("*.pdf"):
        if any(ing_reg.is_internal_dir(part, cfg) for part in pdf_path.relative_to(raw_dir).parts[:-1]):
            continue
        if pdf_path.name == filename or str(pdf_path) == filename:
            # Inline disposition → browser renders PDF instead of downloading.
            # Don't pass `filename=` (FastAPI sets attachment disposition then).
            return FileResponse(
                str(pdf_path),
                media_type="application/pdf",
                filename=pdf_path.name,
                content_disposition_type="inline",
                headers={"Cache-Control": "public, max-age=3600"},
            )
    raise HTTPException(status_code=404, detail=f"PDF not found: {filename}")


# ─── Config ──────────────────────────────────────────────────────────────────

@app.get("/config")
def get_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return {"config": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ConfigUpdateRequest(BaseModel):
    config: str  # raw YAML text


@app.put("/config")
def update_config(req: ConfigUpdateRequest):
    try:
        # Validate it's valid YAML before saving
        yaml.safe_load(req.config)
        # UTF-8, via a temp file: open(path, "w") alone uses the Windows code
        # page, fails on the Vietnamese in config.yaml, and has already
        # truncated the file by then — leaving an empty config behind.
        tmp_path = Path(CONFIG_PATH).with_name(Path(CONFIG_PATH).name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(req.config)
        os.replace(tmp_path, CONFIG_PATH)
        # Reset chain so it reloads config on next request
        global _chain
        _chain = None
        return {"status": "ok", "message": "Config updated. The retrieval pipeline will reload on next request."}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Indexing jobs ────────────────────────────────────────────────────────────
#
# Every change to the index — a new upload, a replaced version, a deletion, a
# full re-sync — is a job in one persisted queue, run by one background worker
# in a subprocess (src/ingest/jobs.py, src/ingest/worker.py). Jobs update the
# live Qdrant collection in place and swap index files atomically; when a job
# ends the chain reloads BM25 and the clause index from disk (≈0.15 s), so Chat
# stays available throughout and the models are never reloaded.

_ingest_lock = threading.Lock()     # check-then-move of documents must not interleave
_reload_lock = threading.Lock()
_job_runner: Optional[JobRunner] = None
_job_runner_lock = threading.Lock()


def _ingest_paths(cfg: dict | None = None) -> ing_reg.IngestPaths:
    paths = ing_reg.IngestPaths.from_cfg(cfg or _load_cfg())
    paths.ensure()
    return paths


def _get_runner() -> JobRunner:
    global _job_runner
    if _job_runner is None:
        with _job_runner_lock:
            if _job_runner is None:
                cfg = _load_cfg()
                paths = _ingest_paths(cfg)
                timeout = float((cfg.get("ingest") or {}).get("job_timeout_s", 3600))
                _job_runner = JobRunner(
                    JobStore(paths.jobs_dir), CONFIG_PATH,
                    on_finished=_on_job_finished, timeout_s=timeout, raw_dir=paths.raw_dir,
                )
    return _job_runner


def _hot_reload() -> None:
    """Swap in the index files a job rewrote. A chain that is not loaded yet
    will read them when it is."""
    chain = _chain
    if chain is None:
        return
    with _reload_lock:
        try:
            chain.reload_indexes()
        except Exception as exc:
            print(f"[ingest] hot reload failed: {exc}")


def _on_job_finished(job: dict) -> None:
    _hot_reload()
    try:
        keep = int((_load_cfg().get("ingest") or {}).get("keep_jobs", 200))
        _get_runner().store.prune(keep)
    except Exception:
        pass


def _cleanup_staging(paths: ing_reg.IngestPaths, ttl_hours: float) -> None:
    cutoff = time.time() - ttl_hours * 3600
    for d in paths.staging_dir.iterdir() if paths.staging_dir.is_dir() else []:
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


@app.on_event("startup")
def _start_job_runner():
    """Resume the queue: jobs a previous process left running are queued again
    (every job step is idempotent), and stale upload staging is cleared."""
    try:
        cfg = _load_cfg()
        runner = _get_runner()
        runner.store.recover()
        _cleanup_staging(_ingest_paths(cfg), float((cfg.get("ingest") or {}).get("staging_ttl_hours", 24)))
        runner.start()
    except Exception as exc:
        print(f"[ingest] job runner not started: {exc}")


def _err(status: int, code: str, message: Optional[str] = None, **kw):
    raise HTTPException(status_code=status, detail={"code": code, "message": message or ing_val.message(code, **kw)})


class ReindexRequest(BaseModel):
    mode: Optional[str] = "sync"


@app.post("/reindex")
def reindex(body: Optional[ReindexRequest] = None):
    """Re-parse every document and re-sync the indexes.

    mode=sync (default) keeps the collection live: new points are upserted and
    points of vanished chunks deleted, so Chat keeps answering. mode=rebuild
    drops and recreates the collection — only needed after changing the
    embedding model or vector parameters; Chat is degraded until it finishes.
    """
    mode = (body.mode if body else None) or "sync"
    if mode not in ("sync", "rebuild"):
        _err(400, "BAD_MODE", "mode phải là 'sync' hoặc 'rebuild'.")
    runner = _get_runner()
    if any(j.get("type") == "reindex" for j in runner.store.active()):
        raise HTTPException(status_code=409, detail="A reindex is already running.")
    job = runner.enqueue("reindex", {"mode": mode})
    return {"status": "started", "started_at": job["created_ts"], "job_id": job["id"], "mode": mode}


@app.get("/reindex/status")
def reindex_status():
    """Same shape as before (running/stage/started_at/finished_at/error/log),
    read from the most recent reindex job."""
    job = _get_runner().store.latest("reindex")
    if not job:
        return {"running": False, "stage": "idle", "started_at": None,
                "finished_at": None, "error": None, "log": []}
    running = job.get("status") in JOB_ACTIVE
    if running:
        stage = (job.get("progress") or {}).get("step") or job.get("status")
    else:
        stage = "error" if job.get("status") == "failed" else "done"
    error = (job.get("error") or {}).get("message") if job.get("status") == "failed" else None
    return {
        "running": running,
        "stage": stage,
        "started_at": job.get("started_ts") or job.get("created_ts"),
        "finished_at": job.get("finished_ts"),
        "error": error,
        "log": job.get("log_tail") or [],
        "job_id": job.get("id"),
        "status": job.get("status"),
        "progress": job.get("progress") or {},
    }


@app.get("/jobs")
def list_jobs(active: bool = Query(False, description="only queued/running jobs")):
    store = _get_runner().store
    jobs = store.active() if active else store.list()[-50:]
    return {"jobs": jobs}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = _get_runner().store.get(job_id)
    if not job:
        _err(404, "JOB_NOT_FOUND", "Không tìm thấy job.")
    return job


# ─── Group registry + document management ─────────────────────────────────────
#
# A "group" is a folder under data/. The user picks (or creates) a group when
# uploading; the parser derives doc metadata from the folder via the registry
# (data/groups.json). The endpoints below let the UI list/create/delete groups
# and documents. Every change to the corpus enqueues an indexing job, so the
# index follows the files without a manual reindex.

ALLOWED_EXTENSIONS = set(ing_reg.SUPPORTED_EXTS)


def _groups_path() -> Path:
    return Path(_load_cfg()["data"]["raw_dir"]) / "groups.json"


def _processed_dir_names() -> set[str]:
    cfg = _load_cfg()
    return {Path(cfg["data"]["processed_dir"]).name, "processed", "processed_v2"}


def _load_groups() -> dict:
    p = _groups_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _save_groups(reg: dict) -> None:
    ing_reg.atomic_write_json(_groups_path(), reg)


def _slugify(name: str) -> str:
    """Folder-safe ASCII slug from a (possibly Vietnamese) group name."""
    s = (name or "").strip().replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s or "group"


def _safe_group_name(group: str) -> str:
    """Reject traversal / processed / internal dirs; return a single path component."""
    g = Path(group).name
    if not g or g in _processed_dir_names() or ing_reg.is_internal_dir(g):
        raise HTTPException(status_code=400, detail=f"invalid group: {group!r}")
    return g


@app.get("/groups")
def list_groups():
    """List all groups = registry entries ∪ data/ subfolders (with file counts)."""
    cfg = _load_cfg()
    raw = Path(cfg["data"]["raw_dir"])
    reg = _load_groups()
    skip = _processed_dir_names()
    # Backups and ingestion state live in folders under data/ too; they are not groups.
    folders = ({p.name for p in raw.iterdir()
                if p.is_dir() and p.name not in skip and not ing_reg.is_internal_dir(p.name, cfg)}
               if raw.is_dir() else set())
    counts = ing_reg.group_file_counts(cfg)
    out = []
    for key in sorted(set(reg) | folders):
        e = reg.get(key, {})
        out.append({
            "id": key,
            "label": e.get("label", key),
            "doc_type": e.get("doc_type", ""),
            "issuing_body": e.get("issuing_body", ""),
            "language": e.get("language", ""),
            "file_count": counts.get(key, 0),
        })
    return {"groups": out}


class GroupCreate(BaseModel):
    label: str
    doc_type: Optional[str] = ""
    issuing_body: Optional[str] = ""
    language: Optional[str] = ""


def _group_entry(label: str, doc_type: str = "", issuing_body: str = "", language: str = "") -> dict:
    entry = {"label": label, "doc_type": doc_type or "", "issuing_body": issuing_body or ""}
    if language in ("vi", "en"):
        entry["language"] = language
    return entry


@app.post("/groups")
def create_group(body: GroupCreate):
    label = (body.label or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    slug = _slugify(label)
    cfg = _load_cfg()
    folder = Path(cfg["data"]["raw_dir"]) / slug
    reg = _load_groups()
    if slug in reg or folder.exists():
        raise HTTPException(status_code=409, detail=f"group '{slug}' already exists")
    folder.mkdir(parents=True, exist_ok=True)
    reg[slug] = _group_entry(label, body.doc_type, body.issuing_body, body.language)
    _save_groups(reg)
    return {"id": slug, "label": label, "doc_type": body.doc_type or "", "issuing_body": body.issuing_body or "",
            "language": reg[slug].get("language", ""), "file_count": 0}


def _remove_document_files(doc: ing_reg.DocFile) -> None:
    """The file, its sidecar, and any backup left by an unfinished replacement."""
    side = ing_reg.sidecar_path(doc.path)
    for p in (doc.path, side, doc.path.with_name(doc.path.name + ".prev"),
              side.with_name(side.name + ".prev")):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


@app.delete("/groups/{group}")
def delete_group(group: str):
    """Delete a group folder (and all its documents) + its registry entry, and
    remove those documents from every index."""
    g = _safe_group_name(group)
    cfg = _load_cfg()
    folder = Path(cfg["data"]["raw_dir"]) / g
    with _ingest_lock:
        docs = [d for d in ing_reg.list_raw_documents(cfg) if d.group == g]
        sources = {d.source for d in docs}
        # Index data of this group's documents whose files are already gone.
        on_disk = {d.source for d in ing_reg.list_raw_documents(cfg)}
        for src, st in ing_reg.jsonl_stats(Path(cfg["data"]["processed_dir"])).items():
            if st.get("doc_group") == g and src not in on_disk:
                sources.add(src)
        if folder.is_dir():
            shutil.rmtree(folder)
        reg = _load_groups()
        if g in reg:
            del reg[g]
            _save_groups(reg)
        job = None
        if sources:
            job = _get_runner().enqueue("remove", {"items": [{"source": s, "group": g} for s in sorted(sources)]})
    return {"deleted_group": g, "removed_files": len(docs), "reindex_required": False,
            "job_id": job["id"] if job else None}


@app.delete("/documents/{filename:path}")
def delete_document(filename: str):
    """Delete a document (+ sidecar) and remove it from every index.

    Also accepts a source that exists only in the index (its file already
    gone), so leftovers of an earlier delete can be cleaned up."""
    cfg = _load_cfg()
    name = Path(filename).name
    with _ingest_lock:
        doc = ing_reg.find_document(cfg, name)
        stats = ing_reg.jsonl_stats(Path(cfg["data"]["processed_dir"]))
        if doc is None and name not in stats:
            raise HTTPException(status_code=404, detail=f"document not found: {name}")
        if doc is not None:
            name, group = doc.source, doc.group
            _remove_document_files(doc)
        else:
            group = stats[name].get("doc_group") or ""
        job = _get_runner().enqueue("remove", {"items": [{"source": name, "group": group}]})
    return {"deleted": name, "group": group, "reindex_required": False, "job_id": job["id"]}


# ─── Upload: stage → validate → commit → index ────────────────────────────────
#
# 1. POST /uploads streams every file into a private staging folder, reads its
#    real type from the bytes, and reports per file: accepted or why not,
#    pages, likely scan, suggested language, name/content conflicts.
# 2. POST /uploads/{id}/commit moves the files the user confirmed into the
#    group folder, writes their sidecars and queues one ingest job.
# Nothing reaches data/<group>/ before the user has seen that report.

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _limits(cfg: dict) -> ing_val.Limits:
    return ing_val.Limits.from_cfg(cfg)


def _resolve_group_for_check(group: str, cfg: dict) -> str:
    """Group id the uploaded files are meant for (only used to classify name
    conflicts in the report; commit resolves it for real)."""
    raw_group = (group or "").strip()
    if not raw_group:
        return ""
    raw = Path(cfg["data"]["raw_dir"])
    if raw_group in _load_groups() or (raw / raw_group).is_dir():
        return Path(raw_group).name
    return ing_naming.slugify_group(raw_group)


def _public_report(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("staged_name",)}


@app.post("/uploads", status_code=201)
async def create_upload(request: Request, files: list[UploadFile] = File(...), group: str = Form("")):
    cfg = _load_cfg()
    paths = _ingest_paths(cfg)
    limits = _limits(cfg)
    ing = cfg.get("ingest") or {}
    _cleanup_staging(paths, float(ing.get("staging_ttl_hours", 24)))

    if len(files) > limits.max_files_per_upload:
        _err(400, "TOO_MANY_FILES", max_files=limits.max_files_per_upload)
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > limits.max_batch_bytes + 1024 * 1024:
        _err(413, "BATCH_TOO_LARGE", max_batch_mb=int(limits.max_batch_mb))
    if not ing_val.free_disk_ok(paths.staging_dir, declared, limits):
        _err(507, "DISK_FULL")

    upload_id = uuid.uuid4().hex[:12]
    stage_dir = paths.staging_dir / upload_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    target_group = _resolve_group_for_check(group, cfg)
    taken = ing_reg.taken_stems(cfg)
    hashes = ing_reg.content_hashes(cfg, paths)
    labels = {k: (v or {}).get("label") or k for k, v in _load_groups().items()}

    def glabel(gid: Optional[str]) -> str:
        return labels.get(gid, gid) if gid else "—"
    detect_cfg = ing.get("language_detect") or {}
    ocr_ok: Optional[bool] = None
    batch_stems: dict[str, str] = {}
    batch_hashes: dict[str, str] = {}
    batch_bytes = 0
    reports: list[dict] = []

    for idx, f in enumerate(files, start=1):
        key = f"f{idx}"
        original = f.filename or ""
        safe = ing_naming.safe_filename(original) if original else ""
        ext = ing_naming.split_name(safe)[1] if safe else ""
        report = {
            "file_key": key, "original_name": original, "safe_name": safe,
            "file_type": ext.lstrip("."), "size": 0, "sha256": None,
            "status": "ok", "reason_code": None, "message": None,
            "pages": None, "scanned_pages_estimate": 0, "ocr_seconds_estimate": 0,
            "language_suggestion": "vi", "language_reason": None,
            "conflict": None, "warnings": [],
            "staged_name": f"{key}{ext or '.bin'}",
        }
        staged = stage_dir / report["staged_name"]

        def reject(code: str, **kw) -> None:
            report.update(status="rejected", reason_code=code, message=ing_val.message(code, **kw))

        # Stream to disk: never more than 1 MiB of an upload in memory.
        digest = hashlib.sha256()
        size = 0
        too_big = batch_full = False
        with open(staged, "wb") as out:
            while True:
                block = await f.read(1 << 20)
                if not block:
                    break
                size += len(block)
                batch_bytes += len(block)
                if size > limits.max_file_bytes:
                    too_big = True
                    break
                if batch_bytes > limits.max_batch_bytes:
                    batch_full = True
                    break
                digest.update(block)
                out.write(block)
        report["size"] = size

        if not original or not safe:
            reject("NAME_INVALID")
        elif too_big:
            reject("TOO_LARGE", max_file_mb=int(limits.max_file_mb))
        elif batch_full:
            reject("BATCH_TOO_LARGE", max_batch_mb=int(limits.max_batch_mb))
        else:
            try:
                insp = ing_val.inspect_file(staged, ext, limits)
            except ing_val.Rejected as exc:
                report.update(status="rejected", reason_code=exc.code, message=exc.message)
            except Exception as exc:
                reject("CORRUPT")
                report["message"] += f" ({type(exc).__name__})"
            else:
                lang, why = ing_val.suggest_language(insp.text_sample, detect_cfg)
                report.update(pages=insp.pages, language_suggestion=lang, language_reason=why,
                              scanned_pages_estimate=insp.scanned_pages_estimate,
                              ocr_seconds_estimate=round(insp.scanned_pages_estimate * ing_val.OCR_SECONDS_PER_PAGE))
                if why == "no_diacritics":
                    report["warnings"].append("broken_text_layer")
                if insp.scanned_pages_estimate:
                    if ocr_ok is None:
                        ocr_ok = ing_val.ocr_available()
                    report["warnings"].append("needs_ocr" if ocr_ok else "ocr_unavailable")

        if report["status"] == "ok":
            sha = digest.hexdigest()
            report["sha256"] = sha
            dup = hashes.get(sha)
            if dup is not None:
                reject("DUPLICATE_CONTENT", source=dup.source, group=glabel(dup.group))
            elif sha in batch_hashes:
                reject("DUPLICATE_CONTENT", source=batch_hashes[sha], group="lần tải này")
            else:
                batch_hashes[sha] = safe
                sk = ing_naming.stem_key(safe)
                if sk in batch_stems:
                    report["conflict"] = {"type": "in_batch", "with": batch_stems[sk],
                                          "message": ing_val.message("NAME_CONFLICT_IN_BATCH")}
                elif sk in taken:
                    existing = taken[sk]
                    if existing is not None and existing.group == target_group:
                        report["conflict"] = {"type": "same_group", "source": existing.source,
                                              "group": existing.group,
                                              "message": ing_val.message("NAME_CONFLICT_SAME_GROUP")}
                    else:
                        other = existing.group if existing is not None else ""
                        report["conflict"] = {"type": "other_group",
                                              "source": existing.source if existing else None,
                                              "group": other or "—",
                                              "message": ing_val.message("NAME_CONFLICT_OTHER_GROUP",
                                                                         group=glabel(other))}
                batch_stems.setdefault(sk, key)

        if report["status"] != "ok":
            try:
                staged.unlink()
            except OSError:
                pass
        reports.append(report)

    manifest = {"upload_id": upload_id, "created_at": now_iso(), "created_ts": time.time(),
                "group_hint": target_group, "files": reports}
    ing_reg.atomic_write_json(stage_dir / "manifest.json", manifest)
    ttl = float(ing.get("staging_ttl_hours", 24))
    return {"upload_id": upload_id, "expires_in_hours": ttl, "limits": {
                "max_file_mb": limits.max_file_mb, "max_files_per_upload": limits.max_files_per_upload,
                "max_batch_mb": limits.max_batch_mb},
            "files": [_public_report(r) for r in reports]}


class CommitNewGroup(BaseModel):
    label: str
    doc_type: Optional[str] = ""
    issuing_body: Optional[str] = ""
    language: Optional[str] = ""


class CommitGroup(BaseModel):
    id: Optional[str] = None
    new: Optional[CommitNewGroup] = None


class CommitFile(BaseModel):
    file_key: str
    action: str = "add"                 # add | replace | rename | skip
    language: Optional[str] = None      # vi | en (default: the suggestion)
    metadata: Optional[dict] = None     # title, doc_type, doc_number, issuing_body


class CommitRequest(BaseModel):
    group: CommitGroup
    files: list[CommitFile]
    index_now: bool = True


def _resolve_commit_group(spec: CommitGroup, cfg: dict) -> str:
    raw = Path(cfg["data"]["raw_dir"])
    reg = _load_groups()
    if spec.id:
        gid = _safe_group_name(spec.id)
        if gid not in reg and not (raw / gid).is_dir():
            _err(404, "GROUP_NOT_FOUND", f"Không tìm thấy nhóm «{gid}».")
        (raw / gid).mkdir(parents=True, exist_ok=True)
        return gid
    if spec.new and (spec.new.label or "").strip():
        label = spec.new.label.strip()
        gid = ing_naming.slugify_group(label)
        if ing_reg.is_internal_dir(gid, cfg):
            gid = f"nhom-{gid}"
        # A name that slugs to an existing group means that group (as before).
        if gid not in reg:
            reg[gid] = _group_entry(label, (spec.new.doc_type or "").strip(),
                                    (spec.new.issuing_body or "").strip(), spec.new.language or "")
            _save_groups(reg)
        (raw / gid).mkdir(parents=True, exist_ok=True)
        return gid
    _err(400, "GROUP_REQUIRED", "Vui lòng chọn hoặc tạo nhóm cho tài liệu.")


_METADATA_KEYS = ("title", "doc_type", "doc_number", "issuing_body")


def _clean_metadata(meta: Optional[dict]) -> dict:
    meta = meta or {}
    return {k: str(meta.get(k) or "").strip()[:300] for k in _METADATA_KEYS}


def _commit(upload_id: str, body: CommitRequest) -> dict:
    if not _ID_RE.match(upload_id or ""):
        _err(404, "UPLOAD_NOT_FOUND", "Phiên tải lên không tồn tại hoặc đã hết hạn.")
    cfg = _load_cfg()
    paths = _ingest_paths(cfg)
    stage_dir = paths.staging_dir / upload_id
    manifest = ing_reg.read_json(stage_dir / "manifest.json")
    if not isinstance(manifest, dict):
        _err(404, "UPLOAD_NOT_FOUND", "Phiên tải lên không tồn tại hoặc đã hết hạn.")
    by_key = {f["file_key"]: f for f in manifest.get("files", [])}
    default_lang = ((cfg.get("ingest") or {}).get("default_language") or "vi")

    with _ingest_lock:
        group_id = _resolve_commit_group(body.group, cfg)
        registry_groups = _load_groups()
        group_lang = (registry_groups.get(group_id) or {}).get("language")

        def glabel(gid: Optional[str]) -> str:
            return ((registry_groups.get(gid) or {}).get("label") or gid) if gid else "—"

        taken = ing_reg.taken_stems(cfg)
        hashes = ing_reg.content_hashes(cfg, paths)
        results: list[dict] = []
        items: list[dict] = []
        for req in body.files:
            f = by_key.get(req.file_key)
            if f is None:
                results.append({"file_key": req.file_key, "status": "error", "code": "UNKNOWN_FILE",
                                "message": "File không có trong phiên tải lên."})
                continue
            base = {"file_key": req.file_key, "original_name": f.get("original_name")}
            if f.get("status") != "ok":
                results.append({**base, "status": "rejected", "code": f.get("reason_code"), "message": f.get("message")})
                continue
            if req.action == "skip":
                results.append({**base, "status": "skipped"})
                continue
            if req.action not in ("add", "replace", "rename"):
                results.append({**base, "status": "error", "code": "BAD_ACTION", "message": "Hành động không hợp lệ."})
                continue
            staged = stage_dir / f["staged_name"]
            if not staged.exists():
                results.append({**base, "status": "error", "code": "UPLOAD_NOT_FOUND",
                                "message": "File tạm không còn (đã commit hoặc hết hạn)."})
                continue

            name = f["safe_name"]
            key = ing_naming.stem_key(name)
            existing = taken.get(key)
            replaces: Optional[ing_reg.DocFile] = None
            if key in taken:
                if req.action == "rename":
                    name = ing_naming.unique_name(name, set(taken))
                elif req.action == "replace" and existing is not None and existing.group == group_id:
                    replaces = existing
                else:
                    code = ("NAME_CONFLICT_SAME_GROUP" if existing is not None and existing.group == group_id
                            else "NAME_CONFLICT_OTHER_GROUP")
                    results.append({**base, "status": "error", "code": code,
                                    "message": ing_val.message(code, group=glabel(existing.group if existing else ""))})
                    continue
            dup = hashes.get(f.get("sha256"))
            if dup is not None and (replaces is None or dup.source != replaces.source):
                results.append({**base, "status": "error", "code": "DUPLICATE_CONTENT",
                                "message": ing_val.message("DUPLICATE_CONTENT", source=dup.source,
                                                           group=glabel(dup.group))})
                continue

            dest = paths.raw_dir / group_id / name
            try:
                if replaces is not None:
                    # Keep the old version beside the new one until the new one
                    # is indexed; the worker restores it if indexing fails.
                    old_side = ing_reg.sidecar_path(replaces.path)
                    prev = replaces.path.with_name(replaces.path.name + ".prev")
                    ing_reg.replace_with_retry(replaces.path, prev)
                    if old_side.exists():
                        ing_reg.replace_with_retry(old_side, old_side.with_name(old_side.name + ".prev"))
                ing_reg.replace_with_retry(staged, dest)
            except OSError as exc:
                results.append({**base, "status": "error", "code": "COMMIT_FAILED", "message": str(exc)})
                continue

            language = req.language if req.language in ("vi", "en") else None
            language = language or f.get("language_suggestion") or group_lang or default_lang
            ing_reg.save_sidecar(dest, {
                "schema": 1,
                "source": name,
                "original_filename": f.get("original_name"),
                "group": group_id,
                "file_type": ing_naming.split_name(name)[1].lstrip("."),
                "size_bytes": f.get("size"),
                "sha256": f.get("sha256"),
                "language": language,
                "metadata": _clean_metadata(req.metadata),
                "uploaded_at": now_iso(),
                "status": "queued" if body.index_now else "not_indexed",
                "status_detail": {"step": "queued" if body.index_now else None, "error_code": None, "message": None},
                "report": None,
                "indexed_at": None,
                "replaced": replaces.source if replaces is not None else None,
            })
            taken[ing_naming.stem_key(name)] = ing_reg.DocFile(dest, group_id, name)
            if f.get("sha256"):
                hashes[f["sha256"]] = ing_reg.DocFile(dest, group_id, name)
            item = {"source": name, "group": group_id}
            if replaces is not None:
                item["replaces"] = replaces.source
            items.append(item)
            results.append({**base, "status": "queued" if body.index_now else "not_indexed",
                            "source": name, "renamed": name != f["safe_name"],
                            "replaces": item.get("replaces"), "language": language})

        job = _get_runner().enqueue("ingest", {"items": items}) if items and body.index_now else None
    shutil.rmtree(stage_dir, ignore_errors=True)
    return {"job_id": job["id"] if job else None, "group": group_id, "documents": results}


@app.post("/uploads/{upload_id}/commit", status_code=202)
def commit_upload(upload_id: str, body: CommitRequest):
    return _commit(upload_id, body)


@app.delete("/uploads/{upload_id}", status_code=204)
def cancel_upload(upload_id: str):
    if _ID_RE.match(upload_id or ""):
        shutil.rmtree(_ingest_paths().staging_dir / upload_id, ignore_errors=True)
    return None


@app.post("/upload-docs")
async def upload_docs(
    request: Request,
    files: list[UploadFile] = File(...),
    group: str = Form(...),
    on_conflict: str = Form("skip"),
):
    """Deprecated one-shot upload, kept for scripts: stage + commit in one call.

    Unlike the original endpoint it no longer overwrites a same-named document
    silently — `on_conflict` is skip (default), rename or replace — and the
    accepted files are indexed right away (response carries `job_id`).
    """
    raw_group = (group or "").strip()
    if not raw_group:
        raise HTTPException(status_code=400, detail="group is required")
    cfg = _load_cfg()
    raw = Path(cfg["data"]["raw_dir"])
    staged = await create_upload(request, files=files, group=raw_group)
    if raw_group in _load_groups() or (raw / raw_group).is_dir():
        spec = CommitGroup(id=raw_group)
    else:
        spec = CommitGroup(new=CommitNewGroup(label=raw_group))
    policy = on_conflict if on_conflict in ("skip", "rename", "replace") else "skip"
    commit_files = []
    for r in staged["files"]:
        if r["status"] != "ok":
            continue
        action = "add" if not r.get("conflict") else policy
        if action == "replace" and (r.get("conflict") or {}).get("type") != "same_group":
            action = "rename" if policy != "skip" else "skip"
        commit_files.append(CommitFile(file_key=r["file_key"], action=action))
    result = _commit(staged["upload_id"], CommitRequest(group=spec, files=commit_files, index_now=True))
    group_id = result["group"]
    saved, skipped = [], []
    by_key = {r["file_key"]: r for r in staged["files"]}
    for r in staged["files"]:
        if r["status"] != "ok":
            skipped.append({"filename": r["original_name"] or "<empty>", "reason": r["message"]})
    for d in result["documents"]:
        orig = by_key.get(d["file_key"], {})
        if d["status"] in ("queued", "not_indexed"):
            saved.append({"filename": d["source"], "size": orig.get("size"), "group": group_id,
                          "path": str(raw / group_id / d["source"])})
        elif d["status"] != "rejected":
            skipped.append({"filename": d.get("original_name") or d["file_key"],
                            "reason": d.get("message") or d["status"]})
    return {
        "saved":   saved,
        "skipped": skipped,
        "saved_count":   len(saved),
        "skipped_count": len(skipped),
        "group":         group_id,
        "upload_dir":    str(raw / group_id),
        "job_id":        result["job_id"],
    }


# ─── Library catalogue (data/catalog.json) ──────────────────────────────────
# Display metadata (title, number, year) for documents whose files carry none.
# Edited by hand and only read here; design: docs/design/documents-library-redesign.md.

_CATALOG_TEXT_LIMITS = {"title": 300, "title_en": 300, "doc_number": 80, "issuing_body": 120}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_catalog_cache: dict = {"key": None, "data": {}}


def _catalog_path() -> Path:
    return Path(_load_cfg()["data"]["raw_dir"]) / "catalog.json"


def _load_catalog() -> dict:
    """{source: entry} from data/catalog.json; {} when the file is missing or unreadable."""
    p = _catalog_path()
    try:
        st = p.stat()
    except OSError:
        return {}
    key = (str(p), st.st_mtime_ns, st.st_size)
    if _catalog_cache["key"] != key:
        data: dict = {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            docs = raw.get("documents") if isinstance(raw, dict) else None
            if not isinstance(docs, dict):
                raise ValueError("no 'documents' object")
            data = {k: v for k, v in docs.items() if isinstance(v, dict)}
        except (OSError, ValueError) as exc:
            print(f"[catalog] {p} ignored: {exc}")
        _catalog_cache.update(key=key, data=data)
    return _catalog_cache["data"]


def _catalog_entry(entry: Optional[dict], size: int) -> Optional[dict]:
    """The display fields of one catalogue entry, or None — also when the entry
    describes another file of the same name (its recorded size differs)."""
    if not entry:
        return None
    if type(entry.get("size")) is int and entry["size"] != size:
        return None
    out: dict = {}
    for k, limit in _CATALOG_TEXT_LIMITS.items():
        v = entry.get(k)
        if isinstance(v, str) and len(v.strip()) <= limit:
            out[k] = v.strip()
    year = entry.get("year")
    if type(year) is int and 1900 <= year <= 2100:
        out["year"] = year
    issued = entry.get("issued")
    if isinstance(issued, str) and _ISO_DATE.match(issued.strip()):
        out["issued"] = issued.strip()
    return out or None


# ─── Document registry (every document, with its indexing status) ───────────

@app.get("/documents")
def list_documents():
    """All documents — including uploaded-but-not-yet-searchable ones, failed
    ones and index leftovers — with one reconciled status each."""
    cfg = _load_cfg()
    rows = ing_reg.build_registry(cfg, _get_runner().store.active())
    reg = _load_groups()
    for r in rows:
        r["group_label"] = (reg.get(r["group"]) or {}).get("label") or r["group"]
    catalog = _load_catalog()
    for r in rows:
        r["catalog"] = _catalog_entry(catalog.get(r["source"]), r.get("size", 0))
    summary = {"total": 0, "indexed": 0, "processing": 0, "failed": 0, "not_indexed": 0, "orphan": 0}
    for r in rows:
        s = r["status"]
        if s == "orphan":
            summary["orphan"] += 1
            continue
        summary["total"] += 1
        if s == "indexed":
            summary["indexed"] += 1
        elif s in ("queued", "processing", "removing"):
            summary["processing"] += 1
        elif s in ("failed", "empty"):
            summary["failed"] += 1
        else:
            summary["not_indexed"] += 1
    return {"documents": rows, "summary": summary}


_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@app.get("/documents/{source}/file")
def document_file(source: str):
    """The original file: PDFs open in the browser, DOCX downloads."""
    doc = ing_reg.find_document(_load_cfg(), Path(source).name)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"document not found: {source}")
    inline = doc.ext == ".pdf"
    return FileResponse(
        str(doc.path),
        media_type=_MEDIA_TYPES.get(doc.ext, "application/octet-stream"),
        filename=doc.source,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@app.post("/documents/{source}/retry", status_code=202)
def retry_document(source: str):
    """Queue a document for (re)indexing: failed, empty, or never indexed."""
    cfg = _load_cfg()
    runner = _get_runner()
    with _ingest_lock:
        doc = ing_reg.find_document(cfg, Path(source).name)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"document not found: {source}")
        for job in runner.store.active():
            if any(it.get("source") == doc.source for it in (job.get("params") or {}).get("items", [])):
                _err(409, "ALREADY_QUEUED", "Tài liệu đang chờ hoặc đang được xử lý.")
        side = ing_reg.load_sidecar(doc.path)
        if side is not None:
            side["status"] = "queued"
            side["status_detail"] = {"step": "queued", "error_code": None, "message": None}
            ing_reg.save_sidecar(doc.path, side)
        job = runner.enqueue("ingest", {"items": [{"source": doc.source, "group": doc.group}]})
    return {"job_id": job["id"], "source": doc.source}


@app.get("/index/health")
def index_health():
    """Chunk counts per source in the three stores (JSONL, Qdrant, BM25).

    Any difference means the stores disagree — e.g. a job interrupted by a
    crash — and a sync (POST /reindex) will reconcile them."""
    from src.config import load_config

    cfg = _load_cfg()
    stats = ing_reg.jsonl_stats(Path(cfg["data"]["processed_dir"]))
    jsonl = {src: st["chunk_count"] for src, st in stats.items()}
    errors: list[str] = []
    bm25: dict[str, int] = {}
    try:
        with open(cfg["bm25"]["index_path"], "rb") as f:
            payload = pickle.load(f)
        for rec in payload.get("records", []):
            src = rec.get("source") or "unknown"
            bm25[src] = bm25.get(src, 0) + 1
    except Exception as exc:
        errors.append(f"bm25: {exc}")
    qdrant: dict[str, int] = {}
    try:
        full_cfg = load_config(CONFIG_PATH)
        emb = importlib.import_module("src.pipeline.02_embed_index")
        client = emb.get_qdrant_client(full_cfg)
        qdrant = emb.count_by_source(client, full_cfg["vector_store"]["collection_name"])
    except Exception as exc:
        errors.append(f"qdrant: {type(exc).__name__}: {exc}")
    drift = []
    for src in sorted(set(jsonl) | set(bm25) | set(qdrant)):
        row = {"source": src, "jsonl": jsonl.get(src, 0), "bm25": bm25.get(src, 0),
               "qdrant": qdrant.get(src, 0) if not errors or qdrant else None}
        if row["jsonl"] != row["bm25"] or (row["qdrant"] is not None and row["qdrant"] != row["jsonl"]):
            drift.append(row)
    return {
        "ok": not drift and not errors,
        "drift": drift,
        "errors": errors,
        "totals": {"jsonl": sum(jsonl.values()), "bm25": sum(bm25.values()),
                   "qdrant": sum(qdrant.values()) if qdrant else None},
    }


# ─── Evaluate ────────────────────────────────────────────────────────────────

@app.post("/evaluate")
async def evaluate():
    try:
        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(None, run_evaluation, CONFIG_PATH)
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/evaluate/latest")
def evaluate_latest():
    """Return the most recent evaluation summary JSON from eval/results/."""
    try:
        cfg = _load_cfg()
        out_dir = Path(cfg.get("evaluation", {}).get("output_dir", "eval/results"))
        if not out_dir.exists():
            return JSONResponse({"error": "no results yet", "files": []}, status_code=404)
        files = sorted(out_dir.glob("summary_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return JSONResponse({"error": "no summary files found", "files": []}, status_code=404)
        with files[0].open(encoding="utf-8") as f:
            data = json.load(f)
        data["_file"] = files[0].name
        data["_available"] = [f.name for f in files[:10]]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Entrypoint ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    cfg = _load_cfg()
    uvicorn.run(
        "api.main:app",
        host=cfg["api"]["host"],
        port=cfg["api"]["port"],
        reload=True,
    )
