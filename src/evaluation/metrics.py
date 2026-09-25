"""
Retrieval & generation evaluation metrics — tuned for extractive RAG over
Vietnamese legal documents.

Retrieval  (require ground-truth relevant_ids per query):
  Precision@k, Recall@k, F1@k, Hit Rate@k, MRR@k, MAP@k, NDCG@k

Generation (require reference answers — no LLM needed):
  answer_recall   — fraction of reference bigrams found in the hypothesis
                    (preferred over ROUGE F1 for extractive systems: rewards
                     recall without penalising verbosity)
  answer_recall_f — fuzzy variant: uses sequence-matcher ratio per sentence
  bertscore_pho   — BERTScore F1 with vinai/phobert-base (Vietnamese-native)

Legacy metrics kept for comparison:
  rouge1, rougeL, bertscore (multilingual bert-base)
"""

import difflib
import math
import re
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# Shared text helpers
# ══════════════════════════════════════════════════════════════════════════════

def _tokenise(text: str) -> list[str]:
    return [t for t in re.findall(r'\w+', text.lower()) if len(t) >= 2]


def _bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def _split_sentences(text: str, min_len: int = 15) -> list[str]:
    parts = re.split(r'(?<=[.!?;:])\s+|\n+', text.strip())
    return [p.strip() for p in parts if len(p.strip()) >= min_len]


# ══════════════════════════════════════════════════════════════════════════════
# Retrieval metrics (single query)
# ══════════════════════════════════════════════════════════════════════════════

def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    top = retrieved[:k]
    return sum(1 for r in top if r in relevant) / k if top else 0.0


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return sum(1 for r in retrieved[:k] if r in relevant) / len(relevant)


def f1_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    p = precision_at_k(retrieved, relevant, k)
    r = recall_at_k(retrieved, relevant, k)
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def hit_rate_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    return 1.0 if any(r in relevant for r in retrieved[:k]) else 0.0


def reciprocal_rank(retrieved: list[str], relevant: set[str], k: int) -> float:
    for rank, r in enumerate(retrieved[:k], start=1):
        if r in relevant:
            return 1.0 / rank
    return 0.0


def average_precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    hits, score = 0, 0.0
    for rank, r in enumerate(retrieved[:k], start=1):
        if r in relevant:
            hits += 1
            score += hits / rank
    return score / min(len(relevant), k) if relevant else 0.0


def dcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    return sum(
        (1.0 if r in relevant else 0.0) / math.log2(rank + 1)
        for rank, r in enumerate(retrieved[:k], start=1)
    )


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    actual = dcg_at_k(retrieved, relevant, k)
    ideal  = dcg_at_k(list(relevant)[:k], relevant, k)
    return actual / ideal if ideal > 0 else 0.0


