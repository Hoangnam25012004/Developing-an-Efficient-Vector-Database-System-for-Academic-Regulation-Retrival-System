"""Parser behaviour for uploaded documents, and what must NOT change for the
original corpus (records without a sidecar keep exactly 16 fields)."""

import json
import tempfile
import unittest
from pathlib import Path

from tests._util import make_docx, make_pdf, parser

pc = parser()
LEGACY_FIELDS = ["chunk_id", "source", "source_path", "page", "chunk_index", "text",
                 "doc_group", "doc_type", "doc_number", "issuing_body", "section_type",
                 "chapter", "chapter_title", "article", "article_title", "khoan"]

LEGAL_VN = (
    "Điều 1. Phạm vi điều chỉnh\n1. Quy định này áp dụng cho toàn bộ nhân viên của công ty.\n"
    "2. Nhân viên phải nộp báo cáo chi phí trong vòng ba mươi ngày sau chuyến công tác.\n"
    "Điều 2. Đối tượng áp dụng\n1. Cán bộ quản lý các cấp trong công ty và các đơn vị trực thuộc.\n"
    "2. Nhân viên chính thức, cộng tác viên và thực tập sinh của công ty.\n"
    "Điều 3. Hiệu lực thi hành\nQuy định này có hiệu lực kể từ ngày ký ban hành và thay thế các quy định trước đây.\n"
)
# Each chapter is longer than 120 characters: the (unchanged, paper) chunker
# treats a shorter "Chapter N ..." unit as heading-only and merges it away.
ENGLISH = (
    "Chapter 1. General provisions\n1. This policy applies to all employees of the company and to "
    "contractors who work on its premises or on its behalf in any country.\n"
    "Chapter 2. Travel\n1. Economy class is required for flights shorter than six hours unless a "
    "director approves another class of travel in writing before the trip.\n"
    "Chapter 3. Expenses\n1. Reimbursement requests without original receipts will be rejected by "
    "the finance department and returned to the employee for correction.\n"
)


