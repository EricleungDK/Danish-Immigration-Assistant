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


def validate_runtime_baseline_prose_contract(
    policy: dict[str, Any], document_text: str
) -> list[str]:
    normalized_text = _normalize_prose(document_text)
    provider = policy["providers"]["initial"]
    models = policy["models"]
    app = policy["application"]
    browser_security = policy["browser_security"]
    network = policy["network"]
    privacy = policy["privacy"]
    knowledge_releases = policy["knowledge_releases"]
    supported_environment = policy["supported_environment"]
    verified_environment = supported_environment["first_verified"]
    hardware = supported_environment["hardware"]

    failures: list[str] = []
    _require_phrases(
        failures,
        "provider neutrality",
        normalized_text,
        [
            f"{provider['name']} is the first MVP provider baseline",
            "not a permanent product mandate",
            "provider-specific adapters",
        ],
    )
    if provider["mandatory_for_future_versions"]:
        failures.append("provider neutrality policy makes the first provider mandatory")
    _reject_phrases(
        failures,
        "provider neutrality",
        normalized_text,
        [
            f"{provider['name']} is mandatory",
            f"must use {provider['name']}",
            "only supported provider",
            "permanent product mandate and ollama is mandatory",
        ],
    )

    _require_phrases(
        failures,
        "generation and embedding",
        normalized_text,
        [
            f"approved initial generation model is {models['generation']['initial']}",
            "not an approved official source",
            "cannot supply official facts from model knowledge",
            "generation and embedding are separate capabilities",
            "generation accepts messages, a response schema, and runtime options",
            "returns structured output with provider and model identity",
            "embedding accepts text inputs",
            "returns vectors with model identity and vector dimensions",
            (
                f"{models['embedding']['provisional_candidate']} is only a "
                "provisional embedding candidate"
            ),
            (
                "not a supported embedding model until the retrieval benchmark "
                "and later human architecture approval accept it"
            ),
        ],
    )
    _reject_phrases(
        failures,
        "generation and embedding",
        normalized_text,
        [
            "is an approved official source",
            "may supply official facts from model knowledge",
            "generation and embedding are interchangeable",
            "supported embedding model for production before retrieval benchmark",
            "any embedding model",
        ],
    )

    _require_phrases(
        failures,
        "loopback and browser security",
        normalized_text,
        [
            "local-only answer path",
            f"application bind host: {app['default_bind_host']}",
            f"{provider['name']} endpoint: {provider['default_endpoint']}",
            "non-loopback application exposure is unsupported",
            "state-changing browser requests must validate host and origin",
            "must not be placed in urls, logs, or test output",
            "does not require provider credentials",
        ],
    )
    if not browser_security["reject_non_loopback_by_default"]:
        failures.append("loopback policy must reject non-loopback defaults")
    if not browser_security["validate_host_and_origin_for_state_changes"]:
        failures.append("loopback policy must require Host and Origin validation")
    if privacy["provider_credentials_required_for_mvp"]:
        failures.append("loopback policy must not require provider credentials for the MVP")
    _reject_phrases(
        failures,
        "loopback and browser security",
        normalized_text,
        [
            "non-loopback application exposure is supported by default",
            "do not need host or origin validation",
            "provider credentials are required",
        ],
    )

    _require_phrases(
        failures,
        "Knowledge release update separation",
        normalized_text,
        [
            "release network activity is separate from the local-only answer path",
            "approved knowledge release artifact only after explicit user approval",
            "knowledge release installation is separate from application-code updates",
            "must not use git pull as an update mechanism",
            "application-code updates are manual",
        ],
    )
    if network["answer_path_allows_outbound_requests"]:
        failures.append("Knowledge release policy must not permit answer-path networking")
    if not network["knowledge_release_download_requires_user_approval"]:
        failures.append("Knowledge release downloads must require user approval")
    if not knowledge_releases["separate_from_application_code_updates"]:
        failures.append("Knowledge release updates must remain separate from code updates")
    if not knowledge_releases["application_must_not_run_git_pull"]:
        failures.append("Knowledge release policy must prohibit git pull updates")
    _reject_phrases(
        failures,
        "Knowledge release update separation",
        normalized_text,
        [
            "knowledge release installation is bundled with application-code updates",
            "should use git pull as an update mechanism",
            "answer-time browsing is allowed",
            "remote inference is allowed",
            "remote embedding is allowed",
        ],
    )

    _require_phrases(
        failures,
        "environment requirements",
        normalized_text,
        [
            (
                f"first verified environment is {verified_environment['host']} "
                f"on {verified_environment['architecture']}"
            ),
            f"python {verified_environment['python'].replace('+', ' or newer')}",
            f"{provider['name']} {verified_environment['ollama'].replace('+', ' or newer')}",
            verified_environment["browser"],
            f"{hardware['minimum_system_ram_gb']} gb system ram is the initial minimum",
            f"{hardware['recommended_system_ram_gb']} gb is recommended",
            "gpu acceleration is recommended",
            "cpu-only compatibility and latency are measured rather than guaranteed",
            "does not claim them verified",
        ],
    )
    _reject_phrases(
        failures,
        "environment requirements",
        normalized_text,
        [
            "macos and native linux are verified",
            "cpu-only compatibility and latency are guaranteed",
            "desktop packaging is verified",
            "background services are verified",
        ],
    )

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


def _normalize_prose(text: str) -> str:
    return " ".join(text.replace("`", "").casefold().split())


def _require_phrases(
    failures: list[str], label: str, normalized_text: str, phrases: list[str]
) -> None:
    missing = [
        phrase
        for phrase in phrases
        if _normalize_prose(phrase) not in normalized_text
    ]
    if missing:
        failures.append(
            f"{label} prose is missing or contradicts: {', '.join(missing)}"
        )


def _reject_phrases(
    failures: list[str], label: str, normalized_text: str, phrases: list[str]
) -> None:
    rejected = [
        phrase
        for phrase in phrases
        if _normalize_prose(phrase) in normalized_text
    ]
    if rejected:
        failures.append(
            f"{label} prose contains contradictory claims: {', '.join(rejected)}"
        )


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
