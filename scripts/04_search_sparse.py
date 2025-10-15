#!/usr/bin/env python
import argparse
import pickle
from pathlib import Path
from typing import Dict, List

import regex as re
from rank_bm25 import BM25Okapi
from utils import load_config, read_jsonl, vnfold


def tokenize_vi_basic(text: str) -> List[str]:
    # simple accent-folded whitespace tokenizer + punctuation split
    t = vnfold(text)
    return re.findall(r"[a-z0-9]+", t)


def build_index(jsonl_files: List[Path], out_path: Path, tokenizer: str):
    docs, metas = [], []
    for f in jsonl_files:
        rows = read_jsonl(f)
        for r in rows:
            docs.append(r["text"])  # raw text
            metas.append(r)
    if tokenizer == "vi_basic":
        tokenized = [tokenize_vi_basic(d) for d in docs]
    else:
        tokenized = [d.lower().split() for d in docs]
    bm25 = BM25Okapi(tokenized)
    with open(out_path, "wb") as f:
        pickle.dump({"bm25": bm25, "metas": metas, "tokenizer": tokenizer}, f)
    print(f"[ok] built BM25 index @ {out_path} (n={len(docs)})")


def search(query: str, index_path: Path, top_k: int, tokenizer: str):
    with open(index_path, "rb") as f:
        obj = pickle.load(f)
    bm25, metas, tok = obj["bm25"], obj["metas"], obj.get("tokenizer", tokenizer)
    tok_fn = tokenize_vi_basic if tok == "vi_basic" else (lambda s: s.lower().split())
    q = tok_fn(query)
    scores = bm25.get_scores(q)
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [{"score": float(scores[i]), **metas[i]} for i in order]


def main():
    parser = argparse.ArgumentParser(description="BM25 build/search")
    parser.add_argument("mode", choices=["build", "search"])
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--jsonl_glob", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--index", default=None)
    parser.add_argument("--query", default=None)
    parser.add_argument("--top_k", type=int, default=None)

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.mode == "build":
        glob_pat = args.jsonl_glob or cfg["paths"]["jsonl_glob"]
        files = list(sorted(Path().glob(glob_pat)))
        if not files:
            raise SystemExit(f"No JSONL files matched {glob_pat}")
        out_path = Path(args.out or cfg["paths"]["bm25_index_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        build_index(files, out_path, cfg["bm25"]["tokenizer"])
    else:
        index_path = Path(args.index or cfg["paths"]["bm25_index_path"])
        if not index_path.exists():
            raise SystemExit(f"BM25 index not found: {index_path}. Run build first.")
        res = search(
            args.query or "quy che",
            index_path,
            args.top_k or cfg["search"]["top_k"],
            cfg["bm25"]["tokenizer"],
        )
        for r in res:
            preview = (r["text"][:80]).replace("\n", " ")
            print(f"{r['score']:.4f}\t{r['doc_id']}\t{r['path_hierarchy']}\t{preview}…")


if __name__ == "__main__":
    main()
