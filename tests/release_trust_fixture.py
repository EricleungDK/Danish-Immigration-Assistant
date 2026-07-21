"""Explicitly test-only signing material for generated knowledge releases.

The trust root created here must never be copied into ``config/trust_roots`` or
used as a production-default trust anchor.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


TEST_ONLY_TRUST_ROOT_ID = "test-only-release-key-v1"


@dataclass(frozen=True)
class TestReleaseTrustFixture:
    signing_private_key_path: Path
    trust_root_path: Path
    trust_root_id: str = TEST_ONLY_TRUST_ROOT_ID


def create_test_release_trust_fixture(
    fixture_dir: str | Path,
) -> TestReleaseTrustFixture:
    """Create or reuse an isolated Ed25519 key pair for test releases only."""

    resolved_fixture_dir = Path(fixture_dir)
    resolved_fixture_dir.mkdir(parents=True, exist_ok=True)
    private_key_path = (
        resolved_fixture_dir / "TEST-ONLY-DO-NOT-TRUST-release-private-key.pem"
    )
    trust_root_path = resolved_fixture_dir / "TEST-ONLY-release-trust-root.json"
    fixture = TestReleaseTrustFixture(
        signing_private_key_path=private_key_path,
        trust_root_path=trust_root_path,
    )
    if private_key_path.is_file() and trust_root_path.is_file():
        return fixture

    try:
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        public_key = subprocess.run(
            [
                "openssl",
                "pkey",
                "-in",
                str(private_key_path),
                "-pubout",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        ).stdout.decode("ascii")
    except (FileNotFoundError, subprocess.CalledProcessError, UnicodeError) as exc:
        raise RuntimeError(
            "Could not create the explicitly test-only Ed25519 release key."
        ) from exc

    private_key_path.chmod(0o600)
    trust_root_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "trust_root_id": TEST_ONLY_TRUST_ROOT_ID,
                "algorithm": "ed25519",
                "public_key_pem": public_key,
                "status": "active",
                "test_only": True,
                "warning": "Never trust this key outside automated tests.",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return fixture
