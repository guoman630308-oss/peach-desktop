"""事务、图片复验和 HTTP 入口的行为验证。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from peach.redirect import create_redirect_app
from peach.routes_media import _image_response
from peach.web_state import WebContract
from peach.config import PeachSettings
from peach.health import database_status, readiness
from support.ledger import fresh_ledger


class RuntimeConsistencyTests(unittest.TestCase):
    def test_readiness_distinguishes_missing_database_and_pending_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / "ledger.db"
            settings = PeachSettings(db_path=path, configured=True)
            self.assertFalse(readiness(settings)["ready"])
            self.assertFalse(path.exists())
            self.assertEqual(database_status(path), "missing")
            path.touch()
            self.assertEqual(database_status(path), "empty")
            fresh_ledger(root)
            self.assertEqual(database_status(path), "available")
            self.assertTrue(readiness(settings)["ready"])
            connection = sqlite3.connect(path)
            try:
                connection.execute("DELETE FROM schema_migration WHERE version=(SELECT max(version) FROM schema_migration)")
                connection.commit()
            finally:
                connection.close()
            result = readiness(settings)
            self.assertTrue(result["checks"]["database"])
            self.assertFalse(result["checks"]["schema"])

    def test_commit_invalidates_values_read_during_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "test.db"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE sample(value INTEGER)")
                connection.execute("INSERT INTO sample VALUES(0)")
            connection.close()
            contract = WebContract(path)

            def read():
                with contract.read_connection() as connection:
                    return connection.execute("SELECT value FROM sample").fetchone()[0]

            with contract.write_transaction() as connection:
                contract.cache_bust()
                connection.execute("UPDATE sample SET value=1")
                self.assertEqual(contract.cached("sample", read), 0)
            self.assertEqual(contract.cached("sample", read), 1)
            generation = contract.cache_generation
            with self.assertRaises(ValueError):
                with contract.write_transaction() as connection:
                    connection.execute("UPDATE sample SET value=2")
                    raise ValueError("rollback")
            self.assertEqual(contract.cache_generation, generation)
            self.assertEqual(read(), 1)

    def test_aggregate_cache_is_bounded_and_keeps_recent_hits(self):
        contract = WebContract(Path("unused.db"))
        for key in range(192):
            contract.cached(str(key), lambda key=key: key)
        contract.cached("0", lambda: -1)
        contract.cached("new", lambda: 999)
        self.assertEqual(len(contract.cache), 192)
        self.assertIn("0", contract.cache)
        self.assertNotIn("1", contract.cache)

    def test_http_navigation_uses_fixed_origin_and_does_not_replay_writes(self):
        with TestClient(create_redirect_app("https://peach.test")) as client:
            health = client.get("/healthz")
            self.assertEqual(health.json()["service"], "peach-redirect")
            response = client.get("/items?q=a&q=b&t=secret", headers={"Host": "evil.test"},
                                  follow_redirects=False)
            self.assertEqual(response.headers["location"], "https://peach.test/items?q=a&q=b")
            self.assertEqual(client.post("/api/action", json={"value": 1}).status_code, 426)

    def test_mutable_image_revalidates_after_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "cover.jpg"
            path.write_bytes(b"first")
            app = FastAPI()

            @app.get("/image")
            def image(request: Request):
                return _image_response(request, path)

            with TestClient(app) as client:
                response = client.get("/image")
                headers = {"If-None-Match": response.headers["etag"]}
                self.assertEqual(client.get("/image", headers=headers).status_code, 304)
                path.write_bytes(b"replacement")
                updated = client.get("/image", headers=headers)
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.content, b"replacement")
                self.assertEqual(updated.headers["cache-control"], "private, no-cache")
