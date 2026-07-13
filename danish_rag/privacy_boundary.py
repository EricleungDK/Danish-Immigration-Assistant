"""Privacy boundary helpers for local answer and release-update traffic."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request

from .runtime_policy import is_loopback_url


APPROVED_RELEASE_NETWORK_OPERATIONS = {
    "knowledge_release_discovery",
    "approved_knowledge_release_artifact_retrieval",
    "project_release_discovery",
}
APPROVED_UPDATE_REQUEST_FIELDS = {
    "operation",
    "application_version",
    "active_knowledge_release_id",
    "requested_knowledge_release_id",
    "artifact_name",
}
REQUIRED_ANSWER_PATH_WORKFLOWS = {
    "question",
    "retrieval",
    "generation",
    "evidence_inspection",
    "history",
    "deletion",
    "export",
    "local_indexing",
    "knowledge_update_review",
}
REQUIRED_PROHIBITED_UPDATE_FIELDS = {
    "question",
    "normalized_question",
    "answer",
    "evidence",
    "conversation_id",
    "conversation_record",
    "turn_index",
    "citation_id",
    "prompt",
    "messages",
    "stable_conversation_derived_identifier",
}


class PrivacyBoundaryError(ValueError):
    """Raised when an operation would cross the configured privacy boundary."""


def require_loopback_endpoint(endpoint: str, *, purpose: str) -> None:
    """Require a provider endpoint to stay on loopback for local-only workflows."""

    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PrivacyBoundaryError(
            f"{purpose} requires a full loopback HTTP endpoint."
        )
    if not is_loopback_url(endpoint):
        raise PrivacyBoundaryError(
            f"{purpose} requires a loopback endpoint; refusing {endpoint}."
        )


def validate_runtime_policy_privacy_boundary(policy: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    network = policy.get("network", {})
    privacy = policy.get("privacy", {})

    if network.get("answer_path_allows_outbound_requests") is not False:
        failures.append("answer path must not allow outbound requests")
    if network.get("knowledge_release_download_requires_user_approval") is not True:
        failures.append("knowledge release artifact retrieval must require approval")
    if privacy.get("provider_credentials_required_for_mvp") is not False:
        failures.append("MVP must not require provider credentials")
    if privacy.get("account_required_for_mvp") is not False:
        failures.append("MVP must not require an account")
    if privacy.get("cloud_history_required_for_mvp") is not False:
        failures.append("MVP must not require cloud history")
    if privacy.get("remote_inference_credentials_required_for_mvp") is not False:
        failures.append("MVP must not require remote inference credentials")
    if privacy.get("put_user_content_in_urls_or_logs") is not False:
        failures.append("user content must not be placed in URLs or logs")
    sends_answer_path_content = privacy.get(
        "send_questions_answers_evidence_or_conversation_records_to_updates"
    )
    if sends_answer_path_content is not False:
        failures.append("update traffic must not receive answer-path content")

    observed = set(network.get("answer_path_observed_workflows", []))
    missing_observation = sorted(REQUIRED_ANSWER_PATH_WORKFLOWS - observed)
    if missing_observation:
        failures.append(
            "privacy observation is missing workflow(s): "
            + ", ".join(missing_observation)
        )

    operations = set(network.get("permitted_release_network_operations", []))
    unknown_operations = sorted(operations - APPROVED_RELEASE_NETWORK_OPERATIONS)
    missing_operations = sorted(APPROVED_RELEASE_NETWORK_OPERATIONS - operations)
    if unknown_operations:
        failures.append(
            "unapproved release network operation(s): "
            + ", ".join(unknown_operations)
        )
    if missing_operations:
        failures.append(
            "missing approved release network operation(s): "
            + ", ".join(missing_operations)
        )

    permitted_fields = set(network.get("permitted_update_request_fields", []))
    prohibited_fields = set(network.get("prohibited_update_request_fields", []))
    unknown_permitted = sorted(permitted_fields - APPROVED_UPDATE_REQUEST_FIELDS)
    missing_permitted = sorted(APPROVED_UPDATE_REQUEST_FIELDS - permitted_fields)
    if unknown_permitted:
        failures.append(
            "unapproved update request field(s): " + ", ".join(unknown_permitted)
        )
    if missing_permitted:
        failures.append(
            "missing approved update request field(s): " + ", ".join(missing_permitted)
        )
    missing_prohibited = sorted(REQUIRED_PROHIBITED_UPDATE_FIELDS - prohibited_fields)
    if missing_prohibited:
        failures.append(
            "update traffic denylist is missing field(s): "
            + ", ".join(missing_prohibited)
        )
    overlap = sorted(permitted_fields & prohibited_fields)
    if overlap:
        failures.append(
            "update request fields are both permitted and prohibited: "
            + ", ".join(overlap)
        )

    return failures


def build_release_network_request(
    policy: dict[str, Any],
    *,
    operation: str,
    base_url: str,
    application_version: str,
    active_knowledge_release_id: str = "",
    requested_knowledge_release_id: str = "",
    artifact_name: str = "",
    extra_fields: dict[str, str] | None = None,
) -> Request:
    """Build a release-network request from only policy-approved metadata fields."""

    if operation not in set(policy["network"]["permitted_release_network_operations"]):
        raise PrivacyBoundaryError(
            f"Release network operation is not approved: {operation}."
        )
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PrivacyBoundaryError("Release network base URL must be an HTTP URL.")

    fields = {
        "operation": operation,
        "application_version": application_version,
    }
    if active_knowledge_release_id:
        fields["active_knowledge_release_id"] = active_knowledge_release_id
    if requested_knowledge_release_id:
        fields["requested_knowledge_release_id"] = requested_knowledge_release_id
    if artifact_name:
        fields["artifact_name"] = artifact_name
    if extra_fields:
        fields.update({str(key): str(value) for key, value in extra_fields.items()})

    failures = validate_update_request_fields(policy, fields)
    if failures:
        raise PrivacyBoundaryError("; ".join(failures))

    url = f"{base_url.rstrip('/')}/updates?{urlencode(fields)}"
    return Request(url, headers={"Accept": "application/json"}, method="GET")


def validate_update_request_fields(
    policy: dict[str, Any],
    fields: dict[str, Any],
) -> list[str]:
    permitted_fields = set(policy["network"].get("permitted_update_request_fields", []))
    prohibited_fields = set(policy["network"].get("prohibited_update_request_fields", []))
    field_names = set(fields)

    failures: list[str] = []
    unknown = sorted(field_names - permitted_fields)
    if unknown:
        failures.append(
            "update request includes unapproved field(s): " + ", ".join(unknown)
        )
    prohibited = sorted(field_names & prohibited_fields)
    if prohibited:
        failures.append(
            "update request includes prohibited field(s): " + ", ".join(prohibited)
        )
    return failures
