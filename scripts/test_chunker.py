"""Smoke test for the HPAD chunker (src/pipeline/01_parse_chunk.py).

Exercises the content-driven path (detect_pattern → hierarchical_split), the
paragraph fallback, document classification, and long-leaf splitting. No PDF
input — operates on in-memory strings (PyMuPDF is imported by the module but
not used by these functions)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pc", ROOT / "src/pipeline/01_parse_chunk.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

structured_text = """
Chương I

QUY ĐỊNH CHUNG

Điều 1. Phạm vi điều chỉnh

Quyết định này quy định về xử lý kỷ luật sinh viên trường Đại học Quốc tế.

Điều 15. Hình thức xử lý kỷ luật

1. Khiển trách. Áp dụng đối với sinh viên vi phạm quy định lần đầu, mức độ nhẹ.

2. Cảnh cáo. Áp dụng đối với sinh viên đã bị khiển trách mà tái phạm hoặc vi phạm có tính chất nghiêm trọng hơn.

3. Đình chỉ học tập 01 năm học. Áp dụng đối với sinh viên thi hộ hoặc nhờ người thi hộ lần đầu, hoặc các vi phạm nghiêm trọng khác.

4. Buộc thôi học. Áp dụng đối với sinh viên thi hộ lần thứ hai, làm giả văn bằng chứng chỉ.

Điều 16. Trình tự xử lý

Việc xử lý kỷ luật sinh viên phải được thực hiện theo trình tự sau đây.
"""

unstructured_text = """
Phòng Công tác sinh viên (CTSV) hướng dẫn việc thực hiện chính sách miễn, giảm học phí (MGHP) áp dụng từ tháng 11 năm 2024.

Sinh viên thuộc diện được xét miễn, giảm học phí cần nộp hồ sơ theo mẫu M01 tại văn phòng CTSV trước ngày 15/10 hằng năm.

Hồ sơ bao gồm: đơn xin miễn giảm học phí, bản sao công chứng giấy tờ chứng minh diện được hưởng, và xác nhận của địa phương.
"""

print("=" * 70)
print("TEST 1: HPAD detects heading levels on a structured document")
print("=" * 70)
norm = pc._normalize_headers(structured_text)
winners = pc.detect_pattern(norm)
print("  Detected levels:", {lvl: lbl for lvl, (lbl, _) in winners.items()})
chunks = pc.hierarchical_split(norm, winners)
print(f"  Produced {len(chunks)} chunks:")
for i, c in enumerate(chunks, 1):
    print(f"  [{i}] section={c.get('section_type')}, chapter={c.get('chapter')!r}, "
          f"article={c.get('article')!r}, khoan={c.get('khoan')!r}")
    print(f"      {c.get('text','')[:90].replace(chr(10),' ')}...")

print("\n" + "=" * 70)
print("TEST 2: No heading pattern → paragraph fallback")
print("=" * 70)
winners2 = pc.detect_pattern(unstructured_text)
print("  Detected levels (expected empty):", dict(winners2))
chunks2 = pc.chunk_paragraph(unstructured_text)
print(f"  ParagraphChunker produced {len(chunks2)} chunks:")
for i, c in enumerate(chunks2, 1):
    print(f"  [{i}] section={c.get('section_type')}  {c.get('text','')[:80].replace(chr(10),' ')}...")

print("\n" + "=" * 70)
print("TEST 3: folder-based group metadata + doc_number extraction")
print("=" * 70)
registry = pc.load_group_registry()
gm = pc.group_meta("Quyet-dinh-quy-che-quy-dinh-day-du-cua-truong-dhqt", registry)
print(f"  group_meta → {gm}")
assert gm["doc_group"] == "Quyet-dinh-quy-che-quy-dinh-day-du-cua-truong-dhqt"
num = pc.extract_doc_number("Số: 586/QĐ-ĐHQT\nĐiều 1. ...")
print(f"  extract_doc_number('Số: 586/QĐ-ĐHQT ...') → {num!r}")
assert "ĐHQT" in num, "expected to recover the QĐ-ĐHQT number"

print("\n" + "=" * 70)
print("TEST 4: Long leaf is split, metadata preserved")
print("=" * 70)
long_text = pc._normalize_headers("""
Điều 30. Quy trình xét miễn giảm học phí

1. """ + ("Sinh viên nộp hồ sơ tại phòng Công tác sinh viên trong thời hạn quy định. " * 12) + """
""")
w = pc.detect_pattern(long_text)
chunks3 = pc.hierarchical_split(long_text, w)
print(f"  Produced {len(chunks3)} chunk(s):")
for i, c in enumerate(chunks3, 1):
    print(f"  [{i}] article={c.get('article')!r}, khoan={c.get('khoan')!r}, len={len(c.get('text',''))}")

print("\nDONE.")
