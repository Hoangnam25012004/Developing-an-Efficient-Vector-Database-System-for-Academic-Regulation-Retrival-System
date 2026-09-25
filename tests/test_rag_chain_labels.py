"""Citation labels: legacy chunks are rendered exactly as before; chunks of
uploaded non-legal documents are named after their own structure."""

import importlib.util
import sys
import types
import unittest

from tests._util import ROOT


def load_rag_chain():
    # Same isolation as scripts/test_synthesis.py: no models, no Qdrant.
    stubs = {
        "src.config": types.SimpleNamespace(load_config=lambda *a, **k: {}),
        "src.retrieval.hybrid_retriever": types.SimpleNamespace(HybridRetriever=object),
        "src.retrieval.reranker": types.SimpleNamespace(Reranker=object),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("rag_chain_labels", ROOT / "src/chat/rag_chain.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


rc = load_rag_chain()


def chunk(**kw):
    base = {"source": "doc.pdf", "page": 2, "text": "Nội dung.", "chapter": "", "chapter_title": "",
            "article": "", "article_title": "", "khoan": ""}
    base.update(kw)
    return base


class LabelTest(unittest.TestCase):
    def test_legacy_chunks_use_the_legal_locator(self):
        c = chunk(article="15", article_title="Hình thức xử lý kỷ luật", khoan="3")
        self.assertEqual(rc._locator(c), rc._legal_locator(c))
        self.assertEqual(rc._locator(c), "Điều 15, Khoản 3 – Hình thức xử lý kỷ luật")

    def test_vietnamese_legal_documents_uploaded_later_keep_legal_labels(self):
        c = chunk(article="2", khoan="1", level_labels={"chapter": "", "article": "DIEU_VN", "khoan": "NUM_DOT"})
        self.assertEqual(rc._locator(c), "Điều 2, Khoản 1")

    def test_english_manual(self):
        c = chunk(article="3", article_title="Airfare", khoan="2", language="en",
                  level_labels={"chapter": "CHAPTER_EN", "article": "SECTION_EN", "khoan": "NUM_DOT"})
        self.assertEqual(rc._locator(c), "Section 3, item 2 – Airfare")

    def test_word_headings(self):
        c = chunk(chapter="2", chapter_title="Phạm vi áp dụng",
                  level_labels={"chapter": "HEADING_1", "article": "HEADING_2", "khoan": "HEADING_3"})
        self.assertEqual(rc._locator(c), "Phạm vi áp dụng")

    def test_tier2_heading_for_generic_structure(self):
        lab = {"chapter": "", "article": "SECTION_EN", "khoan": "NUM_DOT_NUM"}
        docs = [chunk(article="4", article_title="Leave", khoan=k, text=f"Rule {k}.", language="en",
                      level_labels=lab, _score_rerank=s) for k, s in (("4.2", 0.6), ("4.10", 0.58), ("4.1", 0.55), ("4.3", 0.5))]
        answer, tier = rc.hybrid_answer("leave rules", docs)
        self.assertEqual(tier, "tier2")
        self.assertIn("**Section 4 – Leave**", answer)
        self.assertNotIn("Điều", answer)
        self.assertLess(answer.index("Item 4.1"), answer.index("Item 4.2"))
        self.assertLess(answer.index("Item 4.3"), answer.index("Item 4.10"))    # numeric, not lexical

    def test_english_stopwords_only_for_english_chunks(self):
        self.assertEqual(rc._stopwords_for(chunk()), frozenset())
        self.assertIn("the", rc._stopwords_for(chunk(language="en")))
        # "an" is a Vietnamese syllable ("an toàn"): it must still count for Vietnamese text.
        text = "Quy định chung về nhà trường. Sinh viên phải đảm bảo an toàn phòng thí nghiệm."
        self.assertIn("**Sinh viên phải đảm bảo an toàn phòng thí nghiệm.**",
                      rc.highlight_relevant(text, "an toàn", 1, rc._stopwords_for(chunk())))


if __name__ == "__main__":
    unittest.main()
