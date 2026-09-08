"""Verify the pinned public evidence set for a specific release candidate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from danish_rag.evidence_integrity import reject_duplicate_json_object, sha256_file
from danish_rag.knowledge_release import verify_knowledge_release
from danish_rag.source_registry import load_source_registry, validate_source_registry_against_release, assess_source_registry_qualification


def safe_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("candidate evidence path is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("candidate evidence path must stay within the repository")
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("candidate evidence path escapes the repository")
    return result


def read_artifact(root: Path, candidate: dict, name: str) -> dict:
    reference = candidate["artifacts"][name]
    path = safe_path(root, reference["path"])
    if sha256_file(path) != reference.get("sha256"):
        raise ValueError(f"candidate {name} evidence hash does not match")
    value = json.loads(path.read_text(), object_pairs_hook=reject_duplicate_json_object)
    if not isinstance(value, dict):
        raise ValueError(f"candidate {name} evidence is not an object")
    return value


def validate_candidate_evidence(root: Path, qualification: dict) -> list[str]:
    candidate = qualification.get("candidate_evidence")
    if candidate is None:
        return []
    failures = []
    try:
        if not isinstance(candidate, dict) or candidate.get("schema_version") != "candidate-release-evidence-v1":
            raise ValueError("unsupported candidate evidence schema")
        release_dir = safe_path(root, candidate["release_dir"])
        if sha256_file(release_dir / "manifest.json") != candidate["manifest_sha256"]:
            raise ValueError("candidate manifest hash does not match")
        release = verify_knowledge_release(release_dir, trust_root_path=safe_path(root, candidate["trust_root_path"]))
        manifest = release["manifest"]
        required = qualification["active_corpus_requirements"]
        for field in ("knowledge_release_id", "corpus_schema_version"):
            if required[field] != manifest[field]:
                failures.append(f"candidate manifest does not match configured {field}")
        if required["source_registry_version"] != manifest["source_registry_version"]:
            failures.append("candidate registry identity differs from qualification")
        if safe_path(root, required["manifest_path"]) != release_dir / "manifest.json":
            failures.append("qualification refers to a different candidate manifest path")
        required_artifacts = {"retrieval", "production_install", "final_answer", "final_answer_origin", "release_monitors", "candidate_browser", "signing_custody", "source_registry"}
        contract = candidate.get("retrieval_contract")
        if contract is not None:
            if contract != "owner-approved-production-and-fixture-v1":
                raise ValueError("unsupported candidate retrieval contract")
            required_artifacts.update({"retrieval_proposal", "retrieval_approval", "approved_retrieval"})
        if not required_artifacts.issubset(candidate.get("artifacts", {})):
            raise ValueError("candidate evidence set is incomplete")
        for name in candidate["artifacts"]:
            read_artifact(root, candidate, name)
        registry = load_source_registry(safe_path(root, candidate["artifacts"]["source_registry"]["path"]))
        validate_source_registry_against_release(registry, release_dir)
        if assess_source_registry_qualification(registry)["production_release_eligible"] is not True:
            failures.append("candidate source registry is not production-qualified")
        custody = read_artifact(root, candidate, "signing_custody")
        root_id = manifest["integrity"]["trust_root_id"]
        backup = custody.get("custody", {}).get("backup_verification", {})
        trust = json.loads(safe_path(root, candidate["trust_root_path"]).read_text())
        if custody.get("active_trust_root_id") != root_id or custody.get("public_key_sha256") != trust.get("public_key_sha256"):
            failures.append("signing custody does not match the candidate trust root")
        if (custody.get("custody", {}).get("backup_status") != "owner-confirmed-off-device-backup-recovery-verified"
            or backup.get("public_key_matches") is not True or backup.get("trust_root_id") != root_id
            or backup.get("original_key_retained") is not True):
            failures.append("candidate signing-key recovery backup is not verified")
        installation = read_artifact(root, candidate, "production_install")
        installed = installation.get("candidate_identity", {})
        if (installation.get("knowledge_release_id") != manifest["knowledge_release_id"]
            or installed.get("knowledge_release_id") != manifest["knowledge_release_id"]
            or installed.get("manifest_sha256") != candidate["manifest_sha256"]
            or installed.get("trust_root_id") != root_id
            or installed.get("corpus_artifact_sha256") != sha256_file(release_dir / "corpus/documents.json")
            or installed.get("signature_sha256") != sha256_file(release_dir / manifest["integrity"]["signature"])):
            failures.append("installation evidence does not match the signed candidate")
        index = installed.get("index_metadata", {})
        retrieval_index = read_artifact(root, candidate, "retrieval").get("provenance", {}).get("index_metadata")
        if index != retrieval_index:
            failures.append("installation and retrieval index identities differ")
        if (index.get("knowledge_release_id") != manifest["knowledge_release_id"]
            or index.get("corpus_schema_version") != manifest["corpus_schema_version"]
            or index.get("embedding_model") != qualification["runtime"]["embedding_model"]):
            failures.append("installation index does not match configured candidate")
        browser = read_artifact(root, candidate, "candidate_browser")
        if browser.get("passed") is not True or browser.get("corpus_id") != manifest["knowledge_release_id"]:
            failures.append("candidate browser evidence does not pass for this corpus")
        if (browser.get("model") != qualification["runtime"]["generation_model"]
            or browser.get("mode") != "actual-candidate-live-ollama"):
            failures.append("candidate browser execution model or mode differs")
        if browser.get("test_sha256") != sha256_file(safe_path(root, browser["test"])):
            failures.append("candidate browser evidence does not match the current test")
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        failures.append(str(exc))
    return failures
