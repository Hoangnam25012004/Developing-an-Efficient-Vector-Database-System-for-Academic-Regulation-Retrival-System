"""POST /chat returns the rerank and RRF scores of its sources.

chain.query() hands the API its sources with the scores under score_rerank /
score_rrf, while retrieve() and GET /search keep the internal _score_* keys.
The API reads either, so /chat no longer reports null scores and the other
endpoints return exactly what they returned before.
"""

import unittest
from unittest import mock

from tests._util import ROOT  # noqa: F401

ANSWER = "**Điều 1 – Phạm vi** [1]\n\n> 1. Nội dung.\n\n---\n**Tài liệu đính kèm:**\n[1]: a.pdf — trang 2"


class FakeChain:
    def query(self, question):
        return {
            "question": question,
            "answer": ANSWER,
            "tier": "tier1",
            "sources": [{
                "chunk_id": "c1", "source": "a.pdf", "page": 2, "article": "1", "khoan": "1",
                "text": "1. Nội dung.", "score_rrf": 0.03, "score_rerank": 0.91,
                "level_labels": None, "file_type": None, "language": None,
            }],
        }


class ChatScoresTest(unittest.TestCase):
    def setUp(self):
        # Imported here, not at module level: test_api_e2e must be the first to
        # import the API (it reads CONFIG_PATH at import time) when both run
        # in one process.
        from fastapi.testclient import TestClient
        import api.main as api_main

        self.api = api_main
        patcher = mock.patch.object(api_main, "get_chain", lambda: FakeChain())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(api_main.app)

    def test_chat_sources_carry_scores(self):
        r = self.client.post("/chat", json={"question": "Phạm vi là gì?"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["answer"], ANSWER)
        src = body["sources"][0]
        self.assertAlmostEqual(src["score_rerank"], 0.91)
        self.assertAlmostEqual(src["score_rrf"], 0.03)
        self.assertEqual((src["chunk_id"], src["source"], src["page"], src["article"], src["khoan"]),
                         ("c1", "a.pdf", 2, "1", "1"))

    def test_internal_keys_still_win(self):
        s = self.api._doc_to_source({"_score_rerank": 0.7, "score_rerank": 0.1, "_score_rrf": 0.2})
        self.assertEqual((s.score_rerank, s.score_rrf), (0.7, 0.2))

    def test_missing_scores_stay_null(self):
        s = self.api._doc_to_source({"chunk_id": "c", "source": "a.pdf", "_score_dense": 0.5})
        self.assertIsNone(s.score_rerank)
        self.assertIsNone(s.score_rrf)
        self.assertEqual(s.score_dense, 0.5)


if __name__ == "__main__":
    unittest.main()