"""自有 Python 代码的全仓静态检查。"""

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PythonLintTests(unittest.TestCase):
    def test_owned_python_passes_correctness_lint(self):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", ".", "--output-format=concise"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
