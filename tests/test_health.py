"""/health reports whether Qdrant answers and holds the configured collection.

The UI's status bar reads the `qdrant` field; before it existed the bar always
said "Qdrant offline". The probe must not load the RAG chain, and the
existing fields keep their names and types.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._util import ROOT  # noqa: F401


class FakeQdrant:
    instances: list = []
    exists = True
    fail = False

    def __init__(self, url=None, api_key=None, timeout=None, check_compatibility=True):
        self.url, self.timeout, self.closed = url, timeout, False
        FakeQdrant.instances.append(self)

    def collection_exists(self, name):
        if FakeQdrant.fail:
            raise ConnectionError("connection refused")
        return FakeQdrant.exists and name == "health_probe_test"

    def close(self):
        self.closed = True


class HealthTest(unittest.TestCase):
    def setUp(self):
        # Imported here, not at module level: test_api_e2e must be the first to
        # import the API (it reads CONFIG_PATH at import time) when both run
        # in one process.
        from fastapi.testclient import TestClient
        import api.main as api_main

        self.api = api_main
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Path(self.tmp.name) / "config.yaml"
        cfg.write_text(
            "vector_store:\n"
            "  qdrant_url: http://qdrant.invalid:6333\n"
            "  qdrant_api_key: ''\n"
            "  collection_name: health_probe_test\n",
            encoding="utf-8",
        )
        FakeQdrant.instances, FakeQdrant.exists, FakeQdrant.fail = [], True, False
        for patcher in (
            mock.patch.object(self.api, "CONFIG_PATH", str(cfg)),
            mock.patch.object(self.api, "QdrantClient", FakeQdrant),
            mock.patch.dict(self.api._qdrant_probe, {"at": float("-inf"), "ok": False}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(api_main.app)  # no context manager: startup hooks stay off

    def get(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_online_when_collection_exists(self):
        self.assertIs(self.get()["qdrant"], True)
        probe = FakeQdrant.instances[0]
        self.assertEqual(probe.url, "http://qdrant.invalid:6333")
        self.assertLessEqual(probe.timeout, 3)  # the UI gives up after 3.5 s
        self.assertTrue(probe.closed)

    def test_offline_when_collection_missing(self):
        FakeQdrant.exists = False
        self.assertIs(self.get()["qdrant"], False)

    def test_offline_when_unreachable(self):
        FakeQdrant.fail = True
        self.assertIs(self.get()["qdrant"], False)

    def test_offline_when_config_unreadable(self):
        with mock.patch.object(self.api, "CONFIG_PATH", str(Path(self.tmp.name) / "missing.yaml")):
            self.assertIs(self.get()["qdrant"], False)

    def test_probe_is_cached_briefly(self):
        self.assertIs(self.get()["qdrant"], True)
        FakeQdrant.exists = False
        self.assertIs(self.get()["qdrant"], True)
        self.assertEqual(len(FakeQdrant.instances), 1)
        self.api._qdrant_probe["at"] -= self.api._QDRANT_PROBE_TTL_S + 1
        self.assertIs(self.get()["qdrant"], False)
        self.assertEqual(len(FakeQdrant.instances), 2)

    def test_existing_fields_unchanged(self):
        body = self.get()
        self.assertEqual(body["status"], "ok")
        self.assertIsInstance(body["ready"], bool)
        self.assertIsInstance(body["timestamp"], float)
        self.assertEqual(set(body), {"status", "ready", "qdrant", "timestamp"})

    def test_probe_does_not_load_the_chain(self):
        with mock.patch.object(self.api, "get_chain", side_effect=AssertionError("chain loaded")):
            self.get()
        self.assertIsNone(self.api._chain)


if __name__ == "__main__":
    unittest.main()
