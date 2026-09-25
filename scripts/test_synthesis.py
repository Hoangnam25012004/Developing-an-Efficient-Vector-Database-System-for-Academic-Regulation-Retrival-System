"""Smoke test for the LLM-free synthesis tiers."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Load rag_chain.py directly without going through src.chat.__init__ which
# pulls in the full config + retriever stack (and needs dotenv etc.)
spec = importlib.util.spec_from_file_location(
    "rag_chain_iso", ROOT / "src/chat/rag_chain.py"
)
# Stub the imports rag_chain.py only needs at class init time
import types
fake_config = types.ModuleType("src.config")
fake_config.load_config = lambda *a, **k: {}
sys.modules["src.config"] = fake_config
fake_retr = types.ModuleType("src.retrieval.hybrid_retriever")
fake_retr.HybridRetriever = object
sys.modules["src.retrieval.hybrid_retriever"] = fake_retr
fake_rer = types.ModuleType("src.retrieval.reranker")
fake_rer.Reranker = object
sys.modules["src.retrieval.reranker"] = fake_rer

rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

hybrid_answer = rc.hybrid_answer
_classify_tier = rc._classify_tier


def make_chunk(source, article, khoan, score, text, article_title="", doc_number=""):
    return {
        "source": source, "page": 1,
        "doc_number": doc_number,
        "article": article, "article_title": article_title,
        "khoan": khoan, "_score_rerank": score,
        "text": text,
    }


print("=" * 70)
print("TEST 1: Tier 1 — single dominant Khoản (large gap)")
print("=" * 70)

t1_docs = [
    make_chunk("kyluat.pdf", "15", "3", 0.85,
               "Sinh viên thi hộ lần đầu bị đình chỉ học tập 01 năm học.",
               "Hình thức xử lý kỷ luật", "719/QĐ-ĐHQT"),
    make_chunk("kyluat.pdf", "15", "1", 0.20,
               "Khiển trách áp dụng đối với vi phạm nhẹ.",
               "Hình thức xử lý kỷ luật"),
    make_chunk("kyluat.pdf", "8", "2", 0.15,
               "Quy trình xử lý vi phạm.",
               "Quy trình"),
]
print(f"\nTier classified: {_classify_tier(t1_docs, 0.85, 0.05, 0.30)}")
print("\nAnswer:")
print(hybrid_answer("Thi hộ lần đầu bị xử lý thế nào?", t1_docs)[0])

print()
print("=" * 70)
print("TEST 2: Tier 2 — multiple Khoản from same Điều (no dominant gap)")
print("=" * 70)

t2_docs = [
    make_chunk("kyluat.pdf", "15", "1", 0.62,
               "Khiển trách áp dụng đối với vi phạm lần đầu, mức độ nhẹ.",
               "Hình thức xử lý kỷ luật", "719/QĐ-ĐHQT"),
    make_chunk("kyluat.pdf", "15", "2", 0.58,
               "Cảnh cáo áp dụng đối với vi phạm tái phạm.",
               "Hình thức xử lý kỷ luật"),
    make_chunk("kyluat.pdf", "15", "3", 0.55,
               "Đình chỉ học tập 01 năm áp dụng cho thi hộ lần đầu.",
               "Hình thức xử lý kỷ luật"),
    make_chunk("kyluat.pdf", "15", "4", 0.50,
               "Buộc thôi học áp dụng cho vi phạm đặc biệt nghiêm trọng.",
               "Hình thức xử lý kỷ luật"),
]
print(f"\nTier classified: {_classify_tier(t2_docs, 0.62, 0.05, 0.30)}")
print("\nAnswer:")
print(hybrid_answer("Các hình thức xử lý kỷ luật sinh viên?", t2_docs)[0])

print()
print("=" * 70)
print("TEST 3: Tier 3 — multi-source / multi-Điều")
print("=" * 70)

t3_docs = [
    make_chunk("mghp.pdf", "5", "2", 0.71,
               "Sinh viên thuộc diện hộ nghèo được miễn 100% học phí.",
               "Đối tượng được miễn", "688/QĐ-ĐHQT"),
    make_chunk("mghp.pdf", "8", "1", 0.65,
               "Hồ sơ bao gồm: đơn xin miễn (mẫu M01), giấy xác nhận hộ nghèo.",
               "Hồ sơ", "688/QĐ-ĐHQT"),
    make_chunk("tb486.pdf", "", "", 0.48,
               "Sinh viên nộp hồ sơ trước ngày 15/10 hằng năm tại Phòng CTSV.",
               "", "486-TB/DHQT-CTSV"),
]
print(f"\nTier classified: {_classify_tier(t3_docs, 0.71, 0.05, 0.30)}")
print("\nAnswer:")
print(hybrid_answer("Điều kiện miễn học phí và thủ tục nộp hồ sơ?", t3_docs)[0])

print()
print("=" * 70)
print("TEST 4: Tier 4 — not found (top score below min_score)")
print("=" * 70)

t4_docs = [
    make_chunk("random.pdf", "1", "", 0.02, "Some unrelated content."),
]
print(f"\nTier classified: {_classify_tier(t4_docs, 0.02, 0.05, 0.30)}")
print("\nAnswer:")
print(hybrid_answer("How do I cook pasta?", t4_docs)[0])

print()
print("DONE.")