def records(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class ParserIngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.out = self.dir / "out"
        self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_pdf_without_sidecar_keeps_the_legacy_record_shape(self):
        pdf = make_pdf(self.dir / "legacy.pdf", [LEGAL_VN])
        n, label = pc.process_pdf(pdf, self.out, "G", {"G": {"doc_type": "law", "issuing_body": "QH"}}, 250)
        n2, label2, report = pc.process_document(pdf, self.dir, "G", {"G": {"doc_type": "law", "issuing_body": "QH"}}, 250)
        self.assertGreater(n, 0)
        self.assertEqual((n, label), (n2, label2))
        a = (self.out / "legacy.jsonl").read_bytes()
        b = (self.dir / "legacy.jsonl").read_bytes()
        self.assertEqual(a, b)                    # process_pdf ≡ process_document without sidecar
        for rec in records(self.out / "legacy.jsonl"):
            self.assertEqual(list(rec), LEGACY_FIELDS)
            self.assertEqual((rec["doc_type"], rec["issuing_body"]), ("law", "QH"))
        self.assertEqual(report["pages"], 1)

    def test_uploaded_pdf_gets_v2_fields_and_metadata_overrides(self):
        pdf = make_pdf(self.dir / "new.pdf", [LEGAL_VN])
        side = {"language": "vi", "metadata": {"doc_type": "Chính sách", "doc_number": "12/QĐ", "issuing_body": ""}}
        pc.process_document(pdf, self.out, "G", {"G": {"doc_type": "law", "issuing_body": "QH"}}, 250, sidecar=side)
        recs = records(self.out / "new.jsonl")
        self.assertEqual(list(recs[0])[:16], LEGACY_FIELDS)
        self.assertEqual(list(recs[0])[16:], ["file_type", "language", "ingest_version", "level_labels"])
        self.assertEqual(recs[0]["doc_type"], "Chính sách")         # override
        self.assertEqual(recs[0]["doc_number"], "12/QĐ")            # override
        self.assertEqual(recs[0]["issuing_body"], "QH")             # empty override → group default
        self.assertEqual(recs[0]["level_labels"]["article"], "DIEU_VN")

    def test_english_profile_skips_ocr_and_restores_the_global_profile(self):
        pdf = make_pdf(self.dir / "en.pdf", [ENGLISH])
        calls = []
        original = pc._ocr_page
        pc._ocr_page = lambda page: calls.append(page.number) or ""
        try:
            before = json.dumps(pc._PROFILE, default=list, sort_keys=True)
            pc.process_document(pdf, self.out, "", {}, 250, sidecar={"language": "en"}, cfg={})
            self.assertEqual(calls, [])                             # no wasted OCR pass
            self.assertEqual(json.dumps(pc._PROFILE, default=list, sort_keys=True), before)
            pc.process_document(pdf, self.out, "", {}, 250, sidecar={"language": "vi"}, cfg={})
            self.assertEqual(len(calls), 1)                         # the Vietnamese check still applies
        finally:
            pc._ocr_page = original
        recs = records(self.out / "en.jsonl")
        self.assertEqual(recs[0]["level_labels"]["chapter"], "CHAPTER_EN")

    def test_docx_with_legal_numbering_uses_hpad(self):
        def build(d):
            for line in LEGAL_VN.strip().split("\n"):
                d.add_paragraph(line)
        doc = make_docx(self.dir / "quy-dinh.docx", build)
        n, label, report = pc.process_document(doc, self.out, "G", {}, 250, sidecar={"language": "vi"})
        self.assertTrue(label.startswith("hpad("), label)
        recs = records(self.out / "quy-dinh.jsonl")
        self.assertEqual(recs[0]["file_type"], "docx")
        self.assertIn("1", {r["article"] for r in recs})

    def test_docx_with_word_headings_uses_heading_chunker(self):
        def build(d):
            for title in ("Giới thiệu", "Phạm vi áp dụng", "Quy trình thực hiện"):
                d.add_heading(title, 1)
                d.add_paragraph(f"Nội dung chi tiết của phần {title.lower()} được mô tả đầy đủ ở đây.")
        doc = make_docx(self.dir / "so-tay.docx", build)
        n, label, _ = pc.process_document(doc, self.out, "", {}, 250, sidecar={"language": "vi"})
        self.assertTrue(label.startswith("heading("), label)
        recs = records(self.out / "so-tay.jsonl")
        self.assertEqual([r["chapter_title"] for r in recs], ["Giới thiệu", "Phạm vi áp dụng", "Quy trình thực hiện"])
        self.assertTrue(recs[1]["text"].startswith("Phạm vi áp dụng\n"))
        self.assertEqual(recs[0]["level_labels"]["chapter"], "HEADING_1")

    def test_listing_skips_internal_folders_and_word_lock_files(self):
        raw = self.dir / "data"
        for rel in ("G/a.pdf", "G/b.docx", "G/~$b.docx", "processed/_ingest/staging/x/c.pdf",
                    "processed_backup_1/d.pdf", "_ingest/e.pdf", "G/sub/.hidden/f.pdf"):
            p = raw / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"%PDF-1.4")
        pdfs, docxs = pc.list_documents(raw, {"processed", "processed_v2"})
        self.assertEqual(sorted(p.relative_to(raw).as_posix() for p in pdfs + docxs), ["G/a.pdf", "G/b.docx"])

    def test_prune_removes_only_orphan_chunk_files(self):
        (self.out / "keep.jsonl").write_text("{}\n", encoding="utf-8")
        (self.out / "gone.jsonl").write_text("{}\n", encoding="utf-8")
        (self.out / "bm25.pkl").write_bytes(b"x")
        removed = pc.prune_orphans(self.out, [self.dir / "G" / "Keep.pdf"])
        self.assertEqual(removed, ["gone.jsonl"])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["bm25.pkl", "keep.jsonl"])


if __name__ == "__main__":
    unittest.main()
