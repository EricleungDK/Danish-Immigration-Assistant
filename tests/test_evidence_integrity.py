import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from danish_rag.evidence_integrity import (
    canonical_json_sha256,
    is_utc_seconds,
    reject_duplicate_json_object,
    sha256_file,
    utc_now_seconds,
)


class EvidenceIntegrityTests(unittest.TestCase):
    def test_duplicate_json_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate JSON field: release_id"):
            json.loads(
                '{"release_id":"first","release_id":"second"}',
                object_pairs_hook=reject_duplicate_json_object,
            )

    def test_canonical_json_hash_is_key_order_independent(self):
        self.assertEqual(
            canonical_json_sha256({"b": "æ", "a": 1}),
            canonical_json_sha256({"a": 1, "b": "æ"}),
        )

    def test_file_hash_uses_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "evidence.json"
            payload = b'{"status":"passed"}\n'
            path.write_bytes(payload)

            self.assertEqual(sha256_file(path), hashlib.sha256(payload).hexdigest())

    def test_utc_timestamp_has_second_precision(self):
        self.assertRegex(utc_now_seconds(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertTrue(is_utc_seconds("2026-07-14T18:54:09Z"))
        self.assertFalse(is_utc_seconds("2026-07-14T18:54:09.123Z"))
        self.assertFalse(is_utc_seconds("yesterday"))


if __name__ == "__main__":
    unittest.main()
