"""Release qualification loading and documentation contract checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTRACT_START = "<!-- release-qualification-contract:start -->"
CONTRACT_END = "<!-- release-qualification-contract:end -->"

BLOCKING_GATE_STATUSES = {
    "blocked",
    "failed",
    "not_implemented",
    "not_run",
    "not_verified",
    "pending",
}

REQUIRED_BLOCKING_CONDITION_IDS = {
    "uncited-official-fact",
    "personal-eligibility-conclusion",
    "answer-path-personal-data-egress",
    "failed-atomic-rollback",
}

REQUIRED_DOCUMENTATION_PHRASES = {
    "Release Decision": [
        "release decision",
        "do-not-release",
        "blocked",
    ],
    "Distribution Package": [
        "local python web application",
        "single local python process",
        "requirements.txt",
    ],
    "Operating Instructions": [
        "python 3.11 or newer",
        "127.0.0.1",
        "one local process",
    ],
    "Privacy Boundary": [
        "local-only answer path",
        "no account",
        "no cloud history",
        "no remote inference credential",
        "production-user questions are not analytics input",
    ],
    "Model And Runtime": [
        "ollama 0.30.6",
        "gemma4:12b",
        "embeddinggemma",
        "generation and embedding remain separate",
    ],
    "Corpus And Knowledge Releases": [
        "kr-2026-07-06.1",
        "approved-current",
        "reviewed official source",
        "source registry",
    ],
    "Updates And Rollback": [
        "explicit user approval",
        "application-code updates are manual",
        "rollback",
        "previous usable corpus and index",
    ],
    "Recovery": [
        "provider",
        "retrieval",
        "storage",
        "corpus activation",
    ],
    "Support Boundary": [
        "windows 11 with wsl2 ubuntu",
        "native windows is not supported",
        "macos and native linux remain candidates",
    ],
    "Evaluation Results And Limitations": [
        "di-rag-eval-set-v0.1-candidate",
        "required evidence recall@3",
        "official-fact citation coverage",
        "unsupported-claim rate",
        "performance",
        "human approval",
    ],
}


def load_release_qualification(path: str | Path) -> dict[str, Any]:
    qualification_path = Path(path)
    with qualification_path.open(encoding="utf-8") as qualification_file:
        qualification: dict[str, Any] = json.load(qualification_file)

    failures = validate_release_qualification(qualification)
    if failures:
        joined = "; ".join(failures)
        raise ValueError(f"Invalid release qualification {qualification_path}: {joined}")
    return qualification


def extract_documented_release_contract(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    return extract_documented_release_contract_from_text(text)


def derive_release_blockers(qualification: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []

    for blocker in qualification.get("explicit_release_blockers", []):
        if blocker.get("release_blocking", True):
            blockers.append(
                {
                    "id": blocker.get("id"),
                    "source": "explicit_release_blockers",
                    "status": blocker.get("status", "blocked"),
                    "reason": blocker.get("reason", ""),
                }
            )

    for gate in qualification.get("gate_results", []):
        if not gate.get("release_blocking", False):
            continue
        status = gate.get("status")
        if status in BLOCKING_GATE_STATUSES:
            blockers.append(
                {
                    "id": gate.get("id"),
                    "source": "gate_results",
                    "status": status,
                    "reason": gate.get("summary", ""),
                }
            )

    for approval in qualification.get("human_approval_records", []):
        if not approval.get("required", False):
            continue
        status = approval.get("status")
        if status != "approved":
            blockers.append(
                {
                    "id": approval.get("id"),
                    "source": "human_approval_records",
                    "status": status,
                    "reason": approval.get("required_for", ""),
                }
            )

    for condition in qualification.get("blocking_conditions", []):
        if not condition.get("release_blocking", False):
            continue
        observed = condition.get("observed_count")
        max_allowed = condition.get("max_allowed")
        if observed is None or max_allowed is None:
            continue
        if observed > max_allowed:
            blockers.append(
                {
                    "id": condition.get("id"),
                    "source": "blocking_conditions",
                    "status": "failed",
                    "reason": condition.get("description", ""),
                }
            )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for blocker in blockers:
        blocker_id = blocker.get("id")
        if not isinstance(blocker_id, str) or blocker_id in seen:
            continue
        seen.add(blocker_id)
        deduped.append(blocker)
    return deduped


def validate_release_qualification(qualification: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    _require_string(failures, qualification, "qualification_id")
    _require_string(failures, qualification, "version")
    _require_string(failures, qualification, "qualification_status")
    _require_string(failures, qualification, "release_decision")

    if qualification.get("qualification_status") not in {"blocked", "qualified"}:
        failures.append("qualification status must be blocked or qualified")
    if qualification.get("release_decision") not in {"do-not-release", "release"}:
        failures.append("release decision must be do-not-release or release")

    blockers = derive_release_blockers(qualification)
    if blockers:
        if qualification.get("qualification_status") != "blocked":
            failures.append("release with active blockers must remain blocked")
        if qualification.get("release_decision") != "do-not-release":
            failures.append("release with active blockers must remain do-not-release")
    elif qualification.get("qualification_status") == "blocked":
        failures.append("blocked release qualification must explain at least one blocker")

    quality_bar = qualification.get("quality_bar", {})
    if quality_bar.get("approval_required") and quality_bar.get("approval_status") != "approved":
        if not _has_blocker(blockers, "quality-bar-human-approval-pending"):
            failures.append("pending quality-bar approval must be a release blocker")

    evaluation = qualification.get("evaluation", {})
    if evaluation.get("uses_production_user_questions") is not False:
        failures.append("release evaluation must not use production-user questions")
    if evaluation.get("retrieval_and_final_answer_evaluation_separate") is not True:
        failures.append("retrieval and final-answer evaluation must remain separate")

    runtime = qualification.get("runtime", {})
    if runtime.get("answer_path_allows_outbound_requests") is not False:
        failures.append("answer path must not allow outbound requests")
    if runtime.get("application_process_model") != "single-local-python-process":
        failures.append("distribution must use one local Python process")
    if runtime.get("default_bind_host") != "127.0.0.1":
        failures.append("application must bind to loopback by default")
    if runtime.get("application_code_updates") != "manual":
        failures.append("application-code updates must remain manual")
    if runtime.get("knowledge_release_updates") != "explicit-user-approved":
        failures.append("knowledge release updates must require explicit user approval")

    blocking_ids = {
        condition.get("id") for condition in qualification.get("blocking_conditions", [])
    }
    missing_conditions = REQUIRED_BLOCKING_CONDITION_IDS - blocking_ids
    if missing_conditions:
        failures.append(
            "release qualification is missing blocking condition(s): "
            + ", ".join(sorted(missing_conditions))
        )

    for condition in qualification.get("blocking_conditions", []):
        if condition.get("id") in REQUIRED_BLOCKING_CONDITION_IDS:
            if condition.get("release_blocking") is not True:
                failures.append(f"{condition.get('id')} must be release-blocking")
            if condition.get("max_allowed") != 0:
                failures.append(f"{condition.get('id')} must allow zero occurrences")

    if not isinstance(qualification.get("documentation_contract"), dict):
        failures.append("release qualification must include a documentation contract")

    return failures


def validate_release_document_contract(
    qualification: dict[str, Any], documented_contract: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    expected_contract = qualification.get("documentation_contract")

    if documented_contract != expected_contract:
        failures.append("documented release qualification contract differs from config")

    expected_values = {
        "qualification_id": qualification.get("qualification_id"),
        "version": qualification.get("version"),
        "qualification_status": qualification.get("qualification_status"),
        "release_decision": qualification.get("release_decision"),
        "quality_bar_version": qualification.get("quality_bar", {}).get("version"),
        "quality_bar_approval_status": qualification.get("quality_bar", {}).get(
            "approval_status"
        ),
        "evaluation_dataset_id": qualification.get("evaluation", {}).get("dataset_id"),
        "evaluation_dataset_version": qualification.get("evaluation", {}).get(
            "dataset_version"
        ),
        "application_distribution": qualification.get("distribution", {}).get("kind"),
        "application_process_model": qualification.get("runtime", {}).get(
            "application_process_model"
        ),
        "default_bind_host": qualification.get("runtime", {}).get("default_bind_host"),
        "generation_model": qualification.get("runtime", {}).get("generation_model"),
        "embedding_model": qualification.get("runtime", {}).get("embedding_model"),
        "active_knowledge_release_id": qualification.get(
            "active_corpus_requirements", {}
        ).get("knowledge_release_id"),
        "answer_path_allows_outbound_requests": qualification.get("runtime", {}).get(
            "answer_path_allows_outbound_requests"
        ),
        "production_user_question_analytics_allowed": qualification.get(
            "evaluation", {}
        ).get("uses_production_user_questions"),
    }
    for key, expected_value in expected_values.items():
        if documented_contract.get(key) != expected_value:
            failures.append(f"documented {key!r} does not match release qualification")

    return failures


def validate_release_qualification_sources(
    qualification: dict[str, Any],
    runtime_policy: dict[str, Any],
    quality_bar: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    runtime = qualification.get("runtime", {})
    policy_provider = runtime_policy.get("providers", {}).get("initial", {})
    policy_models = runtime_policy.get("models", {})
    policy_application = runtime_policy.get("application", {})
    policy_network = runtime_policy.get("network", {})
    policy_privacy = runtime_policy.get("privacy", {})
    policy_releases = runtime_policy.get("knowledge_releases", {})

    source_expectations = {
        "runtime initial provider": (
            runtime.get("initial_provider"),
            policy_provider.get("id"),
        ),
        "runtime minimum provider version": (
            runtime.get("minimum_provider_version"),
            policy_provider.get("minimum_version"),
        ),
        "generation model": (
            runtime.get("generation_model"),
            policy_models.get("generation", {}).get("initial"),
        ),
        "application process model": (
            runtime.get("application_process_model"),
            policy_application.get("process_model"),
        ),
        "default bind host": (
            runtime.get("default_bind_host"),
            policy_application.get("default_bind_host"),
        ),
        "default provider endpoint": (
            runtime.get("default_provider_endpoint"),
            policy_provider.get("default_endpoint"),
        ),
        "application-code update policy": (
            runtime.get("application_code_updates"),
            policy_application.get("code_updates"),
        ),
        "knowledge-release update policy": (
            runtime.get("knowledge_release_updates"),
            policy_releases.get("updates"),
        ),
        "answer-path network policy": (
            runtime.get("answer_path_allows_outbound_requests"),
            policy_network.get("answer_path_allows_outbound_requests"),
        ),
        "account requirement": (
            runtime.get("account_required_for_mvp"),
            policy_privacy.get("account_required_for_mvp"),
        ),
        "cloud-history requirement": (
            runtime.get("cloud_history_required_for_mvp"),
            policy_privacy.get("cloud_history_required_for_mvp"),
        ),
        "remote-inference credential requirement": (
            runtime.get("remote_inference_credentials_required_for_mvp"),
            policy_privacy.get("remote_inference_credentials_required_for_mvp"),
        ),
        "provider credential requirement": (
            runtime.get("provider_credentials_required_for_mvp"),
            policy_privacy.get("provider_credentials_required_for_mvp"),
        ),
        "embedding model": (
            runtime.get("embedding_model"),
            quality_bar.get("retrieval_baseline", {}).get("embedding_model"),
        ),
    }
    for label, (observed, expected) in source_expectations.items():
        if observed != expected:
            failures.append(
                f"release qualification {label} {observed!r} does not match source {expected!r}"
            )

    quality = qualification.get("quality_bar", {})
    evaluation = qualification.get("evaluation", {})
    quality_expectations = {
        "quality bar id": (
            quality.get("quality_bar_id"),
            quality_bar.get("quality_bar_id"),
        ),
        "quality bar version": (
            quality.get("version"),
            quality_bar.get("version"),
        ),
        "quality bar approval status": (
            quality.get("approval_status"),
            quality_bar.get("approval_status"),
        ),
        "evaluation dataset id": (
            evaluation.get("dataset_id"),
            quality_bar.get("evaluation_set", {}).get("dataset_id"),
        ),
        "evaluation dataset version": (
            evaluation.get("dataset_version"),
            quality_bar.get("evaluation_set", {}).get("version"),
        ),
        "evaluation case count": (
            evaluation.get("case_count"),
            quality_bar.get("evaluation_set", {}).get("case_count"),
        ),
        "retrieval/final-answer separation": (
            evaluation.get("retrieval_and_final_answer_evaluation_separate"),
            quality_bar.get("evaluation_set", {}).get(
                "retrieval_and_final_answer_evaluation_separate"
            ),
        ),
    }
    for label, (observed, expected) in quality_expectations.items():
        if observed != expected:
            failures.append(
                f"release qualification {label} {observed!r} does not match source {expected!r}"
            )

    metric_ids = {metric.get("id") for metric in quality_bar.get("metrics", [])}
    published_metrics = set(evaluation.get("metrics_published", []))
    missing_metrics = metric_ids - published_metrics
    if missing_metrics:
        failures.append(
            "release qualification omits quality-bar metric(s): "
            + ", ".join(sorted(str(metric) for metric in missing_metrics))
        )

    return failures


def validate_release_documentation_prose(
    qualification: dict[str, Any], document_text: str
) -> list[str]:
    failures: list[str] = []
    normalized_text = _normalize_prose(document_text)

    for section, phrases in REQUIRED_DOCUMENTATION_PHRASES.items():
        if section.casefold() not in normalized_text:
            failures.append(f"release documentation is missing {section}")
        for phrase in phrases:
            if phrase.casefold() not in normalized_text:
                failures.append(f"{section} documentation is missing {phrase!r}")

    contract_failures = validate_release_document_contract(
        qualification,
        extract_documented_release_contract_from_text(document_text),
    )
    failures.extend(contract_failures)
    return failures


def extract_documented_release_contract_from_text(document_text: str) -> dict[str, Any]:
    try:
        contract_block = document_text.split(CONTRACT_START, 1)[1].split(
            CONTRACT_END, 1
        )[0]
    except IndexError as exc:
        raise ValueError("Release documentation is missing qualification contract") from exc

    contract_text = _strip_markdown_json_fence(contract_block.strip())
    return json.loads(contract_text)


def _strip_markdown_json_fence(text: str) -> str:
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return text


def _normalize_prose(text: str) -> str:
    return " ".join(text.casefold().split())


def _has_blocker(blockers: list[dict[str, Any]], blocker_id: str) -> bool:
    return any(blocker.get("id") == blocker_id for blocker in blockers)


def _require_string(
    failures: list[str], values: dict[str, Any], key: str
) -> None:
    if not isinstance(values.get(key), str) or not values.get(key):
        failures.append(f"release qualification is missing {key}")
