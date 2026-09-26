"""End-to-end: the real API, the real worker subprocess, a real Qdrant and the
real embedding model — on a private copy of the data and a throw-away
collection, so the live corpus and `reg_chunks` are never touched.

Skipped unless ARRS_E2E=1 (needs Qdrant running and takes a few minutes):

    set ARRS_E2E=1 && venv\\Scripts\\python -m unittest tests.test_api_e2e -v

Run it in its own process: the API module reads CONFIG_PATH at import time.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from tests._util import ROOT, make_docx, make_pdf

E2E = os.environ.get("ARRS_E2E") == "1"

LEGAL = [
    "Điều 1. Phạm vi điều chỉnh",
    "Quy định này quy định về chế độ công tác phí cho nhân viên đi công tác trong nước và nước ngoài.",
    "Điều 2. Đối tượng áp dụng",
    "Quy định áp dụng cho nhân viên chính thức, cộng tác viên và thực tập sinh đi công tác theo quyết định cử đi.",
    "Điều 3. Mức phụ cấp lưu trú",
    "Nhân viên đi công tác được hưởng phụ cấp lưu trú năm trăm nghìn đồng mỗi ngày kể từ ngày bắt đầu chuyến đi.",
]
LEGAL_V2 = [line.replace("năm trăm nghìn", "sáu trăm nghìn") for line in LEGAL]
ENGLISH = [
    "Chapter 1. Remote work policy\n1. Employees may work remotely up to three days per week when their "
    "manager approves a written remote work plan submitted in advance.",
    "Chapter 2. Equipment\n1. The company provides a laptop and reimburses internet costs up to fifty "
    "dollars per month for employees who work remotely on a regular basis.",
    "Chapter 3. Security\n1. Confidential documents must never be printed at home and every device must "
    "use full disk encryption and a screen lock after five minutes of inactivity.",
]


@unittest.skipUnless(E2E, "set ARRS_E2E=1 to run the end-to-end ingestion test")
class IngestionE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import yaml

        cls.tmp = tempfile.TemporaryDirectory(prefix="arrs_e2e_")
        base = Path(cls.tmp.name)
        cls.raw = base / "data"
        (cls.raw / "Luat-quoc-gia").mkdir(parents=True)
        legacy = sorted((ROOT / "data" / "Luat-quoc-gia").glob("*.pdf"), key=lambda p: p.stat().st_size)[0]
        cls.legacy = shutil.copy2(legacy, cls.raw / "Luat-quoc-gia" / legacy.name)
        shutil.copy2(ROOT / "data" / "groups.json", cls.raw / "groups.json")
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        cfg["data"] = {"raw_dir": cls.raw.as_posix(), "processed_dir": (cls.raw / "processed").as_posix()}
        cfg["bm25"]["index_path"] = (cls.raw / "processed" / "bm25.pkl").as_posix()
        cls.collection = f"reg_chunks_e2e_{uuid.uuid4().hex[:8]}"
        cfg["vector_store"]["collection_name"] = cls.collection
        cls.cfg_path = base / "config.e2e.yaml"
        cls.cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.environ["CONFIG_PATH"] = str(cls.cfg_path)

        from fastapi.testclient import TestClient
        import api.main as api_main

        assert api_main.CONFIG_PATH == str(cls.cfg_path), "run this test module in its own process"
        cls.api = api_main
        cls.client = TestClient(api_main.app)
        api_main._get_runner().start()
        cls.files = base / "uploads"
        (cls.files / "v2").mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        try:
            from src.config import load_config
            import importlib
            emb = importlib.import_module("src.pipeline.02_embed_index")
            emb.get_qdrant_client(load_config(str(cls.cfg_path))).delete_collection(cls.collection)
        finally:
            cls.api._get_runner().stop()
            time.sleep(1)
            cls.tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────
    def wait(self, job_id, timeout=900):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.client.get(f"/jobs/{job_id}").json()
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(1)
        self.fail(f"job {job_id} did not finish")

    def docs(self):
        return {d["source"]: d for d in self.client.get("/documents").json()["documents"]}

    def health(self):
        h = self.client.get("/index/health").json()
        self.assertTrue(h["ok"], json.dumps(h, ensure_ascii=False)[:800])
        return h

    def upload(self, paths, group):
        files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
        r = self.client.post("/uploads", files=files, data={"group": group})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def commit(self, upload, group, actions=None, languages=None):
        actions, languages = actions or {}, languages or {}
        body = {"group": group, "index_now": True, "files": [
            {"file_key": f["file_key"], "action": actions.get(f["safe_name"], "add"),
             "language": languages.get(f["safe_name"])}
            for f in upload["files"] if f["status"] == "ok"]}
        r = self.client.post(f"/uploads/{upload['upload_id']}/commit", json=body)
        self.assertEqual(r.status_code, 202, r.text)
        return r.json()

    def search(self, query, mode="sparse"):
        return self.client.get("/search", params={"query": query, "mode": mode, "top_k": 5}).json()["results"]

    # ── the scenario ─────────────────────────────────────────────────────
    def test_full_lifecycle(self):
        # 0. index the copied legacy document (sync on an empty collection)
        job = self.client.post("/reindex", json={"mode": "sync"}).json()
        self.assertEqual(self.wait(job["job_id"])["status"], "succeeded")
        self.health()
        self.assertIs(self.client.get("/health").json()["qdrant"], True)  # the status bar's signal
        legacy_chunks = self.docs()[self.legacy.name]["chunk_count"]
        self.assertGreater(legacy_chunks, 0)
        legacy_jsonl = (self.raw / "processed" / (self.legacy.stem + ".jsonl")).read_bytes()

        # 1. check: a DOCX, an English PDF, a duplicate, an unsupported file, a name conflict
        docx_path = make_docx(self.files / "Quy định công tác phí.docx",
                              lambda d: [d.add_paragraph(t) for t in LEGAL])
        en_path = make_pdf(self.files / "Remote Work Policy.pdf", ENGLISH)
        dup = self.files / "copy.pdf"
        shutil.copy2(self.legacy, dup)
        txt = self.files / "notes.txt"
        txt.write_text("hello", encoding="utf-8")
        clash = make_pdf(self.files / self.legacy.name, ["Nội dung khác hẳn với văn bản gốc."])
        up = self.upload([docx_path, en_path, dup, txt, clash], "Luat-quoc-gia")
        by_name = {f["original_name"]: f for f in up["files"]}
        self.assertEqual(by_name["copy.pdf"]["reason_code"], "DUPLICATE_CONTENT")
        self.assertEqual(by_name["notes.txt"]["reason_code"], "UNSUPPORTED_TYPE")
        self.assertEqual(by_name[self.legacy.name]["conflict"]["type"], "same_group")
        self.assertEqual(by_name["Remote Work Policy.pdf"]["language_suggestion"], "en")
        self.assertEqual(by_name["Quy định công tác phí.docx"]["safe_name"], "Quy-định-công-tác-phí.docx")

        # 2. commit (the clash is kept under a new name), index in the background
        new_group = {"new": {"label": "Chính sách nội bộ", "doc_type": "Chính sách", "issuing_body": "Phòng Nhân sự"}}
        res = self.commit(up, new_group, actions={self.legacy.name: "rename"})
        self.assertEqual(res["group"], "Chinh-sach-noi-bo")
        renamed = [d for d in res["documents"] if d.get("renamed")]
        self.assertEqual(len(renamed), 1)
        job = self.wait(res["job_id"])
        self.assertEqual(job["status"], "succeeded", json.dumps(job, ensure_ascii=False)[:1500])
        docs = self.docs()
        for name in ("Quy-định-công-tác-phí.docx", "Remote-Work-Policy.pdf", renamed[0]["source"]):
            self.assertEqual(docs[name]["status"], "indexed", name)
        self.assertEqual(docs["Remote-Work-Policy.pdf"]["language"], "en")
        self.assertEqual(docs["Quy-định-công-tác-phí.docx"]["doc_type"], "Chính sách")
        self.health()
        # the original document was not touched by any of this
        self.assertEqual((self.raw / "processed" / (self.legacy.stem + ".jsonl")).read_bytes(), legacy_jsonl)

        # 3. searchable right away (BM25 and dense)
        hits = self.search("remote work laptop internet")
        self.assertEqual(hits[0]["source"], "Remote-Work-Policy.pdf")
        self.assertEqual(hits[0]["file_type"], "pdf")
        hits = self.search("phụ cấp lưu trú công tác", mode="dense")
        self.assertIn("Quy-định-công-tác-phí.docx", [h["source"] for h in hits])

        # 4. replace the DOCX with a new version
        v2 = make_docx(self.files / "v2" / "Quy định công tác phí.docx",
                       lambda d: [d.add_paragraph(t) for t in LEGAL_V2])
        up = self.upload([v2], "Chinh-sach-noi-bo")
        self.assertEqual(up["files"][0]["conflict"]["type"], "same_group")
        res = self.commit(up, {"id": "Chinh-sach-noi-bo"}, actions={"Quy-định-công-tác-phí.docx": "replace"})
        self.assertEqual(self.wait(res["job_id"])["status"], "succeeded")
        texts = " ".join(h["text"] for h in self.search("phụ cấp lưu trú"))
        self.assertIn("sáu trăm nghìn", texts)
        self.assertNotIn("năm trăm nghìn", texts)
        self.assertFalse(list((self.raw / "Chinh-sach-noi-bo").glob("*.prev")))
        self.health()

        # 5. a replacement that yields no text is rolled back to the old version
        blank = make_pdf(self.files / "v2" / "Remote Work Policy.pdf", ["", ""])
        up = self.upload([blank], "Chinh-sach-noi-bo")
        res = self.commit(up, {"id": "Chinh-sach-noi-bo"}, actions={"Remote-Work-Policy.pdf": "replace"})
        job = self.wait(res["job_id"])
        self.assertEqual(job["results"]["Remote-Work-Policy.pdf"]["status"], "empty")
        self.assertIn("giữ nguyên phiên bản cũ", job["results"]["Remote-Work-Policy.pdf"]["message"])
        self.assertEqual(self.search("remote work laptop internet")[0]["source"], "Remote-Work-Policy.pdf")
        self.assertEqual(self.docs()["Remote-Work-Policy.pdf"]["status"], "indexed")
        self.health()

        # 6. delete a document: gone from every index
        r = self.client.delete("/documents/Remote-Work-Policy.pdf").json()
        self.assertFalse(r["reindex_required"])
        self.wait(r["job_id"])
        self.assertNotIn("Remote-Work-Policy.pdf", self.docs())
        self.assertNotIn("Remote-Work-Policy.pdf", [h["source"] for h in self.search("remote work laptop")])
        self.health()

        # 7. legacy one-shot endpoint still works (and no longer overwrites silently)
        extra = make_docx(self.files / "Phụ lục.docx", lambda d: d.add_paragraph("Phụ lục mẫu đơn đề nghị thanh toán công tác phí dành cho nhân viên các phòng ban."))
        r = self.client.post("/upload-docs", files=[("files", (extra.name, extra.read_bytes(), "application/octet-stream"))],
                             data={"group": "Chinh-sach-noi-bo"}).json()
        self.assertEqual(r["saved_count"], 1)
        self.assertEqual(self.wait(r["job_id"])["status"], "succeeded")

        # 8. an orphan left in the index can be cleaned up
        orphan = self.raw / "processed" / "orphan-doc.jsonl"
        rec = json.loads((self.raw / "processed" / (self.legacy.stem + ".jsonl")).read_text(encoding="utf-8").splitlines()[0])
        rec.update(source="orphan-doc.pdf", chunk_id="00000000000000aa")
        orphan.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(self.docs()["orphan-doc.pdf"]["status"], "orphan")
        self.wait(self.client.delete("/documents/orphan-doc.pdf").json()["job_id"])
        self.assertNotIn("orphan-doc.pdf", self.docs())

        # 9. delete the whole group
        r = self.client.delete("/groups/Chinh-sach-noi-bo").json()
        self.assertEqual(r["removed_files"], 3)
        self.wait(r["job_id"])
        self.assertEqual(set(self.docs()), {self.legacy.name})
        self.health()

        # 10. /groups hides internal folders and counts every supported type
        ids = [g["id"] for g in self.client.get("/groups").json()["groups"]]
        self.assertNotIn("processed", ids)
        self.assertFalse(any(i.startswith("_") for i in ids))

        # 11. PUT /config keeps UTF-8 (and never truncates the file)
        text = self.cfg_path.read_text(encoding="utf-8")
        r = self.client.put("/config", json={"config": text})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.cfg_path.read_text(encoding="utf-8"), text)