def bootstrap_ci(
    values: list[float],
    n_resamples: int = 10000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for a mean.

    With ~100 evaluation queries, differences of a few points between system
    variants are easily within sampling noise. Reporting an interval alongside
    each score keeps comparisons honest.
    """
    if not values:
        return (0.0, 0.0)

    import random

    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = means[int(alpha * n_resamples)]
    hi = means[min(int((1.0 - alpha) * n_resamples), n_resamples - 1)]
    return (round(lo, 4), round(hi, 4))


def paired_bootstrap_pvalue(
    a: list[float],
    b: list[float],
    n_resamples: int = 10000,
    seed: int = 42,
) -> float:
    """Two-sided paired bootstrap p-value for mean(a) - mean(b).

    Paired because both systems are scored on the same queries; the pairing
    removes per-query difficulty from the comparison.
    """
    if len(a) != len(b) or not a:
        return 1.0

    import random

    rng = random.Random(seed)
    n = len(a)
    diffs = [x - y for x, y in zip(a, b)]
    observed = sum(diffs) / n
    centred = [d - observed for d in diffs]

    extreme = 0
    for _ in range(n_resamples):
        resampled = sum(centred[rng.randrange(n)] for _ in range(n)) / n
        if abs(resampled) >= abs(observed):
            extreme += 1
    return round(extreme / n_resamples, 4)


def evaluate_retrieval(queries: list[dict], k_values: list[int] = (1, 3, 5, 10)) -> dict:
    """
    queries: list of {query, relevant_ids (GT), retrieved_ids (system output)}
    Returns mean score for every metric × k combination.
    """
    results: dict[str, list[float]] = {}
    for k in k_values:
        for name in ("precision", "recall", "f1", "hit_rate", "mrr", "map", "ndcg"):
            results.setdefault(f"{name}@{k}", [])

    for q in queries:
        retrieved = q["retrieved_ids"]
        relevant  = set(q["relevant_ids"])
        for k in k_values:
            results[f"precision@{k}"].append(precision_at_k(retrieved, relevant, k))
            results[f"recall@{k}"].append(recall_at_k(retrieved, relevant, k))
            results[f"f1@{k}"].append(f1_at_k(retrieved, relevant, k))
            results[f"hit_rate@{k}"].append(hit_rate_at_k(retrieved, relevant, k))
            results[f"mrr@{k}"].append(reciprocal_rank(retrieved, relevant, k))
            results[f"map@{k}"].append(average_precision_at_k(retrieved, relevant, k))
            results[f"ndcg@{k}"].append(ndcg_at_k(retrieved, relevant, k))

    return {m: round(sum(v) / len(v), 4) for m, v in results.items() if v}


# ══════════════════════════════════════════════════════════════════════════════
# Answer Recall  (primary generation metric for extractive RAG)
# ══════════════════════════════════════════════════════════════════════════════

def answer_recall(reference: str, hypothesis: str) -> float:
    """
    Bigram recall: fraction of reference bigrams found anywhere in hypothesis.

    Unlike ROUGE-1 F1, this does NOT penalise the system for producing a
    longer/more verbose answer — which is exactly what an extractive system does.
    A score of 1.0 means every reference bigram is covered; 0.0 means nothing
    from the reference appears in the answer.
    """
    ref_bg = _bigrams(_tokenise(reference))
    if not ref_bg:
        return 0.0
    hyp_bg = _bigrams(_tokenise(hypothesis))
    return round(len(ref_bg & hyp_bg) / len(ref_bg), 4)


def answer_recall_fuzzy(reference: str, hypothesis: str, threshold: float = 0.65) -> float:
    """
    Sentence-level fuzzy recall: for each reference sentence, check whether a
    sufficiently similar span exists anywhere in the hypothesis.

    Uses difflib SequenceMatcher (fast, no external deps).
    threshold=0.65 allows minor paraphrasing and punctuation differences.
    """
    ref_sents = _split_sentences(reference)
    if not ref_sents:
        return 0.0

    hits = 0
    for sent in ref_sents:
        # exact substring match (fast path)
        if sent.lower() in hypothesis.lower():
            hits += 1
            continue
        # fuzzy match against the whole hypothesis
        ratio = difflib.SequenceMatcher(None, sent.lower(), hypothesis.lower()).ratio()
        if ratio >= threshold:
            hits += 1

    return round(hits / len(ref_sents), 4)


# ══════════════════════════════════════════════════════════════════════════════
# BERTScore — PhoBERT (Vietnamese-native) + legacy multilingual fallback
# ══════════════════════════════════════════════════════════════════════════════

def compute_bertscore_batch(
    references: list[str],
    hypotheses: list[str],
    model_type: str = "xlm-roberta-base",
    lang: str = "vi",
) -> list[float]:
    """
    BERTScore F1 per pair.

    Default model: xlm-roberta-base — trained on 100 languages (Common Crawl),
    significantly better for Vietnamese than bert-base-multilingual-cased
    (0.877 vs 0.815 on test pairs).  PhoBERT is not supported by the
    bert_score registry and cannot be used directly.
    """
    from bert_score import score as bscore
    _, _, F1 = bscore(
        hypotheses, references,
        model_type=model_type,
        verbose=False,
    )
    return [round(f.item(), 4) for f in F1]


# ══════════════════════════════════════════════════════════════════════════════
# Legacy ROUGE (kept for comparison)
# ══════════════════════════════════════════════════════════════════════════════

def _rouge_scores(reference: str, hypothesis: str) -> dict[str, float]:
    from rouge_score import rouge_scorer
    scorer = rouge_scorer.RougeScorer(["rouge1", "rougeL"], use_stemmer=False)
    s = scorer.score(reference, hypothesis)
    return {
        "rouge1": round(s["rouge1"].fmeasure, 4),
        "rougeL": round(s["rougeL"].fmeasure, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Aggregate generation metrics
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_generation(
    references: list[str],
    hypotheses: list[str],
) -> dict[str, float]:
    """
    Primary metrics:   answer_recall, bertscore_xlmr
    Legacy comparison: rouge1, rougeL, bertscore_multi
    """
    if not references:
        return {
            "answer_recall":   0.0,
            "bertscore_xlmr":  0.0,
            "rouge1":          0.0,
            "rougeL":          0.0,
            "bertscore_multi": 0.0,
        }

    ar, r1, rL = [], [], []
    for ref, hyp in zip(references, hypotheses):
        ar.append(answer_recall(ref, hyp))
        rouge = _rouge_scores(ref, hyp)
        r1.append(rouge["rouge1"])
        rL.append(rouge["rougeL"])

    xlmr_scores  = compute_bertscore_batch(references, hypotheses, model_type="xlm-roberta-base")
    multi_scores = compute_bertscore_batch(references, hypotheses, model_type="bert-base-multilingual-cased")

    def mean(lst): return round(sum(lst) / len(lst), 4) if lst else 0.0

    return {
        # ── Primary (better for extractive RAG + Vietnamese) ──
        "answer_recall":   mean(ar),
        "bertscore_xlmr":  mean(xlmr_scores),
        # ── Legacy (kept for comparison) ──
        "rouge1":          mean(r1),
        "rougeL":          mean(rL),
        "bertscore_multi": mean(multi_scores),
    }


def evaluate_generation_per_query(
    references: list[str],
    hypotheses: list[str],
) -> list[dict[str, float]]:
    """Per-query generation metrics for the details JSON file."""
    xlmr_scores  = compute_bertscore_batch(references, hypotheses, model_type="xlm-roberta-base")
    multi_scores = compute_bertscore_batch(references, hypotheses, model_type="bert-base-multilingual-cased")
    out = []
    for ref, hyp, xlmr, multi in zip(references, hypotheses, xlmr_scores, multi_scores):
        rouge = _rouge_scores(ref, hyp)
        out.append({
            "answer_recall":   answer_recall(ref, hyp),
            "bertscore_xlmr":  xlmr,
            "rouge1":          rouge["rouge1"],
            "rougeL":          rouge["rougeL"],
            "bertscore_multi": multi,
        })
    return out
