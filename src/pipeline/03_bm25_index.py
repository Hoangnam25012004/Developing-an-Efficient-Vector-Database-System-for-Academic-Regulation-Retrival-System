"""
Step 03 – Build BM25 index from processed JSONL chunks.

Writes: data/processed/bm25.pkl  (corpus list + BM25Okapi object)
"""

import argparse
import json
import os
import pickle
import re
import time
from pathlib import Path

import yaml
from rank_bm25 import BM25Okapi

try:
    from underthesea import word_tokenize as _vi_tokenize
    _HAS_UNDERTHESEA = True
except ImportError:
    _HAS_UNDERTHESEA = False


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def simple_tokenize(text: str) -> list[str]:
    """Vietnamese-aware tokenizer using underthesea if available, else char-split fallback."""
    if _HAS_UNDERTHESEA:
        return _vi_tokenize(text.lower(), format="text").split()
    text = text.lower()
    tokens = re.split(r"[^\w]+", text, flags=re.UNICODE)
    return [t for t in tokens if t]


def load_chunks(processed_dir: Path, only_files: list[str] | None = None) -> list[dict]:
    """Load chunks from JSONL files.

    If only_files is None, load all *.jsonl in processed_dir.
    If only_files is set, load ONLY chunks from those specific JSONL files
    (basenames). Useful for incremental rebuilds when a new PDF is added.
    """
    only_set = {Path(f).name for f in only_files} if only_files else None
    records = []
    for jsonl in sorted(processed_dir.glob("*.jsonl")):
        if only_set is not None and jsonl.name not in only_set:
            continue
        with jsonl.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def _sources_of_jsonl(path: Path) -> set[str]:
    """The `source` values recorded inside a chunk file.

    Read from the records rather than derived from the file name, which used
    to assume every document was a PDF ('Foo.jsonl' → 'Foo.pdf') and so could
    never replace the chunks of a DOCX.
    """
    sources: set[str] = set()
    if not path.exists():
        return sources
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                sources.add(json.loads(line).get("source"))
    sources.discard(None)
    return sources


def _save_index(index_path: Path, records: list[dict], tokenized: list[list[str]], cfg: dict) -> None:
    """Build BM25 and write it next to the target, then swap it in.

    A reader (the API loading the index) never sees a half-written pickle;
    on Windows the swap is retried while a reader still has the file open.
    """
    bm25 = BM25Okapi(tokenized, k1=cfg["bm25"]["k1"], b=cfg["bm25"]["b"])
    payload = {"records": records, "tokenized": tokenized, "bm25": bm25}
    tmp = index_path.with_name(index_path.name + ".tmp")
    with tmp.open("wb") as f:
        pickle.dump(payload, f)
    for attempt in range(10):
        try:
            os.replace(tmp, index_path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2)


def rebuild_index(cfg: dict, log=print) -> int:
    """Full rebuild from every chunk file (same as the CLI without --only-files)."""
    processed_dir = Path(cfg["data"]["processed_dir"])
    index_path = Path(cfg["bm25"]["index_path"])
    index_path.parent.mkdir(parents=True, exist_ok=True)
    records = load_chunks(processed_dir)
    tokenized = [simple_tokenize(r["text"]) for r in records]
    _save_index(index_path, records, tokenized, cfg)
    log(f"BM25 index rebuilt: {len(records)} chunks")
    return len(records)


