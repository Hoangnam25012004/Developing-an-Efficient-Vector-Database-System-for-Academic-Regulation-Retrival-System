"""Incremental BM25 must equal a full rebuild; incremental Qdrant operations
must touch only the documents they are given."""

import importlib
import json
import pickle
import tempfile
import unittest
from pathlib import Path

from tests._util import ROOT  # noqa: F401
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

bm = importlib.import_module("src.pipeline.03_bm25_index")
emb = importlib.import_module("src.pipeline.02_embed_index")


def chunk(source, idx, text):
    return {"chunk_id": f"{abs(hash((source, idx, text))) % (1 << 60):016x}", "source": source,
            "chunk_index": idx, "text": text}


def write_jsonl(path: Path, recs):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")


class BM25IncrementalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.cfg = {"data": {"processed_dir": str(d / "p")},
                    "bm25": {"index_path": str(d / "p" / "bm25.pkl"), "k1": 1.5, "b": 0.75}}
        self.proc = d / "p"
        self.proc.mkdir()
        write_jsonl(self.proc / "Beta.jsonl", [chunk("Beta.pdf", 0, "học phí miễn giảm sinh viên"),
                                              chunk("Beta.pdf", 1, "điều kiện học bổng khuyến khích")])
        write_jsonl(self.proc / "alpha.jsonl", [chunk("alpha.pdf", 0, "kỷ luật thi hộ đình chỉ")])
        bm.rebuild_index(self.cfg, log=lambda *_: None)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        with open(self.cfg["bm25"]["index_path"], "rb") as f:
            return pickle.load(f)

    def full(self):
        other = dict(self.cfg, bm25=dict(self.cfg["bm25"], index_path=str(self.proc / "full.pkl")))
        bm.rebuild_index(other, log=lambda *_: None)
        with open(other["bm25"]["index_path"], "rb") as f:
            return pickle.load(f)

    def assert_same_as_full(self):
        inc, full = self.load(), self.full()
        self.assertEqual([r["chunk_id"] for r in inc["records"]], [r["chunk_id"] for r in full["records"]])
        self.assertEqual(inc["tokenized"], full["tokenized"])
        q = bm.simple_tokenize("học bổng sinh viên thi hộ báo cáo")
        self.assertEqual(list(inc["bm25"].get_scores(q)), list(full["bm25"].get_scores(q)))

    def test_add_replace_and_remove_match_a_full_rebuild(self):
        write_jsonl(self.proc / "gamma.jsonl", [chunk("gamma.docx", 0, "báo cáo chi phí công tác")])
        bm.update_index(self.cfg, replace_sources={"gamma.docx"}, log=lambda *_: None)
        self.assert_same_as_full()

        write_jsonl(self.proc / "Beta.jsonl", [chunk("Beta.pdf", 0, "học bổng mới hoàn toàn")])
        bm.update_index(self.cfg, replace_sources={"Beta.pdf"}, log=lambda *_: None)
        self.assert_same_as_full()

        (self.proc / "alpha.jsonl").unlink()
        bm.update_index(self.cfg, remove_sources={"alpha.pdf"}, log=lambda *_: None)
        self.assert_same_as_full()
        self.assertNotIn("alpha.pdf", {r["source"] for r in self.load()["records"]})

    def test_sources_come_from_the_records_not_the_file_name(self):
        write_jsonl(self.proc / "gamma.jsonl", [chunk("gamma.docx", 0, "báo cáo")])
        self.assertEqual(bm._sources_of_jsonl(self.proc / "gamma.jsonl"), {"gamma.docx"})


class QdrantIncrementalTest(unittest.TestCase):
    def setUp(self):
        self.client = QdrantClient(":memory:")
        self.client.create_collection("t", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
        self.embed = lambda texts: [[1.0, float(len(t) % 7), 0.5, 0.1] for t in texts]

    def recs(self, source, texts):
        return [{"chunk_id": f"{(hash((source, i, t)) & ((1 << 63) - 1)):016x}", "source": source, "text": t}
                for i, t in enumerate(texts)]

    def test_upsert_prune_and_remove_by_source(self):
        a = self.recs("a.pdf", ["một", "hai", "ba"])
        b = self.recs("b.docx", ["bốn"])
        ids_a = emb.upsert_records(self.client, "t", a, self.embed)
        emb.upsert_records(self.client, "t", b, self.embed)
        self.assertEqual(emb.count_by_source(self.client, "t"), {"a.pdf": 3, "b.docx": 1})
        self.assertEqual(emb.point_ids_for_sources(self.client, "t", ["a.pdf"]), ids_a)

        # replace a.pdf by a shorter version: upsert new, delete the superseded ids
        a2 = self.recs("a.pdf", ["một", "hai mới"])
        before = emb.point_ids_for_sources(self.client, "t", ["a.pdf"])
        new_ids = emb.upsert_records(self.client, "t", a2, self.embed)
        emb.delete_point_ids(self.client, "t", before - new_ids)
        self.assertEqual(emb.point_ids_for_sources(self.client, "t", ["a.pdf"]), new_ids)
        self.assertEqual(emb.count_by_source(self.client, "t")["b.docx"], 1)   # untouched

        emb.delete_sources(self.client, "t", ["a.pdf"])
        self.assertEqual(emb.count_by_source(self.client, "t"), {"b.docx": 1})
        self.assertEqual(emb.collection_vector_size(self.client, "t"), 4)
        self.assertIsNone(emb.collection_vector_size(self.client, "missing"))


if __name__ == "__main__":
    unittest.main()
