"""Verify the shareable, provider-free examples against the active package."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import goya_index


ROOT = Path(__file__).resolve().parents[1]


class DocumentedExamplesTests(unittest.TestCase):
    def run_script(self, path, *args):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(goya_index.__file__).resolve().parent.parent)
        return subprocess.run([sys.executable, "-B", str(ROOT / path), *args],
                              capture_output=True, text=True, env=environment)

    def test_context_example(self):
        result = self.run_script("examples/prepare_context.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["source_paths"][0], "inventory.py")
        self.assertFalse(data["model_called"])
        self.assertLessEqual(data["packet_chars"], 2000)

    def test_benchmark_and_create_only_result(self):
        with tempfile.TemporaryDirectory(prefix="goya-index-bench-test-") as temporary:
            output = Path(temporary) / "result.json"
            result = self.run_script("tools/benchmark.py", "--files", "8", "--queries", "5", "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertTrue(data["passed"])
            self.assertEqual(data["top1_correct"], 5)
            self.assertEqual(data["verified_reads"], 5)
            self.assertEqual(data["verified_packets"], 5)
            self.assertTrue(data["source_unchanged"])
            self.assertEqual(data, json.loads(output.read_text()))
            before = output.read_bytes()
            refused = self.run_script("tools/benchmark.py", "--output", str(output))
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(output.read_bytes(), before)

    def test_benchmark_parameter_bounds(self):
        result = self.run_script("tools/benchmark.py", "--files", "2001")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
