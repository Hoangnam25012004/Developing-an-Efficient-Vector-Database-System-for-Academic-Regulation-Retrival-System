"""
Step 03 – Build BM25 index from processed JSONL chunks.

Writes: data/processed/bm25.pkl  (corpus list + BM25Okapi object)
"""

import argparse
import json
import pickle
import re
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


def _jsonl_to_source_name(jsonl_name: str) -> str:
    """Map 'Foo.jsonl' → 'Foo.pdf' (the source field used in chunk records)."""
    return Path(jsonl_name).stem + ".pdf"


def run(config_path: str = "config.yaml", only_files: list[str] | None = None):
    """Build (or update) the BM25 index.

    Args:
        config_path: path to config.yaml
        only_files:  optional list of JSONL filenames (basenames). When given:
                     - Load existing bm25.pkl
                     - Drop records whose `source` matches the new JSONL files
                     - Append new chunks from those JSONL files
                     - Re-tokenize only the new chunks (reuse old tokenized)
                     - Rebuild BM25 from combined tokenized
                     This is faster than full rebuild because tokenization for
                     the ~3800 old chunks is skipped.
                     If None → full rebuild from all *.jsonl files.
    """
    cfg = load_config(config_path)
    processed_dir = Path(cfg["data"]["processed_dir"])
    index_path = Path(cfg["bm25"]["index_path"])
    index_path.parent.mkdir(parents=True, exist_ok=True)

    if only_files and index_path.exists():
        # ── Incremental path ──────────────────────────────────────────────
        print(f"Incremental rebuild (only-files: {sorted({Path(f).name for f in only_files})})")

        print("Loading existing BM25 index…")
        with index_path.open("rb") as f:
            old_payload = pickle.load(f)
        old_records = old_payload["records"]
        old_tokenized = old_payload["tokenized"]
        print(f"  {len(old_records)} chunks in current index.")

        # Determine which source filenames to replace
        replace_sources = {_jsonl_to_source_name(f) for f in only_files}
        print(f"  Replacing chunks from sources: {sorted(replace_sources)}")

        # Keep records whose source is NOT in the replace set
        kept = [
            (rec, tok) for rec, tok in zip(old_records, old_tokenized)
            if rec.get("source") not in replace_sources
        ]
        kept_records  = [r for r, _ in kept]
        kept_tokenized = [t for _, t in kept]
        dropped = len(old_records) - len(kept_records)
        print(f"  Dropped {dropped} stale chunks.")

        # Load NEW chunks only from specified JSONL files
        print("Loading new chunks…")
        new_records = load_chunks(processed_dir, only_files=only_files)
        print(f"  {len(new_records)} new chunks loaded.")

        # Tokenize ONLY new chunks (old tokenized reused as-is)
        print("Tokenizing new chunks…")
        new_tokenized = [simple_tokenize(r["text"]) for r in new_records]

        records   = kept_records + new_records
        tokenized = kept_tokenized + new_tokenized
    else:
        # ── Full rebuild path (original behaviour) ────────────────────────
        if only_files and not index_path.exists():
            print(f"WARNING: --only-files requested but no existing index at {index_path}. "
                  f"Falling back to full rebuild.")
        print("Loading chunks…")
        records = load_chunks(processed_dir)
        print(f"  {len(records)} chunks loaded.")

        print("Tokenizing…")
        tokenized = [simple_tokenize(r["text"]) for r in records]

    # ── Build + save (shared) ───────────────────────────────────────────────
    print("Building BM25 index…")
    bm25 = BM25Okapi(tokenized, k1=cfg["bm25"]["k1"], b=cfg["bm25"]["b"])

    payload = {"records": records, "tokenized": tokenized, "bm25": bm25}
    with index_path.open("wb") as f:
        pickle.dump(payload, f)

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
