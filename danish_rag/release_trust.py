"""Detached Ed25519 signing and verification for knowledge-release manifests."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .evidence_integrity import reject_duplicate_json_object


TRUST_ROOT_SCHEMA_VERSION = "1.0"
SIGNATURE_ALGORITHM = "ed25519"
ACTIVE_TRUST_ROOT_STATUS = "active"
ED25519_SIGNATURE_BYTES = 64
ED25519_SUBJECT_PUBLIC_KEY_INFO_PREFIX = bytes.fromhex(
    "302a300506032b6570032100"
)
ED25519_SUBJECT_PUBLIC_KEY_INFO_BYTES = 44
TRUST_ROOT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ReleaseTrustError(ValueError):
    """Raised when release signing inputs or signature verification are invalid."""


def sign_manifest(
    manifest_path: str | Path,
    private_key_path: str | Path,
    signature_path: str | Path,
) -> None:
    """Sign the manifest's exact bytes and write a raw detached Ed25519 signature."""

    resolved_manifest_path = Path(manifest_path)
    resolved_private_key_path = Path(private_key_path)
    resolved_signature_path = Path(signature_path)
    manifest_bytes = _read_required_bytes(resolved_manifest_path, "Manifest")
    _require_file(resolved_private_key_path, "Private signing key")
    encoded_public_key = _run_openssl(
        [
            "openssl",
            "pkey",
            "-in",
            str(resolved_private_key_path),
            "-pubout",
            "-outform",
            "DER",
        ],
        operation="inspect the private signing key",
    ).stdout
    _require_ed25519_public_key(encoded_public_key, "Private signing key")

    try:
        with tempfile.TemporaryDirectory(
            prefix="danish-rag-release-trust-"
        ) as temp_dir:
            exact_manifest_path = Path(temp_dir) / "manifest.json"
            exact_manifest_path.write_bytes(manifest_bytes)
            completed = _run_openssl(
                [
                    "openssl",
                    "pkeyutl",
                    "-sign",
                    "-rawin",
                    "-inkey",
                    str(resolved_private_key_path),
                    "-in",
                    str(exact_manifest_path),
                ],
                operation="sign the manifest",
            )
    except OSError as exc:
        raise ReleaseTrustError(
            "Could not prepare exact manifest bytes for signing."
        ) from exc
    signature = completed.stdout
    if len(signature) != ED25519_SIGNATURE_BYTES:
        raise ReleaseTrustError(
            "OpenSSL did not produce a raw Ed25519 manifest signature."
        )

    try:
        resolved_signature_path.write_bytes(signature)
    except OSError as exc:
        raise ReleaseTrustError(
            f"Could not write detached signature: {resolved_signature_path}"
        ) from exc


def verify_manifest_signature(
    manifest_path: str | Path,
    signature_path: str | Path,
    trust_root_path: str | Path,
    expected_trust_root_id: str,
) -> None:
    """Verify a raw signature against the exact manifest bytes and active trust root."""

    if not isinstance(expected_trust_root_id, str) or not TRUST_ROOT_ID_PATTERN.fullmatch(
        expected_trust_root_id
    ):
        raise ReleaseTrustError("Expected trust root ID has an unsafe format.")
    resolved_manifest_path = Path(manifest_path)
    resolved_signature_path = Path(signature_path)
    manifest_bytes = _read_required_bytes(resolved_manifest_path, "Manifest")
    signature_bytes = _read_required_bytes(
        resolved_signature_path, "Detached signature"
    )
    if len(signature_bytes) != ED25519_SIGNATURE_BYTES:
        raise ReleaseTrustError("Detached signature is not a raw Ed25519 signature.")

    trust_root = _load_trust_root(Path(trust_root_path), expected_trust_root_id)
    public_key_pem = trust_root["public_key_pem"]

    try:
        with tempfile.TemporaryDirectory(
            prefix="danish-rag-release-trust-"
        ) as temp_dir:
            temp_root = Path(temp_dir)
            exact_manifest_path = temp_root / "manifest.json"
            public_key_path = temp_root / "release-public-key.pem"
            detached_signature_path = temp_root / "manifest.sig"
            exact_manifest_path.write_bytes(manifest_bytes)
            public_key_path.write_text(public_key_pem, encoding="ascii")
            detached_signature_path.write_bytes(signature_bytes)
            encoded_public_key = _run_openssl(
                [
                    "openssl",
                    "pkey",
                    "-pubin",
                    "-in",
                    str(public_key_path),
                    "-pubout",
                    "-outform",
                    "DER",
                ],
                operation="inspect the trust root public key",
            ).stdout
            _require_ed25519_public_key(encoded_public_key, "Trust root public key")
            completed = _run_openssl(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-inkey",
                    str(public_key_path),
                    "-rawin",
                    "-sigfile",
                    str(detached_signature_path),
                    "-in",
                    str(exact_manifest_path),
                ],
                operation="verify the manifest signature",
                allow_failure=True,
            )
    except (OSError, UnicodeEncodeError) as exc:
        raise ReleaseTrustError("Trust root contains an invalid public key.") from exc

    if completed.returncode != 0:
        raise ReleaseTrustError("Manifest signature verification failed.")


