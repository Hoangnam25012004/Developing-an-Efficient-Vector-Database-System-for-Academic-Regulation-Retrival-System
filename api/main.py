import json
import os
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import regex as re
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict
from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer
from scripts.utils import load_config, vnfold



sys.path.append(os.path.join(os.path.dirname(__file__), "..", "scripts"))

app = FastAPI(title="Reg Retrieval API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

cfg = load_config("configs/default.yaml")

# Init models & indexes on startup
enc = SentenceTransformer(cfg["embedding"]["model_name"])  # dense
qclient = QdrantClient(url=cfg["qdrant"]["url"], api_key=cfg["qdrant"].get("api_key") or None)

bm25_obj = None
bm25_path = Path(cfg["paths"]["bm25_index_path"])
if bm25_path.exists():
    with open(bm25_path, "rb") as f:
        bm25_obj = pickle.load(f)


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="allow")   # pass ALL payload fields through

    score: float
    doc_id: str
    title: Optional[str] = None
    path_hierarchy: List[str] = []
    article_no: Optional[int] = None
    clause_no: Optional[int] = None
    point: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[str] = None
    faculty: Optional[str] = None
    language: Optional[str] = None
    source_file: Optional[str] = None
    # Fields from processed JSONL (may be present depending on ingestion)
    doc_type: Optional[str] = None
    issuing_authority: Optional[str] = None
    group: Optional[str] = None
    doc_number: Optional[str] = None
    scope: Optional[str] = None
    page: Optional[Any] = None
    text: str


@app.get("/health")
def health():
    return {"ok": True}


# ── PDF serving ──────────────────────────────────────────────────────────────
_RAW_DIR = Path("data/Raw")

def _find_pdf(filename: str) -> Path | None:
    """Search recursively in data/Raw for the given PDF filename."""
    for pdf_path in _RAW_DIR.rglob("*.pdf"):
        if pdf_path.name == filename:
            return pdf_path
    return None

@app.get("/pdf/{filename:path}")
def serve_pdf(filename: str):
    """Serve a PDF file from data/Raw by filename."""
    pdf_path = _find_pdf(filename)
    if pdf_path is None:
        raise HTTPException(status_code=404, detail=f"PDF '{filename}' not found")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/stats")
def get_stats():
    """Return basic statistics about indexed documents."""
    processed = Path(cfg["paths"]["processed_dir"])
    jsonl_files = list(processed.glob("*.jsonl"))
    total_docs = len(jsonl_files)
    total_chunks = len(bm25_obj["metas"]) if bm25_obj else 0
    return {
        "total_docs": total_docs,
        "total_chunks": total_chunks,
        "collection": cfg["qdrant"]["collection"],
        "embedding_model": cfg["embedding"]["model_name"],
        "reranker_model": cfg["reranker"]["model_name"],
    }


@app.get("/doc-list")
def list_docs():
    """List all processed documents with metadata."""
    processed = Path(cfg["paths"]["processed_dir"])
    result = []
    for fpath in sorted(processed.glob("*.jsonl")):
        rows = []
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        if rows:
            first = rows[0]
            result.append({
                "filename": fpath.name,
                "pdf_name": first.get("source_file", fpath.stem + ".pdf"),
                "group": first.get("group", ""),
                "doc_type": first.get("doc_type", ""),
                "title": first.get("title", ""),
                "doc_number": first.get("doc_number", ""),
                "chunk_count": len(rows),
                "version": first.get("version", ""),
                "effective_date": first.get("effective_date", ""),
                "issuing_authority": first.get("issuing_authority", ""),
            })
    return result


def tok_fn(s: str, mode: str):
    if mode == "vi_basic":
        return re.findall(r"[a-z0-9]+", vnfold(s))
    return s.lower().split()


@app.get("/search", response_model=List[SearchHit])
def search(
    query: str = Query(...),
    top_k: int = Query(10, ge=1, le=50),
    mode: str = Query("hybrid", enum=["dense", "sparse", "hybrid", "hybrid_rerank"]),
):
    results = []

    if mode in ("dense", "hybrid", "hybrid_rerank"):
        qvec = enc.encode([query], normalize_embeddings=cfg["embedding"].get("normalize", True))[0]
        dhits = qclient.search(
            collection_name=cfg["qdrant"]["collection"],
            query_vector=qvec.tolist(),
            limit=top_k,
            with_payload=True,
        )
        dres = [{"score": float(h.score), **h.payload} for h in dhits]
    else:
        dres = []

    if mode in ("sparse", "hybrid", "hybrid_rerank") and bm25_obj:
        bm25: BM25Okapi = bm25_obj["bm25"]
        metas = bm25_obj["metas"]
        tok = bm25_obj.get("tokenizer", cfg["bm25"]["tokenizer"])
        q = tok_fn(query, tok)
        scores = bm25.get_scores(q)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        sres = [{"score": float(scores[i]), **metas[i]} for i in order]
    else:
        sres = []

    if mode == "dense":
        results = dres
    elif mode == "sparse":
        results = sres
    elif mode == "hybrid":
        # RRF on ids
        def rrf(rank_lists, k=cfg["search"]["rrf_k"]):
            sc = {}
            for lst in rank_lists:
                for r, item in enumerate(lst):
                    did = item["doc_id"]
                    sc[did] = sc.get(did, 0) + 1.0 / (k + r + 1)
            return sc

        fused_scores = rrf([dres, sres])
        by_id = {}
        for it in dres + sres:
            by_id.setdefault(it["doc_id"], it)
        ranked = sorted(by_id.items(), key=lambda kv: fused_scores.get(kv[0], 0), reverse=True)[
            :top_k
        ]
        results = [v for _, v in ranked]
    else:
        # hybrid_rerank
        from sentence_transformers import CrossEncoder

        reranker = CrossEncoder(cfg["reranker"]["model_name"])  # loads once per process
        by_id = {}
        for it in dres + sres:
            by_id.setdefault(it["doc_id"], it)
        cands = list(by_id.values())[: max(50, top_k)]
        pairs = [(query, c["text"]) for c in cands]
        scores = reranker.predict(pairs)
        order = sorted(range(len(scores)), key=lambda i: float(scores[i]), reverse=True)[:top_k]
        results = [cands[i] | {"score": float(scores[i])} for i in order]

    return [SearchHit(**r) for r in results]

@app.get("/")
def root():
    return RedirectResponse(url="/docs")
