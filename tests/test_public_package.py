import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import goya_index


class PublicPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="goya-index-public-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source with spaces"
        self.source.mkdir()
        self.file = self.source / "inventory.py"
        self.file.write_text("def calculate_stock_level():\n    return 42\n")
        self.data = self.root / "private catalog"

    def test_public_api_and_context_need_no_network(self):
        before = self.file.read_bytes()
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            goya_index.build(self.source, self.data)
            self.assertEqual(goya_index.search(self.data, "calculate_stock_level")[0]["source_path"], "inventory.py")
            self.assertTrue(goya_index.read(self.data, "inventory.py")["verified"])
            packet = goya_index.context(self.data, "calculate_stock_level", max_chars=2000)
            self.assertLessEqual(packet["packet_chars"], 2000)
            self.assertEqual(packet["packet_sha256"], hashlib.sha256(packet["packet"].encode()).hexdigest())
            self.assertEqual(packet["candidates"][0]["source_path"], "inventory.py")
            self.assertEqual(packet["candidates"][0]["content_sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(goya_index.sync(self.data)["source_writes"], 0)
            self.assertIsInstance(goya_index.inventory(self.data), dict)
        self.assertEqual(before, self.file.read_bytes())

    def test_hash_mismatch_refused_even_with_restored_size_and_mtime(self):
        goya_index.build(self.source, self.data)
        before = self.file.stat()
        self.file.write_text("def calculate_stock_level():\n    return 99\n")
        os.utime(self.file, ns=(before.st_atime_ns, before.st_mtime_ns))
        result = goya_index.read(self.data, "inventory.py")
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "content_hash_mismatch")
        self.assertNotIn("content", result)
        self.assertEqual(goya_index.context(self.data, "calculate_stock_level")["candidates"], [])

    def test_sensitive_and_hidden_files_not_returned(self):
        (self.source / "credentials.txt").write_text("invented credential fixture")
        (self.source / ".env").write_text("invented hidden fixture")
        # Construct a fake recognisable token, never a real credential.
        (self.source / "notes.txt").write_text("token: " + "sk-" + "x" * 32)
        goya_index.build(self.source, self.data)
        for name in ("credentials.txt", ".env", "notes.txt"):
            result = goya_index.read(self.data, name)
            self.assertFalse(result["verified"], name)
            self.assertNotIn("content", result)

    def test_replaced_leaf_symlink_is_refused(self):
        goya_index.build(self.source, self.data)
        other = self.root / "outside.py"
        other.write_bytes(self.file.read_bytes())
        st = self.file.stat()
        os.utime(other, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.file.unlink()
        self.file.symlink_to(other)
        result = goya_index.read(self.data, "inventory.py")
        self.assertFalse(result["verified"])
        self.assertNotIn("content", result)

    def test_path_traversal_and_absolute_reads_refused(self):
        goya_index.build(self.source, self.data)
        for path in ("../outside.txt", "/etc/passwd", ""):
            with self.assertRaises(ValueError):
                goya_index.read(self.data, path)

    def test_sync_tracks_additions_and_deletions(self):
        goya_index.build(self.source, self.data)
        self.file.unlink()
        (self.source / "delivery.py").write_text("def delivery_eta():\n    return 3\n")
        goya_index.sync(self.data)
        self.assertFalse(goya_index.read(self.data, "inventory.py")["verified"])
        self.assertTrue(goya_index.read(self.data, "delivery.py")["verified"])
        self.assertEqual(goya_index.search(self.data, "delivery_eta")[0]["source_path"], "delivery.py")

    def test_public_argument_bounds(self):
        for query in ("", "   ", None):
            with self.assertRaises(ValueError):
                goya_index.search(self.data, query)
        for limit in (0, 101, True):
            with self.assertRaises(ValueError):
                goya_index.search(self.data, "stock", limit=limit)
        for budget in (255, 100001, True):
            with self.assertRaises(ValueError):
                goya_index.context(self.data, "stock", max_chars=budget)

    def test_unsupported_platform_refuses_before_data_creation(self):
        with patch.object(sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "POSIX"):
                goya_index.build(self.source, self.data)
        self.assertFalse(self.data.exists())

    def test_semantic_mode_refuses_without_provider_call(self):
        goya_index.build(self.source, self.data)
        catalog, _, _ = goya_index.engines()
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            with self.assertRaisesRegex(ValueError, "semantic_search_not_in_portable_release"):
                catalog.unified_search("stock", catalog=self.data / "catalog.sqlite3", use_semantic=True)

    def test_cli_context_and_error_exit(self):
        goya_index.build(self.source, self.data)
        result = subprocess.run([sys.executable, "-B", "-m", "goya_index", "context", "--data", str(self.data), "--query", "calculate_stock_level", "--max-chars", "2000"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["candidates"])
        result = subprocess.run([sys.executable, "-B", "-m", "goya_index", "read", "--data", str(self.data), "--path", "../outside.py"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
