import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests._util import ROOT  # noqa: F401
from src.ingest.jobs import JobRunner, JobStore
from src.ingest.registry import load_sidecar, save_sidecar


def script(tmp: Path, name: str, body: str) -> Path:
    p = tmp / name
    p.write_text(body, encoding="utf-8")
    return p


FINISH = """import json, sys
path = sys.argv[1]
job = json.load(open(path, encoding="utf-8"))
job["status"] = "succeeded"
job["results"] = {"x.pdf": {"status": "indexed"}}
print("Tiếng Việt → ok")          # must not crash: runner forces UTF-8
json.dump(job, open(path, "w", encoding="utf-8"))
"""
CRASH = "import sys\nprint('boom')\nsys.exit(3)\n"
HANG = "import time\ntime.sleep(30)\n"


class JobStoreTest(unittest.TestCase):
    def test_lifecycle_and_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            store = JobStore(Path(d))
            a = store.create("ingest", {"items": [{"source": "a.pdf"}]})
            time.sleep(0.01)
            b = store.create("remove", {"items": [{"source": "b.pdf"}]})
            self.assertEqual(store.next_queued()["id"], a["id"])
            self.assertEqual([j["id"] for j in store.active()], [a["id"], b["id"]])
            a["status"] = "running"
            store.save(a)
            self.assertEqual(store.recover(), [a["id"]])            # left running by a dead process
            self.assertEqual(store.get(a["id"])["status"], "queued")
            self.assertIsNone(store.get("../../etc"))
            for job in (a, b):
                job = store.get(job["id"])
                job["status"] = "succeeded"
                store.save(job)
            store.prune(keep=1)
            self.assertEqual(len(store.list()), 1)


class JobRunnerTest(unittest.TestCase):
    def run_one(self, body: str, timeout: float = 60, with_doc: bool = False):
        tmp = tempfile.TemporaryDirectory()
        d = Path(tmp.name)
        self.addCleanup(tmp.cleanup)
        store = JobStore(d / "jobs")
        raw = d / "data"
        (raw / "G").mkdir(parents=True)
        doc = raw / "G" / "x.pdf"
        doc.write_bytes(b"%PDF")
        save_sidecar(doc, {"status": "queued"})
        worker = script(d, "w.py", body)
        finished = threading.Event()
        seen = {}
        runner = JobRunner(store, "unused.yaml", on_finished=lambda j: (seen.update(j), finished.set()),
                           timeout_s=timeout, command=lambda jp: [sys.executable, str(worker), str(jp)],
                           raw_dir=raw)
        runner.start()
        job = runner.enqueue("ingest", {"items": [{"source": "x.pdf", "group": "G"}]})
        self.assertTrue(finished.wait(90), "runner did not finish the job")
        runner.stop()
        return store.get(job["id"]), seen, doc

    def test_successful_worker(self):
        job, seen, _ = self.run_one(FINISH)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(seen["id"], job["id"])
        self.assertIn("Tiếng Việt → ok", "\n".join(job["log_tail"]))

    def test_crashed_worker_fails_the_job_and_its_documents(self):
        job, _, doc = self.run_one(CRASH)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"]["code"], "WORKER_CRASHED")
        self.assertEqual(load_sidecar(doc)["status"], "failed")

    def test_timeout(self):
        job, _, _ = self.run_one(HANG, timeout=1)
        self.assertEqual(job["error"]["code"], "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
