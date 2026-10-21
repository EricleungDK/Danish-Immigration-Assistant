import subprocess
import sys
import unittest
from pathlib import Path

from danish_rag import privacy_boundary, runtime_policy

ROOT = Path(__file__).resolve().parents[1]


class LoopbackDefinitionTests(unittest.TestCase):
    def test_privacy_boundary_does_not_depend_on_runtime_policy(self):
        code = (
            "import sys, danish_rag.privacy_boundary;"
            "sys.exit(1 if 'danish_rag.runtime_policy' in sys.modules else 0)"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT)
        self.assertEqual(result.returncode, 0)

    def test_runtime_policy_reuses_the_one_loopback_definition(self):
        self.assertIs(runtime_policy.is_loopback_url, privacy_boundary.is_loopback_url)

    def test_loopback_hosts(self):
        for url in ("http://127.0.0.1:11434", "http://localhost:1", "http://[::1]:80", "127.0.0.1"):
            self.assertTrue(privacy_boundary.is_loopback_url(url), url)
        for url in ("http://example.com", "http://10.0.0.1", "http://127.0.0.1.evil.com"):
            self.assertFalse(privacy_boundary.is_loopback_url(url), url)


if __name__ == "__main__":
    unittest.main()
