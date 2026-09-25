import tempfile
import unittest
from pathlib import Path

from tests._util import make_docx, make_pdf, rewrite_zip
from src.ingest.validate import Limits, Rejected, inspect_file, suggest_language, sniff

VI = ("Điều 1. Phạm vi điều chỉnh\n1. Quy định này áp dụng cho toàn bộ nhân viên của công ty, "
      "bao gồm cộng tác viên và thực tập sinh làm việc tại các đơn vị trực thuộc.\n") * 4
EN = ("Chapter 1. General provisions\n1. This policy applies to all employees of the company, "
      "including contractors and interns who work at any of the offices.\n") * 4
# What a scan's broken non-Vietnamese OCR layer looks like (measured on this corpus).
BROKEN = ("DAI HQC QUOC GIA THANH PHO HO CHI MINH CQNG HOA XA HQI CHU NGHIA VIET NAM "
          "DQc l$p Tg do Hanh phuc QUY CHE Cong tac sinh vien ") * 6


class LanguageTest(unittest.TestCase):
    def test_vietnamese(self):
        self.assertEqual(suggest_language(VI), ("vi", "diacritics"))

    def test_english(self):
        self.assertEqual(suggest_language(EN), ("en", "english_stopwords"))

    def test_broken_vietnamese_layer_is_not_taken_for_english(self):
        self.assertEqual(suggest_language(BROKEN), ("vi", "no_diacritics"))

    def test_too_little_text(self):
        self.assertEqual(suggest_language("abc"), ("vi", "little_text"))


class InspectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.limits = Limits()

    def tearDown(self):
        self.tmp.cleanup()

    def reject_code(self, path, ext, limits=None):
        with self.assertRaises(Rejected) as ctx:
            inspect_file(path, ext, limits or self.limits)
        return ctx.exception.code

    def test_valid_pdf(self):
        p = make_pdf(self.dir / "a.pdf", [VI, VI])
        info = inspect_file(p, ".pdf", self.limits)
        self.assertEqual((info.file_type, info.pages, info.scanned_pages_estimate), ("pdf", 2, 0))
        # PyMuPDF returns the spaces of generated PDFs as NBSP; compare words.
        self.assertIn("Phạm vi", " ".join(info.text_sample.split()))

    def test_blank_pdf_pages_count_as_scans(self):
        p = make_pdf(self.dir / "scan.pdf", ["", ""])
        self.assertEqual(inspect_file(p, ".pdf", self.limits).scanned_pages_estimate, 2)

    def test_encrypted_pdf(self):
        p = make_pdf(self.dir / "locked.pdf", [VI], password="secret")
        self.assertEqual(self.reject_code(p, ".pdf"), "ENCRYPTED")

    def test_too_many_pages(self):
        p = make_pdf(self.dir / "long.pdf", [VI] * 3)
        self.assertEqual(self.reject_code(p, ".pdf", Limits(max_pages=2)), "TOO_MANY_PAGES")

    def test_too_large(self):
        p = make_pdf(self.dir / "big.pdf", [VI])
        self.assertEqual(self.reject_code(p, ".pdf", Limits(max_file_mb=0.0001)), "TOO_LARGE")

    def test_empty_file(self):
        p = self.dir / "empty.pdf"
        p.write_bytes(b"")
        self.assertEqual(self.reject_code(p, ".pdf"), "EMPTY_FILE")

    def test_valid_docx(self):
        p = make_docx(self.dir / "a.docx", lambda d: d.add_paragraph(VI))
        info = inspect_file(p, ".docx", self.limits)
        self.assertEqual(info.file_type, "docx")
        self.assertEqual(suggest_language(info.text_sample)[0], "vi")

    def test_docx_without_text(self):
        p = make_docx(self.dir / "blank.docx", lambda d: d.add_paragraph(""))
        self.assertEqual(self.reject_code(p, ".docx"), "NO_TEXT")

    def test_macro_document(self):
        src = make_docx(self.dir / "a.docx", lambda d: d.add_paragraph(VI))
        bad = rewrite_zip(src, self.dir / "m.docx", add={"word/vbaProject.bin": b"\x00" * 10})
        self.assertEqual(self.reject_code(bad, ".docx"), "MACRO_DOCUMENT")

    def test_zip_bomb_guard(self):
        src = make_docx(self.dir / "a.docx", lambda d: d.add_paragraph(VI))
        bomb = rewrite_zip(src, self.dir / "bomb.docx", add={"word/media/zeros.bin": b"\x00" * (3 * 1024 * 1024)})
        self.assertEqual(self.reject_code(bomb, ".docx", Limits(max_docx_uncompressed_mb=1)), "ARCHIVE_TOO_LARGE")
        # the compression ratio check catches it even under a generous size cap
        self.assertEqual(self.reject_code(bomb, ".docx"), "ARCHIVE_TOO_LARGE")

    def test_extension_must_match_content(self):
        docx_file = make_docx(self.dir / "a.docx", lambda d: d.add_paragraph(VI))
        disguised = self.dir / "fake.pdf"
        disguised.write_bytes(docx_file.read_bytes())
        self.assertEqual(self.reject_code(disguised, ".pdf"), "TYPE_MISMATCH")
        pdf_file = make_pdf(self.dir / "b.pdf", [VI])
        disguised2 = self.dir / "fake.docx"
        disguised2.write_bytes(pdf_file.read_bytes())
        self.assertEqual(self.reject_code(disguised2, ".docx"), "TYPE_MISMATCH")

    def test_legacy_doc_and_unsupported_types(self):
        ole = self.dir / "old.docx"
        ole.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
        self.assertEqual(self.reject_code(ole, ".docx"), "LEGACY_DOC")
        encrypted = self.dir / "enc.docx"
        encrypted.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "EncryptionInfo".encode("utf-16-le") + b"\x00" * 64)
        self.assertEqual(self.reject_code(encrypted, ".docx"), "ENCRYPTED")
        txt = self.dir / "a.txt"
        txt.write_text("hello", encoding="utf-8")
        self.assertEqual(self.reject_code(txt, ".txt"), "UNSUPPORTED_TYPE")
        self.assertEqual(self.reject_code(txt, ".doc"), "LEGACY_DOC")

    def test_sniff(self):
        self.assertEqual(sniff(b"%PDF-1.7"), "pdf")
        self.assertEqual(sniff(b"PK\x03\x04rest"), "zip")
        self.assertEqual(sniff(b"hello"), "unknown")


if __name__ == "__main__":
    unittest.main()
