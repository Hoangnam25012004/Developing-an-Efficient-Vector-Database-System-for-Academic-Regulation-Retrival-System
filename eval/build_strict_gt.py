"""Derive a clause-level ground truth from the existing document-scoped one.

Why this exists
---------------
In test_queries_gt_100_v1.jsonl every query carries ~30 relevant_ids drawn
from a single source document — on average 43% of that document's chunks.
Relevance was therefore annotated at document/section granularity, not at the
Khoản level the system claims to resolve. Hit Rate under that ground truth
mostly answers "did we reach the right document?", which is a much weaker
claim than "did we find the right clause?", and it inflates every cutoff
metric: with 30 acceptable targets out of 4115 chunks, hitting one is easy.

What this does
--------------
Each query also has a human-written reference answer, and that answer was
copied from some specific clause. So for every query we score its annotated
candidates by how much of the reference answer they actually contain, and
keep only the chunks that carry it. The result is a strict, clause-level
ground truth derived from existing human annotation — no new labelling.

Reporting both is the honest move: lenient GT measures document routing,
strict GT measures clause resolution, and the gap between them is itself a
finding worth a sentence in the paper.

Run from project root:
  python eval/build_strict_gt.py
  python eval/build_strict_gt.py --threshold 0.5 --max-per-query 3
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def tokenise(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if len(t) >= 2]


def bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def coverage(reference: str, chunk_text: str) -> float:
    """Fraction of the reference answer's bigrams present in this chunk.

    Directional on purpose: a long clause containing the whole short answer
    should score 1.0, so the chunk is not penalised for extra legal text.
    """
    ref = bigrams(tokenise(reference))
    if not ref:
        return 0.0
    return len(ref & bigrams(tokenise(chunk_text))) / len(ref)


def load_corpus() -> dict[str, dict]:
    corpus: dict[str, dict] = {}
    for path in glob.glob(str(ROOT / "data" / "processed" / "*.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    corpus[d["chunk_id"]] = d
    return corpus


def main() -> None:
    ap = argparse.ArgumentParser(description="Derive clause-level ground truth")
    ap.add_argument("--gt", default="eval/test_queries_gt_100_v1.jsonl")
    ap.add_argument("--out", default="eval/test_queries_gt_100_strict.jsonl")
    ap.add_argument(
        "--threshold",
        type=float,
        default=0.45,
        help="minimum share of the reference answer a chunk must contain",
    )
    ap.add_argument(
        "--max-per-query",
        type=int,
        default=5,
        help="cap on strict labels per query, best-scoring first",
    )
    ap.add_argument(
        "--search-corpus",
        action="store_true",
        help="score the whole corpus, not just the annotated candidates "
        "(catches answers whose true clause was missed by the original annotation)",
    )
    args = ap.parse_args()

    corpus = load_corpus()
    print(f"Corpus: {len(corpus)} chunks")

    gt_path = ROOT / args.gt
    rows = [
        json.loads(line)
        for line in gt_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"Queries: {len(rows)}\n")

    out_rows = []
    n_empty = 0
    strict_sizes: list[int] = []
    best_scores: list[float] = []
    outside_candidates = 0

    for r in rows:
        reference = r.get("answer", "") or ""
        candidates = r.get("relevant_ids", []) or []
        pool = list(corpus.keys()) if args.search_corpus else candidates

        scored = []
        for cid in pool:
            chunk = corpus.get(cid)
            if not chunk:
                continue
            scored.append((coverage(reference, chunk.get("text", "")), cid))
        scored.sort(reverse=True)

        strict = [cid for score, cid in scored[: args.max_per_query] if score >= args.threshold]
        top_score = scored[0][0] if scored else 0.0
        best_scores.append(top_score)

        if args.search_corpus and strict and candidates:
            if strict[0] not in set(candidates):
                outside_candidates += 1

        if not strict:
            n_empty += 1

        strict_sizes.append(len(strict))
        out = dict(r)
        out["relevant_ids_lenient"] = candidates
        out["relevant_ids"] = strict
        out["strict_top_coverage"] = round(top_score, 4)
        out_rows.append(out)

    out_path = ROOT / args.out
    with out_path.open("w", encoding="utf-8") as f:
        for row in out_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    resolved = [s for s in strict_sizes if s > 0]
    print("── Strict ground truth ──────────────────────────────────")
    print(f"threshold            : {args.threshold}")
    print(f"queries resolved     : {len(resolved)}/{len(rows)}")
    print(f"unresolved (no chunk): {n_empty}  ← answer not verbatim in any candidate")
    if resolved:
        print(f"labels per query     : mean={statistics.mean(resolved):.2f} max={max(resolved)}")
    print(f"top coverage         : mean={statistics.mean(best_scores):.3f} median={statistics.median(best_scores):.3f}")
    if args.search_corpus:
        print(f"best chunk outside original annotation: {outside_candidates}")
    lenient_mean = statistics.mean([len(r.get("relevant_ids_lenient", [])) for r in out_rows])
    print(f"\nlenient labels/query : {lenient_mean:.1f}")
    print(f"strict  labels/query : {statistics.mean(strict_sizes):.2f}")
    print(f"\nSaved → {out_path}")
    print(
        "\nEvaluate against it with:\n"
        f"  python eval/run_ablation.py --gt {args.out}"
    )


if __name__ == "__main__":
    main()
