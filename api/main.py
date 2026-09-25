"""
FastAPI backend for the RAG chatbot.

Endpoints:
  GET  /health        – health check
  POST /chat          – full RAG query (retrieve + rerank + LLM)
  POST /retrieve      – retrieve + rerank only (no LLM)
  GET  /search        – search with selectable mode (hybrid_rerank|hybrid|dense|sparse)
  GET  /stats         – corpus statistics from processed JSONL files
  GET  /sources       – list source documents with metadata
  GET  /pdf/{filename} – serve PDF file from data dir
  GET  /config        – read config.yaml as JSON
  PUT  /config        – write config.yaml from JSON body
  POST /evaluate      – run evaluation suite
"""

import asyncio
import json
import os
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.chat.rag_chain import RAGChain
from src.evaluation.evaluator import run_evaluation

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.yaml")

app = FastAPI(
    title="ARRS – RAG Chatbot API",
    description="Hybrid search + reranking RAG over Vietnamese university regulations",
    version="2.0.0",
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
        score_rrf     = d.get("_score_rrf"),
        score_rerank  = d.get("_score_rerank"),
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

@app.get("/health")
def health():
    return {
        "status": "ok",
        "ready": _chain is not None,
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
    # Search recursively for the PDF
    for pdf_path in raw_dir.rglob("*.pdf"):
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
        with open(CONFIG_PATH, "w") as f:
            f.write(req.config)
        # Reset chain so it reloads config on next request
        global _chain
        _chain = None
        return {"status": "ok", "message": "Config updated. RAG chain will reload on next request."}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Reindex ─────────────────────────────────────────────────────────────────
#
# Triggered from the "Update Documents" button on the front-end. It runs the
# three offline-pipeline scripts in a background thread and returns immediately.
# State is exposed at GET /reindex/status so the UI can poll.

import importlib
import subprocess
import sys

_reindex_state: dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "started_at": None,
    "finished_at": None,
    "error": None,
    "log": [],
}


def _run_reindex():
    """Run the three pipeline scripts in order. Updates _reindex_state in place."""
    py = sys.executable
    stages = [
        ("parse_chunk",  ["-m", "src.pipeline.01_parse_chunk", "--config", CONFIG_PATH]),
        ("embed_index",  ["-m", "src.pipeline.02_embed_index", "--config", CONFIG_PATH]),
        ("bm25_index",   ["-m", "src.pipeline.03_bm25_index",  "--config", CONFIG_PATH]),
    ]
    _reindex_state["log"] = []
    try:
        for name, args in stages:
            _reindex_state["stage"] = name
            _reindex_state["log"].append(f"[{name}] starting…")
            proc = subprocess.run(
                [py, *args], capture_output=True, text=True, encoding="utf-8",
                errors="replace",
            )
            tail = "\n".join((proc.stdout or "").strip().splitlines()[-15:])
            _reindex_state["log"].append(tail)
            if proc.returncode != 0:
                err = (proc.stderr or "").strip().splitlines()[-10:]
                raise RuntimeError(f"{name} failed (exit {proc.returncode}):\n" + "\n".join(err))
        _reindex_state["stage"] = "done"
        # Force RAGChain to reload (new chunks → new BM25 + Qdrant data)
        global _chain
        with _chain_lock:
            _chain = None
    except Exception as exc:
        _reindex_state["error"] = str(exc)
        _reindex_state["stage"] = "error"
    finally:
        _reindex_state["running"] = False
        _reindex_state["finished_at"] = time.time()


@app.post("/reindex")
def reindex():
    """Kick off the full offline pipeline in a background thread."""
    if _reindex_state["running"]:
        raise HTTPException(status_code=409, detail="A reindex is already running.")
    _reindex_state.update({
        "running":     True,
        "stage":       "starting",
        "started_at":  time.time(),
        "finished_at": None,
        "error":       None,
        "log":         [],
    })
    threading.Thread(target=_run_reindex, daemon=True).start()
    return {"status": "started", "started_at": _reindex_state["started_at"]}


@app.get("/reindex/status")
def reindex_status():
    return _reindex_state


# ─── Group registry + document management ─────────────────────────────────────
#
# A "group" is a folder under data/. The user picks (or creates) a group when
# uploading; the parser derives doc metadata from the folder via the registry
# (data/groups.json). The endpoints below let the UI list/create/delete groups
# and delete individual PDFs. Filesystem changes only take effect in retrieval
# after a /reindex (the UI prompts for it).

ALLOWED_EXTENSIONS = {".pdf", ".docx"}


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
    _groups_path().write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def _slugify(name: str) -> str:
    """Folder-safe ASCII slug from a (possibly Vietnamese) group name."""
    s = (name or "").strip().replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return s or "group"


def _safe_group_name(group: str) -> str:
    """Reject traversal / processed dirs; return a single path component."""
    g = Path(group).name
    if not g or g in _processed_dir_names():
        raise HTTPException(status_code=400, detail=f"invalid group: {group!r}")
    return g


@app.get("/groups")
def list_groups():
    """List all groups = registry entries ∪ data/ subfolders (with file counts)."""
    cfg = _load_cfg()
    raw = Path(cfg["data"]["raw_dir"])
    reg = _load_groups()
    skip = _processed_dir_names()
    folders = {p.name for p in raw.iterdir() if p.is_dir() and p.name not in skip} if raw.is_dir() else set()
    out = []
    for key in sorted(set(reg) | folders):
        folder = raw / key
        n = len(list(folder.glob("*.pdf"))) if folder.is_dir() else 0
        e = reg.get(key, {})
        out.append({
            "id": key,
            "label": e.get("label", key),
            "doc_type": e.get("doc_type", ""),
            "issuing_body": e.get("issuing_body", ""),
            "file_count": n,
        })
    return {"groups": out}


class GroupCreate(BaseModel):
    label: str
    doc_type: Optional[str] = ""
    issuing_body: Optional[str] = ""


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
    reg[slug] = {"label": label, "doc_type": body.doc_type or "", "issuing_body": body.issuing_body or ""}
    _save_groups(reg)
    return {"id": slug, "label": label, "doc_type": body.doc_type or "", "issuing_body": body.issuing_body or "", "file_count": 0}


@app.delete("/groups/{group}")
def delete_group(group: str):
    """Delete a group folder (and all its PDFs) + its registry entry."""
    g = _safe_group_name(group)
    cfg = _load_cfg()
    folder = Path(cfg["data"]["raw_dir"]) / g
    removed = 0
    if folder.is_dir():
        removed = len(list(folder.glob("*.pdf")))
        shutil.rmtree(folder)
    reg = _load_groups()
    if g in reg:
        del reg[g]
        _save_groups(reg)
    return {"deleted_group": g, "removed_files": removed, "reindex_required": True}


@app.delete("/documents/{filename:path}")
def delete_document(filename: str):
    """Delete a single PDF (searched recursively under data/) + any sidecar."""
    cfg = _load_cfg()
    raw = Path(cfg["data"]["raw_dir"])
    name = Path(filename).name
    target = next((p for p in raw.rglob("*.pdf") if p.name == name), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"document not found: {name}")
    target.unlink()
    sidecar = target.with_suffix(target.suffix + ".meta.json")
    if sidecar.exists():
        sidecar.unlink()
    return {"deleted": name, "group": target.parent.name, "reindex_required": True}


@app.post("/upload-docs")
async def upload_docs(
    files: list[UploadFile] = File(...),
    group: str = Form(...),
):
    """Upload PDF/DOCX into data/<group>/.

    `group` is the target group: an existing group id, or a new name (which is
    slugified into a new folder + registry entry). The chosen group folder is
    what the parser reads to assign doc_group/doc_type/issuing_body — no content
    guessing. A /reindex is required afterwards for the files to be retrievable.
    """
    cfg = _load_cfg()
    raw = Path(cfg["data"]["raw_dir"])
    reg = _load_groups()

    raw_group = (group or "").strip()
    if not raw_group:
        raise HTTPException(status_code=400, detail="group is required")
    # Existing id/folder → use as-is; otherwise treat as a new group name.
    if raw_group in reg or (raw / raw_group).is_dir():
        slug = _safe_group_name(raw_group)
    else:
        slug = _slugify(raw_group)
        if slug not in reg:
            reg[slug] = {"label": raw_group, "doc_type": "", "issuing_body": ""}
            _save_groups(reg)

    dest_dir = raw / slug
    dest_dir.mkdir(parents=True, exist_ok=True)

    saved: list[dict] = []
    skipped: list[dict] = []
    for f in files:
        if not f.filename:
            skipped.append({"filename": "<empty>", "reason": "missing filename"})
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            skipped.append({"filename": f.filename, "reason": f"unsupported extension {ext}"})
            continue
        safe_name = Path(f.filename).name          # strip directory traversal
        dest = dest_dir / safe_name
        try:
            content = await f.read()
            dest.write_bytes(content)
            saved.append({"filename": safe_name, "size": len(content),
                          "group": slug, "path": str(dest)})
        except Exception as exc:
            skipped.append({"filename": safe_name, "reason": str(exc)})
    return {
        "saved":   saved,
        "skipped": skipped,
        "saved_count":   len(saved),
        "skipped_count": len(skipped),
        "group":         slug,
        "upload_dir":    str(dest_dir),
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
