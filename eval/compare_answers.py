"""Answer-quality A/B: baseline synthesis versus clause assembly + highlight + caps.

Retrieval is run once per question and the same ranked documents are fed to
both synthesis settings, so every difference comes from the answer layer and
not from retrieval noise. This matters here because the cross-encoder costs
~17 s per query — running the pipeline twice would double the wait and still
leave the comparison less clean.

Reported per question:
  chars        answer length — the sprawl the caps are meant to control
  tier         which synthesis pattern fired
  fragments    quoted clauses that are only part of a Khoản; these are the
               legally dangerous ones, since a missing opening condition or
               closing exception can invert a provision

Run from project root:
  python eval/compare_answers.py
  python eval/compare_answers.py --n 12 --show 3
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval._env import load_env  # noqa: E402

load_env()

from src.chat.clause_assembler import ClauseIndex  # noqa: E402
from src.chat.rag_chain import RAGChain, hybrid_answer  # noqa: E402
from src.config import load_config  # noqa: E402


def count_fragments(docs: list[dict], index: ClauseIndex) -> int:
    """Quoted docs that are one piece of a multi-part Khoản."""
    return sum(1 for d in docs if index.is_split(d) and not d.get("_assembled"))


def main() -> None:
    ap = argparse.ArgumentParser(description="A/B the answer-synthesis layer")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--n", type=int, default=10, help="questions to run")
    ap.add_argument("--show", type=int, default=2, help="answers to print in full")
    ap.add_argument("--questions", default=None, help="file with one question per line")
    args = ap.parse_args()

    cfg = load_config(str(ROOT / args.config))
    llm = cfg["llm"]

    if args.questions:
        questions = [
            q.strip()
            for q in Path(args.questions).read_text(encoding="utf-8").splitlines()
            if q.strip()
        ]
    else:
        gt = ROOT / cfg["evaluation"]["test_queries_path"]
        rows = [
            json.loads(line)
            for line in gt.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        # Spread across the query set rather than taking a contiguous block,
        # which would over-sample one topic.
        step = max(len(rows) // args.n, 1)
        questions = [r["question"] for r in rows[::step]][: args.n]

    print(f"Loading pipeline… ({len(questions)} questions)")
    chain = RAGChain(str(ROOT / args.config))
    index = ClauseIndex(
        cfg["data"]["processed_dir"],
        max_chars=int((llm.get("clause_assembly") or {}).get("max_chars", 3000)),
    )
    print(f"Clause index: {index.stats()}\n")

    rows_out = []
    for i, question in enumerate(questions, start=1):
        t0 = time.perf_counter()
        docs = chain.retrieve(question)
        retrieve_s = time.perf_counter() - t0

        base_answer, base_tier = hybrid_answer(
            question,
            docs,
            min_score=chain.min_score,
            single_dominance_gap=chain.single_dominance_gap,
            not_found_message=chain.not_found_message,
            highlight=0,
            tier2_max_clauses=0,
            tier3_max_clauses=0,
        )

        expanded = index.expand_all(docs)
        new_answer, new_tier = hybrid_answer(
            question,
            expanded,
            min_score=chain.min_score,
            single_dominance_gap=chain.single_dominance_gap,
            not_found_message=chain.not_found_message,
            highlight=int(llm.get("highlight_sentences", 1)),
            tier2_max_clauses=int(llm.get("tier2_max_clauses", 0)),
            tier3_max_clauses=int(llm.get("tier3_max_clauses", 0)),
        )

        row = {
            "question": question,
            "base_chars": len(base_answer),
            "new_chars": len(new_answer),
            "base_tier": base_tier,
            "new_tier": new_tier,
            "base_fragments": count_fragments(docs, index),
            "new_fragments": count_fragments(expanded, index),
            "assembled": sum(1 for d in expanded if d.get("_assembled")),
            "partial": sum(1 for d in expanded if d.get("_partial")),
            "retrieve_s": round(retrieve_s, 1),
        }
        rows_out.append(row)
        print(
            f"[{i}/{len(questions)}] {row['base_tier']}→{row['new_tier']}  "
            f"{row['base_chars']}→{row['new_chars']} chars  "
            f"frag {row['base_fragments']}→{row['new_fragments']}  "
            f"({retrieve_s:.0f}s)  {question[:52]}"
        )

        if i <= args.show:
            print("\n" + "═" * 78)
            print(f"Q: {question}")
            print("─" * 78 + "\n[BEFORE]\n")
            print(base_answer[:1600])
            print("\n" + "─" * 78 + "\n[AFTER]\n")
            print(new_answer[:1600])
            print("═" * 78 + "\n")

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    bc = [r["base_chars"] for r in rows_out]
    nc = [r["new_chars"] for r in rows_out]
    print(f"answer length   mean {statistics.mean(bc):8.0f} → {statistics.mean(nc):8.0f} chars")
    print(f"                max  {max(bc):8d} → {max(nc):8d} chars")
    bf, nf = sum(r["base_fragments"] for r in rows_out), sum(r["new_fragments"] for r in rows_out)
    print(f"fragment quotes      {bf:8d} → {nf:8d}   (incomplete clauses shown)")
    print(f"clauses reassembled  {sum(r['assembled'] for r in rows_out):8d}")
    print(f"capped as partial    {sum(r['partial'] for r in rows_out):8d}")

    for label, key in (("before", "base_tier"), ("after", "new_tier")):
        counts: dict[str, int] = {}
        for r in rows_out:
            counts[r[key]] = counts.get(r[key], 0) + 1
        print(f"tier mix {label:<7}", dict(sorted(counts.items())))

    out = ROOT / "eval" / "results" / f"answers_ab_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
