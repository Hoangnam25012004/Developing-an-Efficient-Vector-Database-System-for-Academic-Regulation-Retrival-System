"""Runs one indexing job in its own process (see jobs.py).

    python -m src.ingest.worker --config config.yaml --job <jobs/<id>.json>

Job types
  ingest   parse the listed documents, upsert their points, drop the points
           and chunks they replace, update BM25. Per document; one failure
           does not stop the others (the job ends "partial").
  remove   drop the listed sources from Qdrant, their chunk files and BM25.
  reindex  parse everything again. mode=sync keeps the collection live
           (upsert + prune stale ids); mode=rebuild recreates it (the old
           behaviour, needed when the embedding model or vector size changes).

Order of operations within a document is chosen so that a failure at any
step leaves the served index consistent (design §5.6): nothing the reader can
see changes until the new points are in Qdrant, and the chunk file is swapped
only after that.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.ingest.jobs import now_iso  # noqa: E402
from src.ingest.registry import (  # noqa: E402
    IngestPaths, atomic_write_json, load_sidecar, read_json, replace_with_retry,
    save_sidecar, sidecar_path, sources_in_jsonl,
)
from src.ingest.validate import message  # noqa: E402

pc = importlib.import_module("src.pipeline.01_parse_chunk")
emb = importlib.import_module("src.pipeline.02_embed_index")
bm = importlib.import_module("src.pipeline.03_bm25_index")


class DocError(Exception):
    def __init__(self, code: str, detail: str = "", report: dict | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.report = report or {}


class Worker:
    def __init__(self, config_path: str, job_path: Path):
        self.config_path = config_path
        self.job_path = job_path
        self.job = read_json(job_path)
        if not isinstance(self.job, dict):
            raise SystemExit(f"cannot read job file {job_path}")
        self.cfg = load_config(config_path)
        self.paths = IngestPaths.from_cfg(self.cfg)
        self.paths.ensure()
        self.collection = self.cfg["vector_store"]["collection_name"]
        self.vector_size = int(self.cfg["vector_store"]["vector_size"])
        self._client = None
        self._embed = None
        pc.configure(self.cfg)

    # ── job file ──────────────────────────────────────────────────────────
    def save(self) -> None:
        atomic_write_json(self.job_path, self.job)

    def log(self, msg: str) -> None:
        print(msg, flush=True)

    def progress(self, step: str, done: int = 0, total: int = 0, current: str = "") -> None:
        self.job["progress"] = {"step": step, "done": done, "total": total, "current": current}
        self.save()

    # ── lazy heavy resources ─────────────────────────────────────────────
    def client(self):
        if self._client is None:
            try:
                client = emb.get_qdrant_client(self.cfg)
                size = emb.collection_vector_size(client, self.collection)
            except Exception as exc:
                raise DocError("QDRANT_UNAVAILABLE", str(exc))
            if size is None:
                emb.ensure_collection(client, self.collection, self.vector_size,
                                      fresh=False, vs=self.cfg["vector_store"])
            elif size != self.vector_size:
                raise DocError("INDEX_FAILED",
                               f"collection vector size {size} ≠ config {self.vector_size}; run a rebuild")
            self._client = client
        return self._client

    def embedder(self):
        if self._embed is None:
            threads = int((self.cfg.get("ingest") or {}).get("worker_threads") or 0)
            try:
                import torch
                if threads <= 0:
                    threads = max(1, (os.cpu_count() or 2) // 2)
                torch.set_num_threads(threads)
            except Exception:
                pass
            self._embed = emb.get_embedder(self.cfg)
        return self._embed

    # ── helpers ──────────────────────────────────────────────────────────
    def doc_path(self, source: str, group: str) -> Path:
        return (self.paths.raw_dir / group / source) if group else (self.paths.raw_dir / source)

    def registry(self) -> dict:
        return pc.load_group_registry(self.paths.groups_json)

    def max_tokens(self) -> int:
        return int((self.cfg.get("chunking") or {}).get("max_tokens", 256))

    @staticmethod
    def read_records(path: Path) -> list[dict]:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def set_sidecar(self, doc_path: Path, **fields) -> None:
        side = load_sidecar(doc_path)
        if side is None:
            return                      # documents without a sidecar stay untouched
        side.update(fields)
        save_sidecar(doc_path, side)

    # ══════════════════════════════════════════════════════════════════════
    #  ingest
    # ══════════════════════════════════════════════════════════════════════
    def run_ingest(self) -> None:
        items = (self.job.get("params") or {}).get("items", [])
        tmp_dir = self.paths.tmp_dir / self.job["id"]
        tmp_dir.mkdir(parents=True, exist_ok=True)
        registry = self.registry()
        succeeded: list[dict] = []
        results = self.job.setdefault("results", {})

        for i, item in enumerate(items):
            src, group = item["source"], item.get("group") or ""
            replaces = item.get("replaces")
            doc_path = self.doc_path(src, group)
            self.progress("parse", i, len(items), src)
            self.set_sidecar(doc_path, status="processing",
                             status_detail={"step": "parse", "job_id": self.job["id"]})
            before_ids: set[int] | None = None
            candidate_ids: set[int] = set()
            try:
                if not doc_path.exists():
                    raise DocError("FILE_MISSING")
                try:
                    n, label, report = pc.process_document(
                        doc_path, tmp_dir, group, registry, self.max_tokens(),
                        sidecar=load_sidecar(doc_path), cfg=self.cfg,
                    )
                except DocError:
                    raise
                except Exception as exc:
                    raise DocError("PARSE_FAILED", f"{type(exc).__name__}: {exc}")
                if n == 0:
                    raise DocError("EMPTY_CONTENT", report=report)
                tmp_jsonl = tmp_dir / (doc_path.stem + ".jsonl")
                records = self.read_records(tmp_jsonl)
                candidate_ids = {emb.chunk_id_to_point_id(r["chunk_id"]) for r in records}

                client = self.client()
                stale_sources = [src] + ([replaces] if replaces and replaces != src else [])
                before_ids = emb.point_ids_for_sources(client, self.collection, stale_sources)
                self.progress("embed", i, len(items), src)
                self.set_sidecar(doc_path, status_detail={"step": "embed", "job_id": self.job["id"]})
                try:
                    embed = self.embedder()
                    new_ids = emb.upsert_records(client, self.collection, records, embed)
                except Exception as exc:
                    raise DocError("EMBED_FAILED", f"{type(exc).__name__}: {exc}")

                # Only now does the chunk file change: the served index already
                # holds the new points, so the file and the vectors agree. The
                # superseded points are deleted last, after BM25 has switched —
                # a failure before then leaves extra points, never missing ones.
                final_jsonl = self.paths.processed_dir / (doc_path.stem + ".jsonl")
                replace_with_retry(tmp_jsonl, final_jsonl)
                if replaces and Path(replaces).stem.casefold() != doc_path.stem.casefold():
                    old_jsonl = self.paths.processed_dir / (Path(replaces).stem + ".jsonl")
                    if old_jsonl.exists() and sources_in_jsonl(old_jsonl) <= {replaces}:
                        old_jsonl.unlink()
                succeeded.append({"source": src, "group": group, "replaces": replaces,
                                  "doc_path": doc_path, "report": report,
                                  "stale_ids": before_ids - new_ids})
                self.log(f"  [OK] {src}: {n} chunks ({label})")
            except Exception as exc:
                err = exc if isinstance(exc, DocError) else DocError("PARSE_FAILED", f"{type(exc).__name__}: {exc}")
                self.log(f"  [FAIL] {src}: {err.code} {err.detail}")
                self._rollback_points(candidate_ids, before_ids)
                status = "empty" if err.code == "EMPTY_CONTENT" else "failed"
                msg = message(err.code, detail=err.detail)
                if replaces:
                    msg = f"Phiên bản mới lỗi: {msg} Đã giữ nguyên phiên bản cũ."
                    self._restore_previous(doc_path, replaces, group)
                else:
                    self.set_sidecar(doc_path, status=status, report=err.report or None,
                                     status_detail={"step": status, "error_code": err.code, "message": msg})
                results[src] = {"status": status, "error_code": err.code, "message": msg}
                self.save()

        if not succeeded:
            return
        # One BM25 update for the whole batch (sources replaced + sources superseded).
        self.progress("bm25", len(items), len(items), "")
        replace_sources = {s["source"] for s in succeeded}
        remove_sources = {s["replaces"] for s in succeeded if s["replaces"] and s["replaces"] != s["source"]}
        try:
            bm.update_index(self.cfg, replace_sources=replace_sources,
                            remove_sources=remove_sources, log=self.log)
        except Exception as exc:
            msg = message("INDEX_FAILED", detail=f"{type(exc).__name__}: {exc}")
            for s in succeeded:
                self.set_sidecar(s["doc_path"], status="failed",
                                 status_detail={"step": "bm25", "error_code": "INDEX_FAILED", "message": msg})
                results[s["source"]] = {"status": "failed", "error_code": "INDEX_FAILED", "message": msg}
            self.save()
            return

        self.progress("cleanup", len(items), len(items), "")
        for s in succeeded:
            try:
                emb.delete_point_ids(self._client, self.collection, s["stale_ids"])
            except Exception as exc:
                # Extra points only; /index/health reports them and a sync removes them.
                s["report"].setdefault("warnings", []).append("stale_points_remaining")
                self.log(f"  [WARN] could not delete superseded points of {s['source']}: {exc}")
            if s["replaces"]:
                self._drop_previous(s["doc_path"], s["replaces"], s["group"])
            self.set_sidecar(s["doc_path"], status="indexed", report=s["report"], indexed_at=now_iso(),
                             status_detail={"step": "done", "error_code": None, "message": None})
            results[s["source"]] = {"status": "indexed", "report": s["report"]}
        self.save()

    def _rollback_points(self, candidate_ids: set[int], before_ids: set[int] | None) -> None:
        """Remove points this attempt may have added (never the old ones)."""
        if before_ids is None or not candidate_ids or self._client is None:
            return
        try:
            emb.delete_point_ids(self._client, self.collection, candidate_ids - before_ids)
        except Exception as exc:
            self.log(f"  [WARN] rollback of new points failed: {exc}")

    def _prev_paths(self, replaces: str, group: str) -> tuple[Path, Path]:
        old = self.doc_path(replaces, group)
        return old.with_name(old.name + ".prev"), sidecar_path(old).with_name(sidecar_path(old).name + ".prev")

    def _restore_previous(self, new_doc: Path, replaces: str, group: str) -> None:
        """A replacement failed: put the old version back, keep the new one aside."""
        prev_doc, prev_side = self._prev_paths(replaces, group)
        failed_dir = self.paths.failed_dir / self.job["id"]
        failed_dir.mkdir(parents=True, exist_ok=True)
        try:
            if new_doc.exists():
                shutil.move(str(new_doc), str(failed_dir / new_doc.name))
            new_side = sidecar_path(new_doc)
            if new_side.exists():
                shutil.move(str(new_side), str(failed_dir / new_side.name))
            old_doc = self.doc_path(replaces, group)
            if prev_doc.exists():
                replace_with_retry(prev_doc, old_doc)
            if prev_side.exists():
                replace_with_retry(prev_side, sidecar_path(old_doc))
        except OSError as exc:
            self.log(f"  [WARN] could not restore previous version of {replaces}: {exc}")

    def _drop_previous(self, new_doc: Path, replaces: str, group: str) -> None:
        for p in self._prev_paths(replaces, group):
            try:
                if p.exists():
                    p.unlink()
            except OSError as exc:
                self.log(f"  [WARN] could not delete {p.name}: {exc}")

    # ══════════════════════════════════════════════════════════════════════
    #  remove
    # ══════════════════════════════════════════════════════════════════════
    def run_remove(self) -> None:
        items = (self.job.get("params") or {}).get("items", [])
        sources = [it["source"] for it in items if it.get("source")]
        results = self.job.setdefault("results", {})
        self.progress("qdrant", 0, len(sources), "")
        try:
            emb.delete_sources(self.client(), self.collection, sources)
        except DocError:
            raise
        except Exception as exc:
            raise DocError("QDRANT_UNAVAILABLE", str(exc))
        self.progress("chunks", 0, len(sources), "")
        for item in items:
            src = item.get("source")
            if not src:
                continue
            jl = self.paths.processed_dir / (Path(src).stem + ".jsonl")
            if jl.exists() and sources_in_jsonl(jl) <= {src}:
                jl.unlink()
            # A sidecar written back by a job that was still running when the
            # document was deleted would otherwise linger next to nothing.
            doc_path = self.doc_path(src, item.get("group") or "")
            side = sidecar_path(doc_path)
            if not doc_path.exists() and side.exists():
                side.unlink()
        self.progress("bm25", len(sources), len(sources), "")
        bm.update_index(self.cfg, remove_sources=set(sources), log=self.log)
        for src in sources:
            results[src] = {"status": "removed"}
        self.save()

    # ══════════════════════════════════════════════════════════════════════
    #  reindex (sync | rebuild)
    # ══════════════════════════════════════════════════════════════════════
    def run_reindex(self) -> None:
        mode = (self.job.get("params") or {}).get("mode", "sync")
        raw_dir = self.paths.raw_dir
        skip = {self.paths.processed_dir.name, "processed", "processed_v2"}
        pdfs, docxs = pc.list_documents(raw_dir, skip)
        docs = pdfs + docxs
        registry = self.registry()
        out = self.paths.tmp_dir / self.job["id"] / "jsonl"
        out.mkdir(parents=True, exist_ok=True)
        results = self.job.setdefault("results", {})

        owner: dict[str, Path] = {}
        parsed: list[Path] = []
        for i, doc in enumerate(docs):
            self.progress("parse", i, len(docs), doc.name)
            key = doc.stem.casefold()
            if key in owner:
                results[doc.name] = {"status": "failed", "error_code": "STEM_CONFLICT",
                                     "message": f"Trùng tên (stem) với {owner[key].name}"}
                continue
            owner[key] = doc
            rel = doc.relative_to(raw_dir)
            group = rel.parts[0] if len(rel.parts) > 1 else ""
            try:
                n, label, report = pc.process_document(doc, out, group, registry, self.max_tokens(),
                                                       sidecar=load_sidecar(doc), cfg=self.cfg)
            except Exception as exc:
                results[doc.name] = {"status": "failed", "error_code": "PARSE_FAILED",
                                     "message": message("PARSE_FAILED", detail=str(exc))}
                continue
            if n == 0:
                results[doc.name] = {"status": "empty", "error_code": "EMPTY_CONTENT",
                                     "message": message("EMPTY_CONTENT")}
                continue
            parsed.append(out / (doc.stem + ".jsonl"))
            results[doc.name] = {"status": "indexed", "report": report}
        self.save()

        records: list[dict] = []
        for jl in parsed:
            records.extend(self.read_records(jl))

        client = None
        try:
            client = emb.get_qdrant_client(self.cfg)
            size = emb.collection_vector_size(client, self.collection)
        except Exception as exc:
            raise DocError("QDRANT_UNAVAILABLE", str(exc))
        if mode == "sync" and size is not None and size != self.vector_size:
            self.log(f"vector size {size} ≠ {self.vector_size}: switching to rebuild")
            mode = "rebuild"
        self.job["params"]["mode_used"] = mode
        if mode == "rebuild" or size is None:
            emb.ensure_collection(client, self.collection, self.vector_size, fresh=(mode == "rebuild"),
                                  vs=self.cfg["vector_store"])
        self._client = client

        model = self.cfg["embedding"]["local_model"]
        state = read_json(self.paths.index_state, {}) or {}
        existing = emb.all_point_ids(client, self.collection) if mode == "sync" else set()
        trusted = (mode == "sync" and state.get("embedding_model") == model
                   and state.get("points") == len(existing))
        new_ids = {emb.chunk_id_to_point_id(r["chunk_id"]) for r in records}
        # A point id is a hash of the chunk's source, position and text, so an
        # id already present under the same model holds the same vector.
        to_embed = [r for r in records
                    if not trusted or emb.chunk_id_to_point_id(r["chunk_id"]) not in existing]
        self.progress("embed", 0, len(to_embed), "")
        if to_embed:
            emb.upsert_records(client, self.collection, to_embed, self.embedder(),
                               progress=lambda d, t: self.progress("embed", d, t, ""))

        # A document that failed to parse this time keeps what it had: its
        # chunk file and its points stay, instead of silently leaving the index.
        failed_sources = [d.name for d in docs if results.get(d.name, {}).get("status") != "indexed"]
        protected = emb.point_ids_for_sources(client, self.collection, failed_sources) if mode == "sync" else set()
        if mode == "sync":
            emb.delete_point_ids(client, self.collection, existing - new_ids - protected)

        # Swap the chunk files in, and drop the ones whose document is gone.
        self.progress("chunks", 0, len(parsed), "")
        for jl in parsed:
            replace_with_retry(jl, self.paths.processed_dir / jl.name)
        document_stems = {d.stem.casefold() for d in docs}
        for jl in self.paths.processed_dir.glob("*.jsonl"):
            if jl.stem.casefold() not in document_stems:
                jl.unlink()
                self.log(f"  pruned orphan chunk file {jl.name}")

        self.progress("bm25", 0, 0, "")
        bm.rebuild_index(self.cfg, log=self.log)

        atomic_write_json(self.paths.index_state, {
            "embedding_model": model,
            "vector_size": self.vector_size,
            "points": len(emb.all_point_ids(client, self.collection)),
            "updated_at": now_iso(),
        })
        for doc in docs:
            if results.get(doc.name, {}).get("status") == "indexed":
                self.set_sidecar(doc, status="indexed", indexed_at=now_iso(),
                                 report=results[doc.name].get("report"),
                                 status_detail={"step": "done", "error_code": None, "message": None})
        self.save()

    # ══════════════════════════════════════════════════════════════════════
    def run(self) -> int:
        jtype = self.job.get("type")
        self.log(f"== job {self.job['id']} ({jtype}) started {now_iso()}")
        try:
            if jtype == "ingest":
                self.run_ingest()
            elif jtype == "remove":
                self.run_remove()
            elif jtype == "reindex":
                self.run_reindex()
            else:
                raise DocError("PARSE_FAILED", f"unknown job type {jtype}")
            statuses = [r.get("status") for r in (self.job.get("results") or {}).values()]
            bad = [s for s in statuses if s in ("failed", "empty")]
            if not statuses or not bad:
                self.job["status"] = "succeeded"
            elif len(bad) < len(statuses):
                self.job["status"] = "partial"
            else:
                self.job["status"] = "failed"
                self.job["error"] = {"code": "ALL_FAILED", "message": "Không tài liệu nào được index."}
            code = 0
        except Exception as exc:
            err = exc if isinstance(exc, DocError) else DocError("WORKER_CRASHED", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
            self.job["status"] = "failed"
            self.job["error"] = {"code": err.code, "message": message(err.code, detail=err.detail)}
            if jtype == "ingest":
                for item in (self.job.get("params") or {}).get("items", []):
                    src = item.get("source")
                    if src and src not in self.job.get("results", {}):
                        doc_path = self.doc_path(src, item.get("group") or "")
                        self.set_sidecar(doc_path, status="failed", status_detail={
                            "step": "failed", "error_code": err.code, "message": self.job["error"]["message"]})
                        self.job.setdefault("results", {})[src] = {
                            "status": "failed", "error_code": err.code, "message": self.job["error"]["message"]}
            code = 1
        finally:
            shutil.rmtree(self.paths.tmp_dir / self.job["id"], ignore_errors=True)
        self.job["finished_at"], self.job["finished_ts"] = now_iso(), time.time()
        self.job["progress"] = {**(self.job.get("progress") or {}), "step": "done"}
        self.save()
        self.log(f"== job {self.job['id']} {self.job['status']} {now_iso()}")
        return code


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one ingestion job")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--job", required=True)
    args = ap.parse_args()
    sys.exit(Worker(args.config, Path(args.job)).run())


if __name__ == "__main__":
    main()