def _load_trust_root(
    trust_root_path: Path, expected_trust_root_id: str
) -> dict[str, Any]:
    try:
        raw_trust_root = trust_root_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ReleaseTrustError(f"Trust root is missing: {trust_root_path}") from exc
    except (OSError, UnicodeError) as exc:
        raise ReleaseTrustError(
            f"Could not read trust root: {trust_root_path}"
        ) from exc

    try:
        trust_root = json.loads(
            raw_trust_root,
            object_pairs_hook=reject_duplicate_json_object,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReleaseTrustError("Trust root must be unambiguous valid JSON.") from exc
    if not isinstance(trust_root, dict):
        raise ReleaseTrustError("Trust root JSON must be an object.")

    required_fields = {
        "schema_version",
        "trust_root_id",
        "algorithm",
        "public_key_pem",
        "status",
    }
    missing_fields = sorted(required_fields - set(trust_root))
    if missing_fields:
        raise ReleaseTrustError(
            f"Trust root missing field(s): {', '.join(missing_fields)}."
        )
    if trust_root["schema_version"] != TRUST_ROOT_SCHEMA_VERSION:
        raise ReleaseTrustError("Unsupported trust root schema version.")
    if trust_root["trust_root_id"] != expected_trust_root_id:
        raise ReleaseTrustError(
            "Trust root ID does not match the expected release key."
        )
    if trust_root["algorithm"] != SIGNATURE_ALGORITHM:
        raise ReleaseTrustError("Trust root does not use Ed25519.")
    if trust_root["status"] != ACTIVE_TRUST_ROOT_STATUS:
        raise ReleaseTrustError("Trust root is not active.")

    public_key_pem = trust_root["public_key_pem"]
    if not isinstance(public_key_pem, str) or not public_key_pem.strip():
        raise ReleaseTrustError("Trust root public key must be a non-empty PEM string.")
    if "-----BEGIN PUBLIC KEY-----" not in public_key_pem:
        raise ReleaseTrustError("Trust root public key must be a public-key PEM.")
    return trust_root


def _read_required_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise ReleaseTrustError(f"{label} is missing: {path}") from exc
    except OSError as exc:
        raise ReleaseTrustError(f"Could not read {label.lower()}: {path}") from exc


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise ReleaseTrustError(f"{label} is missing: {path}")


def _require_ed25519_public_key(encoded_public_key: bytes, label: str) -> None:
    if (
        len(encoded_public_key) != ED25519_SUBJECT_PUBLIC_KEY_INFO_BYTES
        or not encoded_public_key.startswith(
            ED25519_SUBJECT_PUBLIC_KEY_INFO_PREFIX
        )
    ):
        raise ReleaseTrustError(f"{label} is not Ed25519.")


def _run_openssl(
    argv: list[str],
    *,
    operation: str,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ReleaseTrustError("OpenSSL executable was not found.") from exc
    except OSError as exc:
        raise ReleaseTrustError(f"Could not run OpenSSL to {operation}.") from exc

    if completed.returncode != 0 and not allow_failure:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else "."
        raise ReleaseTrustError(f"OpenSSL could not {operation}{suffix}")
    return completed
