import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import goya_index


class PortableIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="goya-index-pack-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source with spaces"
        self.source.mkdir()
        self.file = self.source / "inventory.py"
        self.file.write_text("def calculate_stock_level():\n    return 42\n")
        (self.source / "README.md").write_text("Inventory workflow for a synthetic test.\n")
        self.data = self.root / "catalog with spaces"
        self.before = self.digest()

    def digest(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir() if p.is_file() and not p.is_symlink()}

    def test_arbitrary_folder_build_search_and_verified_read(self):
        result = goya_index.build(self.source, self.data)
        self.assertEqual(result["authority_granted"], "none")
        engine, _, _ = goya_index.engines()
        catalog = self.data / "catalog.sqlite3"
        rows = engine.unified_search("calculate_stock_level", catalog=catalog)
        self.assertEqual(rows[0]["source_path"], "inventory.py")
        read = engine.read_verified_source(catalog, "inventory.py")
        self.assertTrue(read["verified"])
        self.assertEqual(read["content_sha256"], self.before["inventory.py"])
        self.assertEqual(self.digest(), self.before)
        with closing(sqlite3.connect(catalog)) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            for table in ("capabilities", "memories", "temporal_events", "continuity_state"):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_stale_read_refused_then_sync_refreshes(self):
        goya_index.build(self.source, self.data)
        engine, _, _ = goya_index.engines()
        catalog = self.data / "catalog.sqlite3"
        self.file.write_text("def calculate_stock_level():\n    return 42000\n")
        self.assertFalse(engine.read_verified_source(catalog, "inventory.py")["verified"])
        result = engine.incremental_sync(catalog)
        self.assertEqual(result["source_writes"], 0)
        self.assertIn("42000", engine.read_verified_source(catalog, "inventory.py")["content"])

    def test_read_connections_close_on_normal_and_exceptional_exit(self):
        goya_index.build(self.source, self.data)
        engine, metadata, lexical = goya_index.engines()
        for factory, name in ((engine._ro, "catalog.sqlite3"), (metadata._connect, "metadata.sqlite3"), (lexical._connect_readonly, "lexical.sqlite3")):
            with factory(self.data / name) as db:
                self.assertEqual(db.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with factory(self.data / name) as db:
                    raise RuntimeError("synthetic")
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")

    def test_existing_data_and_in_source_output_refused(self):
        with self.assertRaises(ValueError):
            goya_index.build(self.source, self.source / "catalog")
        goya_index.build(self.source, self.data)
        with self.assertRaises(ValueError):
            goya_index.build(self.source, self.data)

    def test_external_symlink_is_not_indexed(self):
        outside = self.root / "outside.txt"
        outside.write_text("external confidential text")
        (self.source / "link.txt").symlink_to(outside)
        goya_index.build(self.source, self.data)
        engine, _, _ = goya_index.engines()
        self.assertFalse(engine.read_verified_source(self.data / "catalog.sqlite3", "link.txt")["verified"])

    def test_fresh_process_uses_only_explicit_catalog(self):
        goya_index.build(self.source, self.data)
        environment = dict(os.environ)
        # The child changes cwd. Bind to the package under test, not a relative
        # PYTHONPATH that changes meaning. Installed-wheel tests bind site-packages.
        environment["PYTHONPATH"] = str(Path(goya_index.__file__).resolve().parent.parent)
        response = subprocess.run([sys.executable, "-B", "-m", "goya_index", "search", "--data", str(self.data), "--query", "calculate_stock_level"], capture_output=True, text=True, check=True, cwd=self.root, env=environment)
        self.assertEqual(json.loads(response.stdout)[0]["source_path"], "inventory.py")
        self.assertEqual(self.digest(), self.before)


if __name__ == "__main__":
    unittest.main()
