"""Runtime policy loading and documentation contract checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


CONTRACT_START = "<!-- runtime-policy-contract:start -->"
CONTRACT_END = "<!-- runtime-policy-contract:end -->"


def load_runtime_policy(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path)
    with policy_path.open(encoding="utf-8") as policy_file:
        policy: dict[str, Any] = json.load(policy_file)

    failures = _validate_runtime_policy_shape(policy)
    if failures:
        joined = "; ".join(failures)
        raise ValueError(f"Invalid runtime policy {policy_path}: {joined}")
    return policy


def extract_documented_policy_contract(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        contract_block = text.split(CONTRACT_START, 1)[1].split(CONTRACT_END, 1)[0]
    except IndexError as exc:
        raise ValueError("Runtime documentation is missing policy contract markers") from exc

    contract_text = _strip_markdown_json_fence(contract_block.strip())
    return json.loads(contract_text)


def validate_policy_document_contract(
    policy: dict[str, Any], documented_contract: dict[str, Any]
) -> list[str]:
    expected_contract = policy.get("documentation_contract")
    failures: list[str] = []

    if documented_contract != expected_contract:
        failures.append("documented policy contract differs from runtime-policy.json")

    provider = policy["providers"]["initial"]
    models = policy["models"]
    app = policy["application"]
    network = policy["network"]
    expected_values = {
        "baseline_id": policy["baseline_id"],
        "initial_provider": provider["id"],
        "minimum_ollama_version": provider["minimum_version"],
        "initial_generation_model": models["generation"]["initial"],
        "provisional_embedding_candidate": models["embedding"]["provisional_candidate"],
        "default_application_bind_host": app["default_bind_host"],
        "default_provider_endpoint": provider["default_endpoint"],
        "application_process_model": app["process_model"],
        "application_code_updates": app["code_updates"],
        "knowledge_release_updates": policy["knowledge_releases"]["updates"],
        "answer_path_allows_outbound_requests": network[
            "answer_path_allows_outbound_requests"
        ],
        "knowledge_release_checks_allowed": network[
            "knowledge_release_checks_allowed"
        ],
    }
    for key, expected_value in expected_values.items():
        if documented_contract.get(key) != expected_value:
            failures.append(f"documented {key!r} does not match runtime policy")

    return failures

def is_loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    host = parsed.hostname if parsed.scheme else value
    return host in {"127.0.0.1", "localhost", "::1"}


def _strip_markdown_json_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines[0].strip() not in {"```", "```json"}:
        return text
    if lines[-1].strip() != "```":
        raise ValueError("Runtime policy contract JSON fence is not closed")
    return "\n".join(lines[1:-1]).strip()


def _validate_runtime_policy_shape(policy: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    required_top_level = {
        "baseline_id",
        "providers",
        "models",
        "capabilities",
        "application",
        "browser_security",
        "network",
        "privacy",
        "knowledge_releases",
        "supported_environment",
        "documentation_contract",
    }
    for key in sorted(required_top_level - set(policy)):
        failures.append(f"missing top-level key {key!r}")

    if failures:
        return failures

    provider = policy["providers"]["initial"]
    if provider["id"] != "ollama":
        failures.append("initial provider must be ollama for issue #26 baseline")
    if not is_loopback_url(provider["default_endpoint"]):
        failures.append("initial provider endpoint must be loopback")
    if not is_loopback_url(policy["application"]["default_bind_host"]):
        failures.append("application default bind host must be loopback")

    capabilities = policy["capabilities"]
    if capabilities != ["generation", "embedding"]:
        failures.append("generation and embedding capabilities must remain separate")

    if policy["models"]["generation"]["treated_as_official_source"]:
        failures.append("generation model must not be treated as an official source")
    if policy["models"]["embedding"]["supported_for_production"]:
        failures.append("embeddinggemma is provisional until retrieval benchmark approval")
    if policy["network"]["answer_path_allows_outbound_requests"]:
        failures.append("local-only answer path must not allow outbound requests")
    if policy["privacy"]["put_user_content_in_urls_or_logs"]:
        failures.append("user content must not be placed in URLs or logs")

    return failures
