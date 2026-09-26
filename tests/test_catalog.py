"""GET /documents carries each document's library-catalogue entry (data/catalog.json).

The catalogue only adds a `catalog` field: every other field keeps its value,
a missing or broken file changes nothing, and an entry written for another
file of the same name (different size) is not applied.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._util import ROOT, make_pdf

DISPLAY_KEYS = {"title", "title_en", "doc_number", "year", "issued", "issuing_body"}


class CatalogTest(unittest.TestCase):
    def setUp(self):
        # Imported here, not at module level: test_api_e2e must be the first to
        # import the API (it reads CONFIG_PATH at import time) when both run
        # in one process.
        from fastapi.testclient import TestClient
        import api.main as api_main
        from src.ingest import registry as ing_reg

        self.api, self.reg = api_main, ing_reg
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.raw = root / "data"
        (self.raw / "processed").mkdir(parents=True)
        group = self.raw / "Quy-che"
        group.mkdir()
        self.a = make_pdf(group / "a-quy-che.pdf", ["Điều 1. Phạm vi điều chỉnh"])
        self.b = make_pdf(group / "b-quy-dinh.pdf", ["Điều 1. Đối tượng áp dụng", "Điều 2. Giải thích từ ngữ"])
        (self.raw / "groups.json").write_text(json.dumps(
            {"Quy-che": {"label": "Quy chế", "doc_type": "regulation", "issuing_body": "Trường"}},
            ensure_ascii=False), encoding="utf-8")
        cfg = root / "config.yaml"
        cfg.write_text(
            f"data:\n  raw_dir: {self.raw.as_posix()}\n  processed_dir: {(self.raw / 'processed').as_posix()}\n",
            encoding="utf-8")
        self.cfg_dict = {"data": {"raw_dir": self.raw.as_posix(), "processed_dir": (self.raw / "processed").as_posix()}}
        runner = SimpleNamespace(store=SimpleNamespace(active=lambda: []))
        for patcher in (
            mock.patch.object(self.api, "CONFIG_PATH", str(cfg)),
            mock.patch.object(self.api, "_get_runner", lambda: runner),
            mock.patch.dict(self.api._catalog_cache, {"key": None, "data": {}}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(api_main.app)  # no context manager: startup hooks stay off

    def write_catalog(self, obj):
        p = self.raw / "catalog.json"
        p.write_text(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return p

    def docs(self):
        r = self.client.get("/documents")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        return {d["source"]: d for d in body["documents"]}, body["summary"]

    def test_no_file_adds_only_an_empty_field(self):
        rows, summary = self.docs()
        expected = {r["source"]: r for r in self.reg.build_registry(self.cfg_dict)}
        self.assertEqual(set(rows), {"a-quy-che.pdf", "b-quy-dinh.pdf"})
        for src, row in rows.items():
            self.assertIsNone(row.pop("catalog"))
            self.assertEqual(row.pop("group_label"), "Quy chế")
            self.assertEqual(row, expected[src])
        self.assertEqual(summary["total"], 2)

    def test_entry_is_applied_to_its_file_only(self):
        self.write_catalog({"version": 1, "documents": {"a-quy-che.pdf": {
            "title": "  Quy chế công tác sinh viên ", "doc_number": "967/QĐ-ĐHQT", "year": 2022,
            "issued": "2022-12-26", "issuing_body": "Trường Đại học Quốc tế", "title_en": "Student Affairs Regulation",
            "size": self.a.stat().st_size, "note": "internal", "unknown": "x"}}})
        rows, _ = self.docs()
        self.assertEqual(rows["a-quy-che.pdf"]["catalog"], {
            "title": "Quy chế công tác sinh viên", "doc_number": "967/QĐ-ĐHQT", "year": 2022,
            "issued": "2022-12-26", "issuing_body": "Trường Đại học Quốc tế", "title_en": "Student Affairs Regulation"})
        self.assertIsNone(rows["b-quy-dinh.pdf"]["catalog"])
        # the registry's own fields are untouched
        self.assertEqual(rows["a-quy-che.pdf"]["title"], "")
        self.assertEqual(rows["a-quy-che.pdf"]["doc_number"], "")

    def test_entry_for_another_file_of_the_same_name_is_ignored(self):
        self.write_catalog({"documents": {"a-quy-che.pdf": {"title": "Old", "size": self.a.stat().st_size + 1}}})
        rows, _ = self.docs()
        self.assertIsNone(rows["a-quy-che.pdf"]["catalog"])

    def test_entry_without_size_applies(self):
        self.write_catalog({"documents": {"b-quy-dinh.pdf": {"title": "Quy định"}}})
        rows, _ = self.docs()
        self.assertEqual(rows["b-quy-dinh.pdf"]["catalog"], {"title": "Quy định"})

    def test_empty_number_is_kept_to_hide_a_wrong_extraction(self):
        self.write_catalog({"documents": {"a-quy-che.pdf": {"doc_number": ""}}})
        rows, _ = self.docs()
        self.assertEqual(rows["a-quy-che.pdf"]["catalog"], {"doc_number": ""})

    def test_invalid_values_are_dropped_one_by_one(self):
        self.write_catalog({"documents": {"a-quy-che.pdf": {
            "title": "x" * 301, "doc_number": 12, "year": "2019", "issued": "5/4/2016", "issuing_body": "Bộ"}}})
        rows, _ = self.docs()
        self.assertEqual(rows["a-quy-che.pdf"]["catalog"], {"issuing_body": "Bộ"})
        for year in (True, 1850, 2150, 2019.0):
            self.api._catalog_cache.update(key=None, data={})
            self.write_catalog({"documents": {"a-quy-che.pdf": {"year": year}}})
            rows, _ = self.docs()
            self.assertIsNone(rows["a-quy-che.pdf"]["catalog"], year)

    def test_broken_or_malformed_file_is_ignored(self):
        for content in ("{not json", json.dumps({"documents": []}), json.dumps([1, 2]),
                        json.dumps({"documents": {"a-quy-che.pdf": "title"}})):
            self.api._catalog_cache.update(key=None, data={})
            self.write_catalog(content)
            with mock.patch("builtins.print"):
                rows, summary = self.docs()
            self.assertTrue(all(r["catalog"] is None for r in rows.values()), content)
            self.assertEqual(summary["total"], 2)

    def test_edits_are_picked_up_without_restart(self):
        p = self.write_catalog({"documents": {"a-quy-che.pdf": {"title": "Một"}}})
        self.assertEqual(self.docs()[0]["a-quy-che.pdf"]["catalog"], {"title": "Một"})
        self.write_catalog({"documents": {"a-quy-che.pdf": {"title": "Hai"}}})
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        self.assertEqual(self.docs()[0]["a-quy-che.pdf"]["catalog"], {"title": "Hai"})

    def test_other_endpoints_ignore_the_file(self):
        self.write_catalog({"documents": {"a-quy-che.pdf": {"title": "T"}}})
        groups = self.client.get("/groups").json()["groups"]
        self.assertEqual([g["id"] for g in groups], ["Quy-che"])


class ShippedCatalogTest(unittest.TestCase):
    """data/catalog.json in the repository describes the shipped documents."""

    def test_every_entry_matches_a_document(self):
        path = ROOT / "data" / "catalog.json"
        if not path.exists():
            self.skipTest("no data/catalog.json (e.g. a data volume created before it existed)")
        from api.main import _catalog_entry

        entries = json.loads(path.read_text(encoding="utf-8"))["documents"]
        files = {p.name: p for p in (ROOT / "data").rglob("*")
                 if p.suffix.lower() in (".pdf", ".docx") and "processed" not in p.parts}
        for source, entry in entries.items():
            with self.subTest(source=source):
                self.assertIn(source, files)
                self.assertEqual(entry.get("size"), files[source].stat().st_size)
                shown = _catalog_entry(entry, files[source].stat().st_size)
                self.assertEqual(set(shown), DISPLAY_KEYS & set(entry))
                if "issued" in entry and "year" in entry:
                    self.assertEqual(int(entry["issued"][:4]), entry["year"])


if __name__ == "__main__":
    unittest.main()
