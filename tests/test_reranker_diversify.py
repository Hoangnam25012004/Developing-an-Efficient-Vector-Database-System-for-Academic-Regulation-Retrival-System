"""Source diversity after reranking must never repeat a chunk.

The second pass of _diversify used to test `doc not in result`, but the
entries of `result` are copies carrying `_score_rerank`, so the test was
always true and the best chunks were appended again whenever the first,
per-source-capped pass picked fewer than top_k. These tests pin the fix and
show it changes nothing else: the first pass is untouched, and wherever the
old code produced no duplicate its output is reproduced exactly.
"""

import random
import unittest

from tests._util import ROOT  # noqa: F401

from src.retrieval.reranker import _diversify


def _diversify_before_fix(scored, top_k, max_per_source):
    """The implementation before the fix (commit 2c11fec), kept as the reference."""
    source_count = {}
    result = []
    for score, doc in scored:
        src = doc.get("source", "")
        if source_count.get(src, 0) < max_per_source:
            doc = dict(doc)
            doc["_score_rerank"] = float(score)
            result.append(doc)
            source_count[src] = source_count.get(src, 0) + 1
        if len(result) == top_k:
            return result
    for score, doc in scored:
        if len(result) == top_k:
            break
        if doc not in [r for r in result]:
            doc = dict(doc)
            doc["_score_rerank"] = float(score)
            result.append(doc)
    return result


def first_pass(scored, top_k, max_per_source):
    count, picked = {}, []
    for score, doc in scored:
        src = doc.get("source", "")
        if count.get(src, 0) < max_per_source:
            picked.append((doc["chunk_id"], float(score)))
            count[src] = count.get(src, 0) + 1
        if len(picked) == top_k:
            break
    return picked


def make(n, sources, rng=None):
    """n docs in descending score order, as rerank() hands them to _diversify."""
    rng = rng or random.Random(0)
    scores = sorted((rng.random() for _ in range(n)), reverse=True)
    return [(s, {"chunk_id": f"{i:016x}", "source": rng.choice(sources) if isinstance(sources, list) else sources,
                 "text": f"chunk {i}"}) for i, s in enumerate(scores)]


def pairs(result):
    return [(d["chunk_id"], d["_score_rerank"]) for d in result]


def first_hit(ids, relevant):
    return next((i for i, c in enumerate(ids) if c in relevant), None)


class DiversifyTest(unittest.TestCase):
    def test_single_source_is_filled_with_its_capped_chunks(self):
        scored = make(20, "a.pdf")
        out = _diversify(scored, 10, 5)
        self.assertEqual([c for c, _ in pairs(out)], [d["chunk_id"] for _, d in scored[:10]])
        self.assertEqual(pairs(out)[:5], pairs(_diversify_before_fix(scored, 10, 5))[:5])
        # the old code repeated the five best chunks instead
        old = [c for c, _ in pairs(_diversify_before_fix(scored, 10, 5))]
        self.assertEqual(old[5:], old[:5])

    def test_fewer_candidates_than_top_k_are_not_repeated(self):
        scored = make(3, ["a.pdf", "b.pdf", "c.pdf"])
        out = _diversify(scored, 10, 5)
        self.assertEqual(len(out), 3)
        self.assertEqual(len({c for c, _ in pairs(out)}), 3)

    def test_output_unchanged_when_the_first_pass_fills_top_k(self):
        rng = random.Random(1)
        scored = [(s, {"chunk_id": f"{i:016x}", "source": f"{i % 3}.pdf", "text": str(i)})
                  for i, s in enumerate(sorted((rng.random() for _ in range(12)), reverse=True))]
        self.assertEqual(pairs(_diversify(scored, 10, 5)), pairs(_diversify_before_fix(scored, 10, 5)))

    def test_scores_attached_and_inputs_untouched(self):
        scored = make(8, "a.pdf")
        out = _diversify(scored, 6, 2)
        self.assertEqual([d["_score_rerank"] for d in out], [float(s) for s, _ in scored[:6]])
        self.assertTrue(all("_score_rerank" not in d for _, d in scored))

    def test_chunks_without_chunk_id(self):
        scored = [(1.0 - i / 10, {"source": "a.pdf", "text": f"t{i}"}) for i in range(8)]
        out = _diversify(scored, 6, 2)
        self.assertEqual([d["text"] for d in out], [f"t{i}" for i in range(6)])

    def test_properties_on_random_inputs(self):
        rng = random.Random(20260926)
        for case in range(2000):
            n = rng.randint(0, 25)
            sources = [f"s{j}.pdf" for j in range(rng.randint(1, 5))]
            scored = make(n, sources, rng)
            top_k, cap = rng.randint(1, 12), rng.randint(1, 6)
            new = pairs(_diversify(scored, top_k, cap))
            old = pairs(_diversify_before_fix(scored, top_k, cap))
            new_ids, old_ids = [c for c, _ in new], [c for c, _ in old]
            msg = f"case {case}: n={n} sources={len(sources)} top_k={top_k} cap={cap}"

            f = first_pass(scored, top_k, cap)
            self.assertEqual(new[:len(f)], f, msg)                        # P1
            self.assertEqual(len(new_ids), len(set(new_ids)), msg)        # P2
            self.assertEqual(len(new), min(top_k, n), msg)
            if len(old_ids) == len(set(old_ids)):
                self.assertEqual(new, old, msg)                           # P3
            if n:
                self.assertEqual(new_ids[0], scored[0][1]["chunk_id"], msg)  # P4
            relevant = {d["chunk_id"] for _, d in scored if rng.random() < 0.2}
            for k in (1, 5, 10):                                          # P5
                old_hit = first_hit(old_ids[:k], relevant) is not None
                new_hit = first_hit(new_ids[:k], relevant) is not None
                self.assertTrue(new_hit or not old_hit, msg)
            # every chunk keeps its place or moves up, so the first hit never drops
            o, w = first_hit(old_ids[:10], relevant), first_hit(new_ids[:10], relevant)
            self.assertTrue(o is None or (w is not None and w <= o), msg)
            for pos, cid in enumerate(new_ids):
                if cid in old_ids:
                    self.assertLessEqual(pos, old_ids.index(cid), msg)


if __name__ == "__main__":
    unittest.main()
