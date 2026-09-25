# Evaluation & vector-database benchmarks

Experiment harness for the KSE 2026 submission. Everything here is
deterministic and re-runnable: the encoder runs once into a cache, and every
later stage reads that cache, so results do not drift between runs.

## Why this harness exists

The thesis pipeline had three measurement problems that these scripts fix.

**1. The HNSW index was never built.** Qdrant only constructs the graph once a
segment exceeds `indexing_threshold`, and only consults it once a segment
exceeds `full_scan_threshold`. Both defaulted to 10,000 while the corpus holds
4,115 chunks, so `indexed_vectors_count` stayed at 0 and every query ran as a
brute-force scan. Any claim about the vector index was therefore untested.
`config.yaml` now exposes both thresholds, and `bench_vectordb.py` forces the
graph to exist so ANN can actually be measured.

**2. Retrieval metrics scored the wrong thing.** `evaluator.py` takes
`retrieved_ids` from the final answer's sources — after reranking, dedup, the
`max_per_source` cap and `top_k` truncation. Those numbers describe the answer
composer, not the retriever, and cannot attribute a gain to BM25, the vector
index, fusion, or the cross-encoder. `run_ablation.py` scores each stage on its
own output instead.

**3. The ground truth was document-scoped.** Each query carries ~30
`relevant_ids`, averaging 43% of its source document's chunks — so Hit Rate
largely answered "did we reach the right document?", not "did we find the right
clause?". `build_strict_gt.py` derives a clause-level ground truth from the
human reference answers. Report both: the gap between them is informative.

## Setup

Qdrant must be running locally:

```bash
docker run -d --name qdrant -p 6333:6333 -v qdrant_storage:/qdrant/storage qdrant/qdrant
```

Build the embedding cache once (~14 min on CPU for 4,115 chunks):

```bash
python eval/build_cache.py
```

Writes `eval/cache/`: corpus vectors, chunk payloads, query vectors, query
metadata. Delete the directory to force a re-encode after changing the encoder
or re-chunking the corpus.

## Vector-database benchmarks

```bash
python eval/bench_vectordb.py --experiment all
```

| Experiment | Question it answers |
|---|---|
| `exact` | Brute-force baseline; also the ground truth for ANN recall |
| `ef` | How query-time beam width trades recall against latency |
| `build` | What `m` / `ef_construct` cost to build and what they buy |
| `quant` | int8 and binary compression: recall lost vs latency gained |
| `scale` | The corpus size at which HNSW starts beating brute force |

Two clocks are reported, and the distinction matters:

- **`server_*`** — Qdrant's own `time` field: the search itself.
- **`latency_*`** — wall time around the HTTP call.

At this corpus size the end-to-end figure is roughly 15× the search time, so
reporting only client latency would hide the index entirely behind transport
overhead. Quote `server_*` for any claim about the index.

The `disk_mb` column measures the collection directory inside the container.
It is dominated by segment and WAL preallocation, not by vector payload
(4,115 × 768 × 4 B ≈ 12.6 MB), so it does not discriminate between
quantization settings and should not be presented as a memory result.

## Retrieval ablation

```bash
# lenient, document-scoped ground truth (as shipped)
python eval/run_ablation.py --tag lenient

# strict, clause-level ground truth
python eval/build_strict_gt.py --search-corpus --threshold 0.5 --max-per-query 3
python eval/run_ablation.py --gt eval/test_queries_gt_100_strict.jsonl --tag strict
```

Stages: `bm25`, `dense-exact`, `dense-hnsw`, `hybrid-rrf`, `hybrid-rerank`.
Each is scored on its own output, so the contribution of each component is
isolated. Hit Rate@5 carries a percentile-bootstrap 95% CI — with 100 queries,
differences of a few points are not distinguishable from sampling noise.

`--search-corpus` scores every chunk rather than only the annotated ones. On
this data it relocated the answer-bearing clause for 37 of 61 resolved queries,
i.e. the original annotation had missed the true clause.

## Tables

```bash
python eval/make_tables.py
```

Reads the newest result files and writes IEEE-style LaTeX into
`Report/tables.tex`, so re-running an experiment updates the manuscript rather
than inviting transcription errors.

## Hardware note

All figures were collected on CPU (Intel Iris Xe is not CUDA-capable, so
PyTorch runs on CPU regardless). This matches the deployment target — a single
institutional workstation — and should be stated with the results.
