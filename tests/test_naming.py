import unicodedata
import unittest

from tests._util import ROOT  # noqa: F401  (puts the project on sys.path)
from src.ingest.naming import safe_filename, slugify_group, split_name, stem_key, unique_name


class SafeFilenameTest(unittest.TestCase):
    def test_spaces_become_dashes_and_vietnamese_is_kept(self):
        self.assertEqual(safe_filename("Quy chế  mới 2026.PDF"), "Quy-chế-mới-2026.pdf")

    def test_nfd_input_is_normalised_to_nfc(self):
        nfd = unicodedata.normalize("NFD", "Quy chế.docx")
        self.assertEqual(safe_filename(nfd), unicodedata.normalize("NFC", "Quy-chế.docx"))

    def test_path_components_are_dropped(self):
        self.assertEqual(safe_filename("../../etc/passwd.pdf"), "passwd.pdf")
        self.assertEqual(safe_filename(r"C:\Users\x\report.docx"), "report.docx")

    def test_drive_letter_like_names_keep_their_text(self):
        # PureWindowsPath would read "a:" as a drive and drop it.
        self.assertEqual(safe_filename("a:b.pdf"), "a-b.pdf")

    def test_quotes_and_windows_forbidden_characters(self):
        self.assertEqual(safe_filename("Nam's <draft>|v2?.pdf"), "Nam-s-draft-v2.pdf")
        self.assertEqual(safe_filename("a`b.docx"), "a-b.docx")

    def test_reserved_and_empty_names(self):
        self.assertEqual(safe_filename("CON.pdf"), "doc-CON.pdf")
        self.assertEqual(safe_filename(".pdf"), "tai-lieu.pdf")
        self.assertEqual(safe_filename("report."), "report")

    def test_long_names_are_cut_but_keep_the_extension(self):
        name = safe_filename("x" * 300 + ".docx")
        self.assertTrue(name.endswith(".docx"))
        self.assertLessEqual(len(name), 120)

    def test_result_matches_the_citation_linkifier(self):
        # ui/index.html links "\S+\.(pdf|docx)" — no whitespace allowed.
        name = safe_filename("Báo cáo\tquý 3\n.pdf")
        self.assertNotRegex(name, r"\s")


class StemTest(unittest.TestCase):
    def test_split_name(self):
        self.assertEqual(split_name("a.b.PDF"), ("a.b", ".pdf"))
        self.assertEqual(split_name("noext"), ("noext", ""))

    def test_stem_key_ignores_case_and_extension(self):
        self.assertEqual(stem_key("Quy-che.PDF"), stem_key("quy-che.docx"))

    def test_unique_name(self):
        taken = {stem_key("a.pdf"), stem_key("a-2.pdf")}
        self.assertEqual(unique_name("a.pdf", taken), "a-3.pdf")
        self.assertEqual(unique_name("b.pdf", taken), "b.pdf")

    def test_slugify_group(self):
        self.assertEqual(slugify_group("Chính sách Nhân sự"), "Chinh-sach-Nhan-su")
        self.assertEqual(slugify_group("Đào tạo"), "Dao-tao")
        self.assertEqual(slugify_group("!!!"), "group")
        self.assertLessEqual(len(slugify_group("x" * 200)), 60)


if __name__ == "__main__":
    unittest.main()
