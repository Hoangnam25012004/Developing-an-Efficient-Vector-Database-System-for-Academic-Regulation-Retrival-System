# Building the graded ground truth

Protocol and rationale for the relevance judgments, written so the paper's
methodology section can be lifted from here and so the collection can be
rebuilt by someone else.

## Why the previous ground truth had to be replaced

`test_queries_gt_100_v1.jsonl` labels 29.8 units per query — on average 43% of
the chunks in the query's source document. Only 27% of those labels sit at
clause level; **63% are table rows**, and half the queries have more than half
their labels drawn from a single table. The clearest case:

> *"Hiến máu tình nguyện (có giấy chứng nhận) được cộng bao nhiêu điểm?"*
> → 79 labels, 92% table rows

One row answers that question. Marking the whole scoring table relevant means a
system scores a hit by retrieving *any* row. The measured Hit Rate@5 of 0.87
therefore describes table routing, not row resolution — and the same
distinction explains why the clause-level Hit Rate is 0.54.

A second, quieter problem: the reference answers are human paraphrases, while
the system quotes verbatim. Scoring n-gram overlap between them measures the
format gap, not answer quality, which is most of why `answer_recall` sat at
0.314 and ROUGE-1 at 0.134.

## Method

Cranfield-style pooling (Voorhees, TREC), sized by the finding of Sanderson &
Zobel (SIGIR 2005) that for a fixed assessor budget, **more topics judged
shallowly** discriminate between systems better than fewer topics judged
deeply. All 100 topics are kept; only the head of each run is pooled.

**Pool.** For every topic, the union of:

| Run | Depth | Contributes |
|---|---|---|
| `hybrid-rerank` | 10 | what the system actually returns |
| `bm25` | 3 | lexical matches the dense model misses |
| `dense` | 3 | semantic matches BM25 misses |

Runs overlap, so the union averages ~11 units per topic — about 1,100
judgments, roughly two hours of work.

Pooling from three runs that fail differently is the point. A pool seeded from
one method inherits that method's blind spots, and the resulting collection
then flatters it. This is also why an automatic heuristic (for example scoring
bigram overlap against the reference answer) is not used to produce labels: it
would encode its own bias as ground truth. Cf. *Don't Use LLMs to Make
Relevance Judgments* (arXiv:2409.15133).

**Order.** Units are shuffled within a topic using a fixed seed. Judging in
rank order invites the assessor to anchor on the system's ranking.

**Grades.**

| Grade | Meaning |
|---|---|
| 2 | Answers the question directly; quoting this unit alone would satisfy the asker |
| 1 | Supporting — a condition, exception, definition or procedure needed to apply the answer, but not the answer |
| 0 | Not relevant, including units from the right document or right table that do not bear on the question |

Graded rather than binary because nDCG assumes graded relevance, and because
binary labels are what made the previous collection uninformative.

**Unjudged units are treated as grade 0.** Standard for pooled collections, and
stated here because it bounds reuse (below).

**Agreement.** 20% of topics are judged a second time in an independent pass
after all first passes finish. Report Cohen's kappa on the 3-level scale and on
the binary collapse. Voorhees (2000) showed assessors disagree substantially
while system *rankings* stay stable, so consistency matters more than a high
kappa — but the figure belongs in the paper.

## Running it

```bash
python eval/build_pool.py        # ~25 min, cross-encoder bound
python eval/judge.py             # ~2 h, resumable at any point
python eval/judge.py --stats     # progress and grade distribution
python eval/judge.py --kappa     # after pass 2
python eval/judge.py --export    # writes eval/test_queries_gt_v2.jsonl
```

Judging keys: `2` `1` `0` to grade, `b` to undo the last graded unit, `q` to
save and quit. Every keystroke is appended to `eval/judgments.jsonl`, so
quitting mid-topic costs nothing.

Then:

```bash
python eval/run_ablation.py --gt eval/test_queries_gt_v2.jsonl --tag v2
```

## Output

`test_queries_gt_v2.jsonl` carries `qrels` with grades plus `relevant_ids`
(grade ≥ 1) and `primary_ids` (grade = 2). The existing evaluator consumes
`relevant_ids` unchanged.

Each qrel stores `source`, `article`, `khoan` and `page` next to `chunk_id`.
`chunk_id` is a content hash, so it changes whenever chunk boundaries move —
and the parser still needs fixing for appendices. The denormalised locator lets
the judgments be re-resolved after a re-chunk instead of being thrown away.

## Reporting and limitations

State in the paper:

- pool depth per run, and the number of units judged
- that unjudged units are treated as non-relevant
- Cohen's kappa and the size of the doubly-judged subset
- P@1, P@5, MRR@10, nDCG@10 — shallow-friendly measures. Sanderson & Zobel
  also found the t-test more reliable than sign or Wilcoxon tests, and far
  more reliable than quoting a percentage difference.

**Reusability is the cost of shallow judging.** A future system that retrieves
relevant units nobody judged is penalised for it. Comparing variants that all
contributed to the pool — which is what this paper does — is sound; publishing
the collection as a general benchmark would need greater depth. `bpref` is
designed for incomplete judgments and is worth reporting alongside.

**One annotator.** Kappa on the doubly-judged subset is the only check on
consistency, and it is a self-agreement measure, not agreement between
independent people. Say so plainly.

## References

- Voorhees & Harman, *TREC: Experiment and Evaluation in Information Retrieval*
- Sanderson & Zobel, *IR System Evaluation: Effort, Sensitivity, and Reliability*, SIGIR 2005
- Yilmaz & Aslam, *Estimating Average Precision with Incomplete and Imperfect Judgments*
- Järvelin & Kekäläinen, *Cumulated Gain-Based Evaluation of IR Techniques* (nDCG)
- Buckley & Voorhees, *Retrieval Evaluation with Incomplete Information* (bpref)
- COLIEE statute-law retrieval — closest domain precedent
