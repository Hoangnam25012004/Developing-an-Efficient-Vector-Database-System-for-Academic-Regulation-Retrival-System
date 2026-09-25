"""Render benchmark/ablation JSON into IEEE-style LaTeX tables.

Keeps the paper's numbers generated rather than transcribed, so a re-run
updates the manuscript instead of inviting copy errors.

Run from project root:
  python eval/make_tables.py                       # newest results
  python eval/make_tables.py --ablation eval/results/ablation_X.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "eval" / "results"


def newest(pattern: str) -> Path | None:
    files = sorted(RESULTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def esc(text: str) -> str:
    return str(text).replace("_", r"\_").replace("&", r"\&").replace("%", r"\%")


def table_ablation(path: Path, label: str = "tab:ablation") -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    gt = data.get("ground_truth", "")
    lpq = data.get("labels_per_query", "")

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        # The pool took 10 documents from the reranked run but only 3 from BM25
        # and dense, and unjudged documents count as non-relevant. Ranks 1--3
        # are therefore judged for every run and comparable; beyond rank 3 only
        # the reranked run is fully judged, so the deeper cutoffs flatter it.
        # HR@1 is the honest headline and the caption has to say why.
        r"\caption{Stage-wise retrieval ablation on %s (%s queries, %s labels/query), "
        r"each row scored on its own output. Pool depth was 10 for the reranked "
        r"run and 3 for BM25 and dense, with unjudged units treated as "
        r"non-relevant; HR@1 is thus unbiased across runs, while HR@5, MRR and "
        r"nDCG favour the more deeply pooled run.}"
        % (esc(gt), data.get("n_scored", ""), lpq),
        rf"\label{{{label}}}",
        r"\begin{tabular}{lccccr}",
        r"\toprule",
        r"Stage & HR@1 & HR@5$^\dagger$ & MRR@10$^\dagger$ & nDCG@10$^\dagger$ & Lat. (ms) \\",
        r"\midrule",
    ]
    best = max((r.get("hit_rate@1", 0) for r in rows), default=0)
    for r in rows:
        hr5 = r.get("hit_rate@5", 0)
        cell = rf"\textbf{{{r.get('hit_rate@1', 0):.3f}}}" \
            if r.get("hit_rate@1", 0) == best else f"{r.get('hit_rate@1', 0):.3f}"
        lines.append(
            f"{esc(r['stage'])} & {cell} & {hr5:.3f} & "
            f"{r.get('mrr@10', 0):.3f} & {r.get('ndcg@10', 0):.3f} & "
            f"{r.get('latency_mean_ms', 0):.1f} \\\\"
        )
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\vspace{2pt}",
        r"{\footnotesize $^\dagger$ affected by pool depth; see caption.}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def table_bench(path: Path, caption: str, label: str) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"]
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        rf"\caption{{{caption}}}",
        rf"\label{{{label}}}",
        r"\begin{tabular}{lccccc}",
        r"\toprule",
        # Server-side time is the index's own cost; the end-to-end column is
        # kept alongside it to show how far HTTP transport dominates at this scale.
        r"Configuration & ANN R@10 & HR@5 & Search p50 & Search p95 & E2E p50 \\",
        r" & & & (ms) & (ms) & (ms) \\",
        r"\midrule",
    ]
    for r in rows:
        recall = r.get("ann_recall@10")
        recall_s = f"{recall:.3f}" if isinstance(recall, (int, float)) else "--"
        lines.append(
            f"{esc(r.get('config', ''))} & {recall_s} & "
            f"{r.get('hit_rate@5', 0):.3f} & "
            f"{r.get('server_p50_ms', 0):.3f} & "
            f"{r.get('server_p95_ms', 0):.3f} & "
            f"{r.get('latency_p50_ms', 0):.1f} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines)


def table_scale(path: Path) -> str:
    """Brute force vs HNSW by corpus size, as a speedup table.

    Task metrics are deliberately omitted: corpus growth is simulated by
    perturbed replication, which injects near-duplicate distractors and
    depresses Hit Rate for reasons that have nothing to do with the index.
    Only latency and ANN recall are interpretable here.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs: dict[int, dict[str, dict]] = {}
    for r in data["rows"]:
        cfg = r.get("config", "")
        if not cfg.startswith("N="):
            continue
        n = int(cfg.split()[0][2:])
        kind = "exact" if "exact" in cfg else "hnsw"
        pairs.setdefault(n, {})[kind] = r

    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Brute-force scan versus HNSW as the corpus grows. Search time is "
        r"Qdrant's server-side figure. The scan cost grows linearly while the graph "
        r"grows sub-linearly, so the index only repays its overhead beyond roughly "
        r"20k vectors.}",
        r"\label{tab:scale}",
        r"\begin{tabular}{rrrrr}",
        r"\toprule",
        r"$N$ & Exact p50 & HNSW p50 & Speedup & ANN R@10 \\",
        r" & (ms) & (ms) & & \\",
        r"\midrule",
    ]
    for n in sorted(pairs):
        e, h = pairs[n].get("exact"), pairs[n].get("hnsw")
        if not e or not h:
            continue
        ep, hp = e.get("server_p50_ms", 0), h.get("server_p50_ms", 0)
        speedup = ep / hp if hp else 0
        bold = r"\textbf{%.2f$\times$}" % speedup if speedup >= 2 else r"%.2f$\times$" % speedup
        lines.append(
            f"{n:,} & {ep:.2f} & {hp:.2f} & {bold} & {h.get('ann_recall@10', 0):.3f} \\\\".replace(
                ",", r"\,"
            )
        )
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate LaTeX tables from results")
    ap.add_argument("--ablation", default=None)
    ap.add_argument("--out", default="Report/tables.tex")
    args = ap.parse_args()

    blocks: list[str] = []

    if args.ablation:
        abl = Path(args.ablation)
        if abl.exists():
            blocks.append(table_ablation(abl))
            print(f"ablation     ← {abl.name}")
    else:
        # The pooled, graded ground truth is the one to report. Runs against
        # the earlier document-scoped file were invalidated by the re-chunk
        # and live under results/invalid_pre_rechunk/, out of newest()'s way.
        p = newest("ablation_v2_*.json") or newest("ablation*.json")
        if p:
            blocks.append(table_ablation(p, "tab:ablation"))
            print(f"ablation     ← {p.name}")

    for pattern, caption, label in (
        ("bench_ef_*.json", "Query-time beam width (\\texttt{hnsw\\_ef}) sweep.", "tab:ef"),
        ("bench_quant_*.json", "Vector compression: memory versus recall.", "tab:quant"),
        ("bench_all_*.json", "Vector index configurations.", "tab:bench"),
    ):
        p = newest(pattern)
        if p:
            blocks.append(table_bench(p, caption, label))
            print(f"{label:<12} ← {p.name}")

    scale = newest("bench_scale_*.json")
    if scale:
        blocks.append(table_scale(scale))
        print(f"{'tab:scale':<12} ← {scale.name}")

    if not blocks:
        raise SystemExit("No result files found in eval/results/. Run the benchmarks first.")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    print(f"\nSaved {len(blocks)} table(s) → {out}")


if __name__ == "__main__":
    main()
