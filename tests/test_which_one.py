import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import goya_index

COPIES = ("alpha", "beta", "gamma", "delta")


class WhichOneTests(unittest.TestCase):
    """Synthetic look-alikes: four scoring.py in different folders and one unique file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="goya-index-which-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source with spaces"
        for name in COPIES:
            (self.source / name).mkdir(parents=True)
            (self.source / name / "scoring.py").write_text(f"def score_{name}(x):\n    return x\n")
        (self.source / "inventory.py").write_text("def calculate_stock_level():\n    return 42\n")
        self.data = self.root / "private catalog"
        goya_index.build(self.source, self.data)

    def digest(self):
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.rglob("*") if p.is_file()}

    def test_vague_question_asks_which_copy(self):
        before = self.digest()
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            result = goya_index.which_one(self.data, "the scoring module")
        self.assertEqual(result["action"], "ask")
        self.assertEqual(result["groups"], [{"pattern": "*/scoring.py", "count": 4}])
        self.assertEqual(sorted(result["shown"]), sorted(f"{n}/scoring.py" for n in COPIES))
        self.assertIn("4 files look like */scoring.py. Which one?", result["message"])
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["rule"], "which-one/2")
        self.assertEqual(self.digest(), before)

    def test_specific_questions_answer(self):
        result = goya_index.which_one(self.data, "Where is score_beta defined?")
        self.assertEqual((result["action"], result["path"]), ("answer", "beta/scoring.py"))
        result = goya_index.which_one(self.data, "the gamma scoring module")
        self.assertEqual((result["action"], result["path"]), ("answer", "gamma/scoring.py"))
        result = goya_index.which_one(self.data, "calculate_stock_level")
        self.assertEqual((result["action"], result["path"]), ("answer", "inventory.py"))

    def test_folder_word_answers_even_when_search_ranks_another_file_first(self):
        source = self.root / "same text"
        for name in COPIES:
            (source / name).mkdir(parents=True)
            (source / name / "scoring.py").write_text("def score(x):\n    return x\n")
        (source / "scoring_module_notes.md").write_text(
            "The scoring module notes.\nEach scoring module returns a number. The scoring module in each folder differs.\n")
        data = self.root / "same text catalog"
        goya_index.build(source, data)
        result = goya_index.which_one(data, "the scoring module in gamma")
        self.assertEqual(result["results"][0], "scoring_module_notes.md")
        self.assertEqual((result["action"], result["path"]), ("answer", "gamma/scoring.py"))
        self.assertEqual(result["rule"], "which-one/2")

    def test_groups_follow_sync(self):
        epsilon = self.source / "epsilon"
        epsilon.mkdir()
        (epsilon / "scoring.py").write_text("def score_epsilon(x):\n    return x\n")
        shutil.rmtree(self.source / "beta")
        (self.source / "gamma" / "scoring.py").write_text("def score_omega(x):\n    return -x\n")
        goya_index.sync(self.data)
        result = goya_index.which_one(self.data, "the scoring module")
        self.assertEqual(result["groups"], [{"pattern": "*/scoring.py", "count": 4}])
        self.assertNotIn("beta/scoring.py", result["shown"])
        self.assertIn("epsilon/scoring.py", result["shown"])
        result = goya_index.which_one(self.data, "Where is score_omega defined?")
        self.assertEqual((result["action"], result["path"]), ("answer", "gamma/scoring.py"))

    def test_cli_which_and_empty_query(self):
        result = subprocess.run([sys.executable, "-B", "-m", "goya_index", "which", "--data", str(self.data),
                                 "--query", "the scoring module"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["action"], "ask")
        with self.assertRaises(ValueError):
            goya_index.which_one(self.data, "  ")


if __name__ == "__main__":
    unittest.main()