def update_index(cfg: dict, replace_sources=(), remove_sources=(), log=print) -> int:
    """Incrementally replace/remove documents in the BM25 index.

    Only the replaced documents are re-tokenized. The result is ordered exactly
    as a full rebuild would order it — chunk files sorted by path, records in
    file order — so the index, its scores and even the order of tied scores
    are identical to rebuilding from scratch. Returns the corpus size.
    """
    processed_dir = Path(cfg["data"]["processed_dir"])
    index_path = Path(cfg["bm25"]["index_path"])
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if not index_path.exists():
        log(f"No BM25 index at {index_path} — building from scratch.")
        records = load_chunks(processed_dir)
        tokenized = [simple_tokenize(r["text"]) for r in records]
        _save_index(index_path, records, tokenized, cfg)
        return len(records)

    replace_sources, remove_sources = set(replace_sources), set(remove_sources)
    with index_path.open("rb") as f:
        old = pickle.load(f)
    drop = replace_sources | remove_sources
    by_source: dict[str, list[tuple[dict, list[str]]]] = {}
    for rec, tok in zip(old["records"], old["tokenized"]):
        src = rec.get("source")
        if src not in drop:
            by_source.setdefault(src, []).append((rec, tok))
    kept = sum(len(v) for v in by_source.values())
    log(f"  kept {kept} chunks, dropping sources: {sorted(drop)}")

    added = 0
    for src in sorted(replace_sources):
        path = processed_dir / (Path(src).stem + ".jsonl")
        if not path.exists():
            continue
        items = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    if rec.get("source") == src:
                        items.append((rec, simple_tokenize(rec["text"])))
        if items:
            by_source[src] = items
            added += len(items)
    log(f"  added {added} chunks")

    def file_key(src):
        return processed_dir / (Path(src or "").stem + ".jsonl")

    records: list[dict] = []
    tokenized: list[list[str]] = []
    for src in sorted(by_source, key=file_key):
        for rec, tok in by_source[src]:
            records.append(rec)
            tokenized.append(tok)
    _save_index(index_path, records, tokenized, cfg)
    return len(records)


def run(config_path: str = "config.yaml", only_files: list[str] | None = None):
    """Build (or update) the BM25 index.

    Args:
        config_path: path to config.yaml
        only_files:  optional list of JSONL filenames (basenames). When given:
                     - Load existing bm25.pkl
                     - Drop records whose `source` matches the new JSONL files
                     - Add the chunks from those JSONL files
                     - Re-tokenize only the new chunks (reuse old tokenized)
                     - Rebuild BM25 in full-rebuild order
                     This is faster than full rebuild because tokenization for
                     the ~4000 old chunks is skipped.
                     If None → full rebuild from all *.jsonl files.
    """
    cfg = load_config(config_path)
    processed_dir = Path(cfg["data"]["processed_dir"])
    index_path = Path(cfg["bm25"]["index_path"])
    index_path.parent.mkdir(parents=True, exist_ok=True)

    if only_files and index_path.exists():
        # ── Incremental path ──────────────────────────────────────────────
        print(f"Incremental rebuild (only-files: {sorted({Path(f).name for f in only_files})})")
        replace_sources: set[str] = set()
        for name in only_files:
            replace_sources |= _sources_of_jsonl(processed_dir / Path(name).name)
        print(f"  Replacing chunks from sources: {sorted(replace_sources)}")
        size = update_index(cfg, replace_sources=replace_sources)
        print(f"BM25 index saved → {index_path}")
        print(f"Corpus size: {size} documents")
        return

    # ── Full rebuild path (original behaviour) ────────────────────────────
    if only_files and not index_path.exists():
        print(f"WARNING: --only-files requested but no existing index at {index_path}. "
              f"Falling back to full rebuild.")
    print("Loading chunks…")
    records = load_chunks(processed_dir)
    print(f"  {len(records)} chunks loaded.")

    print("Tokenizing…")
    tokenized = [simple_tokenize(r["text"]) for r in records]

    # ── Build + save ────────────────────────────────────────────────────────
    print("Building BM25 index…")
    _save_index(index_path, records, tokenized, cfg)

    print(f"BM25 index saved → {index_path}")
    print(f"Corpus size: {len(records)} documents")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build BM25 keyword index")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--only-files", nargs="+", default=None, metavar="FILENAME",
        help="Incremental mode: only re-tokenize chunks from these JSONL files "
             "(basenames, e.g. 'Tai-lieu-moi.jsonl'). Existing index is loaded "
             "and chunks from listed files are replaced — others are preserved. "
             "Falls back to full rebuild if no existing index found.",
    )
    args = parser.parse_args()
    run(args.config, only_files=args.only_files)
