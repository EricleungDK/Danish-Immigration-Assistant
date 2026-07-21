import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from danish_rag.release_trust import (
    ReleaseTrustError,
    sign_manifest,
    verify_manifest_signature,
)


class ReleaseTrustTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("openssl") is None:
            self.skipTest("OpenSSL is required for release trust tests")

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.private_key_path = self.root / "release-private-key.pem"
        self.public_key_path = self.root / "release-public-key.pem"
        self.manifest_path = self.root / "manifest.json"
        self.signature_path = self.root / "manifest.sig"
        self.trust_root_path = self.root / "trust-root.json"
        self.trust_root_id = "project-maintainer-release-key-v1"

        self._generate_key_pair(self.private_key_path, self.public_key_path)
        self._write_trust_root(self.public_key_path)

    def tearDown(self):
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    def _generate_key_pair(self, private_key_path, public_key_path):
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "Ed25519",
                "-out",
                str(private_key_path),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "openssl",
                "pkey",
                "-in",
                str(private_key_path),
                "-pubout",
                "-out",
                str(public_key_path),
            ],
            check=True,
            capture_output=True,
        )

    def _generate_rsa_key_pair(self, private_key_path, public_key_path):
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:512",
                "-out",
                str(private_key_path),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "openssl",
                "pkey",
                "-in",
                str(private_key_path),
                "-pubout",
                "-out",
                str(public_key_path),
            ],
            check=True,
            capture_output=True,
        )

    def _write_trust_root(self, public_key_path, **overrides):
        trust_root = {
            "schema_version": "1.0",
            "trust_root_id": self.trust_root_id,
            "algorithm": "ed25519",
            "public_key_pem": Path(public_key_path).read_text(encoding="ascii"),
            "status": "active",
        }
        trust_root.update(overrides)
        self.trust_root_path.write_text(
            json.dumps(trust_root, indent=2) + "\n",
            encoding="utf-8",
        )

    def _sign_default_manifest(self):
        self.manifest_path.write_bytes(
            b'{\n  "knowledge_release_id": "kr-test"\n}\n'
        )
        sign_manifest(
            self.manifest_path,
            self.private_key_path,
            self.signature_path,
        )

    def test_valid_detached_signature_verifies_the_exact_manifest_bytes(self):
        manifest_bytes = b'{\n  "knowledge_release_id": "kr-test"\n}\n'
        self.manifest_path.write_bytes(manifest_bytes)

        sign_manifest(
            self.manifest_path,
            self.private_key_path,
            self.signature_path,
        )

        self.assertEqual(len(self.signature_path.read_bytes()), 64)
        self.assertIsNone(
            verify_manifest_signature(
                self.manifest_path,
                self.signature_path,
                self.trust_root_path,
                self.trust_root_id,
            )
        )

    def test_semantically_equivalent_but_byte_different_manifest_is_rejected(self):
        self._sign_default_manifest()
        self.manifest_path.write_bytes(
            b'{"knowledge_release_id":"kr-test"}\n'
        )

        with self.assertRaisesRegex(
            ReleaseTrustError, "signature verification failed"
        ):
            verify_manifest_signature(
                self.manifest_path,
                self.signature_path,
                self.trust_root_path,
                self.trust_root_id,
            )

    def test_tampered_detached_signature_is_rejected(self):
        self._sign_default_manifest()
        signature = bytearray(self.signature_path.read_bytes())
        signature[0] ^= 1
        self.signature_path.write_bytes(signature)

        with self.assertRaisesRegex(
            ReleaseTrustError, "signature verification failed"
        ):
            verify_manifest_signature(
                self.manifest_path,
                self.signature_path,
                self.trust_root_path,
                self.trust_root_id,
            )

    def test_signature_from_a_different_key_is_rejected(self):
        other_private_key_path = self.root / "other-private-key.pem"
        other_public_key_path = self.root / "other-public-key.pem"
        self._generate_key_pair(other_private_key_path, other_public_key_path)
        self.manifest_path.write_bytes(b'{"knowledge_release_id":"kr-test"}\n')
        sign_manifest(
            self.manifest_path,
            other_private_key_path,
            self.signature_path,
        )

        with self.assertRaisesRegex(
            ReleaseTrustError, "signature verification failed"
        ):
            verify_manifest_signature(
                self.manifest_path,
                self.signature_path,
                self.trust_root_path,
                self.trust_root_id,
            )

    def test_actual_key_type_must_be_ed25519_not_only_trust_root_metadata(self):
        rsa_private_key_path = self.root / "rsa-private-key.pem"
        rsa_public_key_path = self.root / "rsa-public-key.pem"
        self._generate_rsa_key_pair(rsa_private_key_path, rsa_public_key_path)
        self.manifest_path.write_bytes(b'{"knowledge_release_id":"kr-test"}\n')

        with self.assertRaisesRegex(ReleaseTrustError, "not Ed25519"):
            sign_manifest(
                self.manifest_path,
                rsa_private_key_path,
                self.signature_path,
            )

        subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(rsa_private_key_path),
                "-in",
                str(self.manifest_path),
                "-out",
                str(self.signature_path),
            ],
            check=True,
            capture_output=True,
        )
        self._write_trust_root(rsa_public_key_path)

        with self.assertRaisesRegex(ReleaseTrustError, "not Ed25519"):
            verify_manifest_signature(
                self.manifest_path,
                self.signature_path,
                self.trust_root_path,
                self.trust_root_id,
            )

    def test_missing_signing_or_verification_inputs_are_rejected(self):
        missing_path = self.root / "missing"

        with self.subTest(input="manifest for signing"):
            with self.assertRaisesRegex(ReleaseTrustError, "Manifest is missing"):
                sign_manifest(
                    missing_path,
                    self.private_key_path,
                    self.signature_path,
                )

        self.manifest_path.write_bytes(b"{}\n")
        with self.subTest(input="private key"):
            with self.assertRaisesRegex(
                ReleaseTrustError, "Private signing key is missing"
            ):
                sign_manifest(self.manifest_path, missing_path, self.signature_path)

        self._sign_default_manifest()
        verification_inputs = {
            "manifest": (
                missing_path,
                self.signature_path,
                self.trust_root_path,
                "Manifest is missing",
            ),
            "signature": (
                self.manifest_path,
                missing_path,
                self.trust_root_path,
                "Detached signature is missing",
            ),
            "trust root": (
                self.manifest_path,
                self.signature_path,
                missing_path,
                "Trust root is missing",
            ),
        }
        for label, values in verification_inputs.items():
            manifest, signature, trust_root, message = values
            with self.subTest(input=label):
                with self.assertRaisesRegex(ReleaseTrustError, message):
                    verify_manifest_signature(
                        manifest,
                        signature,
                        trust_root,
                        self.trust_root_id,
                    )

    def test_trust_root_metadata_must_select_the_expected_active_ed25519_key(self):
        self._sign_default_manifest()
        invalid_roots = {
            "wrong ID": (
                {"trust_root_id": "other-release-key"},
                self.trust_root_id,
                "Trust root ID",
            ),
            "unexpected expected ID": (
                {},
                "other-release-key",
                "Trust root ID",
            ),
            "inactive": ({"status": "revoked"}, self.trust_root_id, "not active"),
            "wrong algorithm": (
                {"algorithm": "rsa-pss"},
                self.trust_root_id,
                "does not use Ed25519",
            ),
            "wrong schema": (
                {"schema_version": "2.0"},
                self.trust_root_id,
                "schema version",
            ),
        }

        for label, (overrides, expected_id, message) in invalid_roots.items():
            with self.subTest(root=label):
                self._write_trust_root(self.public_key_path, **overrides)
                with self.assertRaisesRegex(ReleaseTrustError, message):
                    verify_manifest_signature(
                        self.manifest_path,
                        self.signature_path,
                        self.trust_root_path,
                        expected_id,
                    )

    def test_path_like_trust_root_id_is_rejected(self):
        self._sign_default_manifest()
        unsafe_trust_root_id = "../../attacker"
        self._write_trust_root(
            self.public_key_path,
            trust_root_id=unsafe_trust_root_id,
        )

        with self.assertRaises(ReleaseTrustError):
            verify_manifest_signature(
                self.manifest_path,
                self.signature_path,
                self.trust_root_path,
                unsafe_trust_root_id,
            )

    def test_trust_root_rejects_missing_fields_and_ambiguous_json(self):
        self._sign_default_manifest()
        trust_root = json.loads(self.trust_root_path.read_text(encoding="utf-8"))
        trust_root.pop("public_key_pem")
        self.trust_root_path.write_text(json.dumps(trust_root), encoding="utf-8")

        with self.assertRaisesRegex(ReleaseTrustError, "public_key_pem"):
            verify_manifest_signature(
                self.manifest_path,
                self.signature_path,
                self.trust_root_path,
                self.trust_root_id,
            )

        self.trust_root_path.write_text(
            '{"trust_root_id":"first","trust_root_id":"second"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseTrustError, "unambiguous valid JSON"):
            verify_manifest_signature(
                self.manifest_path,
                self.signature_path,
                self.trust_root_path,
                self.trust_root_id,
            )


if __name__ == "__main__":
    unittest.main()
