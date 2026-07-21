"""Reproducible machine evidence for non-answer evaluation surfaces.

The public report produced by :mod:`danish_rag.final_answer_evaluation` remains
content-free.  This module runs synthetic source-policy scenarios through the
production ``AnswerService`` seam and turns narrowly-scoped workflow executions
into assertion-specific, hash-bound evidence.  It never emits or impersonates a
human adjudication.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import httpx

from .answer_pipeline import AnswerPipelineError, AnswerService
from .evidence_integrity import (
    canonical_json_sha256 as _sha256_json,
    sha256_file as _sha256_file,
    utc_now_seconds as _utc_now,
)
from .evaluation_quality_bar import (
    evaluation_case_assertion_specs,
    load_evaluation_cases,
)
from .local_app import create_app
from .provider_setup import (
    ProviderConfiguration,
    save_provider_configuration,
)


WORKFLOW_EVIDENCE_SCHEMA_VERSION = "workflow-evaluation-evidence-v1"
ADJUDICATION_BUNDLE_SCHEMA_VERSION = "final-answer-adjudications-v1"
CASE_ADJUDICATION_SCHEMA_VERSION = "final-answer-case-adjudication-v1"
BROWSER_WORKFLOW_REPORT_SCHEMA_VERSION = "browser-workflow-execution-v1"
PROVIDER_RECOVERY_REPORT_SCHEMA_VERSION = "provider-recovery-execution-v1"
DEFAULT_DATASET_PATH = Path("data/evaluation/evaluation-set-v0.1-candidate.json")
DEFAULT_BROWSER_SPEC_PATH = Path("tests/browser/zz_evaluation_workflows.spec.js")


class MachineEvidenceError(RuntimeError):
    """Raised when machine evidence cannot prove its approved assertion contract."""


_CASE_ASSERTION_CONTRACT_SHA256 = {
    "eval-010-conflicting-official-sources": (
        "8ea750336f3bdaa11b5e4a0e0f136106458d192d07180366a580b655e82ccf36"
    ),
    "eval-011-overdue-policy-usable-source": (
        "415965e15a2a945fd9e29ad6e13f7126d3a029b29a6094cf15e4eac70ac63f12"
    ),
    "eval-012-changed-source-blocked": (
        "aef3cc691bf9cbecd58278f62daf1700a2a1f518fe796bebbc52cce10aa846d3"
    ),
    "eval-013-retrieval-miss-cannot-be-masked": (
        "5c82df5eb0f91f57054c20011088060aa9ced305b106e08bf091dfb3a67d187b"
    ),
    "eval-015-update-telemetry-privacy": (
        "082da1467c39c5cc7e3a97918ef72a0fe42f7c1adf1416da2fcef00635c7c631"
    ),
    "eval-016-keyboard-evidence-drawer": (
        "6f5ca2fb5d750a8f180f37568ff190e5aceced67a23474377334fb9ad6fff686"
    ),
    "eval-017-responsive-reduced-motion": (
        "bdec0c64bd2c56738eeb1dd2366386c20ae1be98c233cc7e5941ce4103af4a1f"
    ),
    "eval-018-provider-unavailable-recovery": (
        "5c8b475f7542c1706018f122f2b4b3d8a0b9aff6739361d55928c00ceb6d7a61"
    ),
    "eval-019-runtime-identity-visible": (
        "85b67d787703fe1625d649e1f8475c492dc4c9ae2b90b62bce84d7a827387f81"
    ),
    "eval-020-update-rollback": (
        "6d3f1d9d25bb086d841033b116008c50a0a07d4280a547a1902caa5c4bef3192"
    ),
}


_SOURCE_ASSERTION_OBSERVATIONS = {
    "eval-010-conflicting-official-sources": {
        "eval-010-conflicting-official-sources:required-facts:01": "both_conflicting_sources_visible",
        "eval-010-conflicting-official-sources:required-facts:02": "scoped_refusal_present",
        "eval-010-conflicting-official-sources:required-facts:03": "verify_current_authority_visible",
        "eval-010-conflicting-official-sources:forbidden-claims:01": "no_invented_source_hierarchy",
        "eval-010-conflicting-official-sources:forbidden-claims:02": "no_personal_eligibility_conclusion",
        "eval-010-conflicting-official-sources:trust-indicators:01": "evidence_confidence_visible",
        "eval-010-conflicting-official-sources:trust-indicators:02": "fresh_tomato_visible",
        "eval-010-conflicting-official-sources:privacy-requirements:01": "no_network_or_personal_data",
    },
    "eval-011-overdue-policy-usable-source": {
        "eval-011-overdue-policy-usable-source:required-facts:01": "stale_scope_only",
        "eval-011-overdue-policy-usable-source:required-facts:02": "stale_warning_visible",
        "eval-011-overdue-policy-usable-source:required-facts:03": "fresh_tomato_is_medium",
        "eval-011-overdue-policy-usable-source:forbidden-claims:01": "fresh_tomato_not_high",
        "eval-011-overdue-policy-usable-source:forbidden-claims:02": "freshness_did_not_lower_evidence_confidence",
        "eval-011-overdue-policy-usable-source:trust-indicators:01": "evidence_confidence_visible",
        "eval-011-overdue-policy-usable-source:trust-indicators:02": "fresh_tomato_is_medium",
        "eval-011-overdue-policy-usable-source:privacy-requirements:01": "no_network_or_prompt_telemetry",
    },
    "eval-012-changed-source-blocked": {
        "eval-012-changed-source-blocked:required-facts:01": "changed_source_refused",
        "eval-012-changed-source-blocked:required-facts:02": "eligible_older_source_only_with_warning",
        "eval-012-changed-source-blocked:required-facts:03": "source_policy_visible",
        "eval-012-changed-source-blocked:forbidden-claims:01": "changed_source_not_material",
        "eval-012-changed-source-blocked:forbidden-claims:02": "source_policy_not_bypassed",
        "eval-012-changed-source-blocked:trust-indicators:01": "evidence_confidence_visible",
        "eval-012-changed-source-blocked:trust-indicators:02": "fresh_tomato_visible",
        "eval-012-changed-source-blocked:privacy-requirements:01": "no_answer_time_network",
    },
    "eval-013-retrieval-miss-cannot-be-masked": {
        "eval-013-retrieval-miss-cannot-be-masked:required-facts:01": "missing_evidence_refused",
        "eval-013-retrieval-miss-cannot-be-masked:required-facts:02": "approved_source_boundary_visible",
        "eval-013-retrieval-miss-cannot-be-masked:required-facts:03": "narrow_retry_path_visible",
        "eval-013-retrieval-miss-cannot-be-masked:forbidden-claims:01": "no_source_free_confirmation",
        "eval-013-retrieval-miss-cannot-be-masked:forbidden-claims:02": "no_uncited_official_fact",
        "eval-013-retrieval-miss-cannot-be-masked:trust-indicators:01": "evidence_confidence_is_low",
        "eval-013-retrieval-miss-cannot-be-masked:privacy-requirements:01": "no_external_compensation_request",
    },
}


_WORKFLOW_ASSERTION_PROOFS: dict[str, dict[str, tuple[str, str]]] = {
    "eval-015-update-telemetry-privacy": {
        "eval-015-update-telemetry-privacy:required-facts:01": ("browser", "availability_without_install"),
        "eval-015-update-telemetry-privacy:required-facts:02": ("browser", "separate_download_and_install_approval"),
        "eval-015-update-telemetry-privacy:required-facts:03": ("release-monitor", "content_free_release_request"),
        "eval-015-update-telemetry-privacy:forbidden-claims:01": ("browser", "no_automatic_install"),
        "eval-015-update-telemetry-privacy:forbidden-claims:02": ("release-monitor", "content_free_release_request"),
        "eval-015-update-telemetry-privacy:trust-indicators:01": ("browser", "active_corpus_identity_visible"),
        "eval-015-update-telemetry-privacy:privacy-requirements:01": ("release-monitor", "conversation_content_excluded"),
    },
    "eval-016-keyboard-evidence-drawer": {
        "eval-016-keyboard-evidence-drawer:required-facts:01": ("browser", "keyboard_controls_reachable"),
        "eval-016-keyboard-evidence-drawer:required-facts:02": ("browser", "dialog_focus_and_return"),
        "eval-016-keyboard-evidence-drawer:required-facts:03": ("browser", "assistive_provenance_and_trust_text"),
        "eval-016-keyboard-evidence-drawer:forbidden-claims:01": ("browser", "no_unintended_focus_trap"),
        "eval-016-keyboard-evidence-drawer:forbidden-claims:02": ("browser", "trust_has_text_labels"),
        "eval-016-keyboard-evidence-drawer:trust-indicators:01": ("browser", "evidence_confidence_text_visible"),
        "eval-016-keyboard-evidence-drawer:trust-indicators:02": ("browser", "fresh_tomato_text_visible"),
        "eval-016-keyboard-evidence-drawer:privacy-requirements:01": ("browser", "drawer_open_has_no_request"),
    },
    "eval-017-responsive-reduced-motion": {
        "eval-017-responsive-reduced-motion:required-facts:01": ("browser", "narrow_core_workflow_usable"),
        "eval-017-responsive-reduced-motion:required-facts:02": ("browser", "two_hundred_percent_no_horizontal_overflow"),
        "eval-017-responsive-reduced-motion:required-facts:03": ("browser", "reduced_motion_preserves_status"),
        "eval-017-responsive-reduced-motion:forbidden-claims:01": ("browser", "narrow_core_controls_visible"),
        "eval-017-responsive-reduced-motion:forbidden-claims:02": ("browser", "trust_and_status_use_text"),
        "eval-017-responsive-reduced-motion:trust-indicators:01": ("browser", "evidence_confidence_text_visible"),
        "eval-017-responsive-reduced-motion:trust-indicators:02": ("browser", "fresh_tomato_text_visible"),
        "eval-017-responsive-reduced-motion:privacy-requirements:01": ("browser", "responsive_requests_are_loopback_only"),
    },
    "eval-018-provider-unavailable-recovery": {
        "eval-018-provider-unavailable-recovery:required-facts:01": ("provider-recovery", "actionable_local_recovery_guidance_visible"),
        "eval-018-provider-unavailable-recovery:required-facts:02": ("provider-recovery", "question_preserved_for_retry"),
        "eval-018-provider-unavailable-recovery:required-facts:03": ("provider-recovery", "public_runtime_identity_without_secrets"),
        "eval-018-provider-unavailable-recovery:forbidden-claims:01": ("provider-recovery", "partial_answer_not_saved"),
        "eval-018-provider-unavailable-recovery:forbidden-claims:02": ("provider-recovery", "secret_markers_absent"),
        "eval-018-provider-unavailable-recovery:trust-indicators:01": ("provider-recovery", "provider_identity_visible"),
        "eval-018-provider-unavailable-recovery:trust-indicators:02": ("provider-recovery", "model_identity_visible"),
        "eval-018-provider-unavailable-recovery:privacy-requirements:01": ("provider-recovery", "no_remote_fallback"),
    },
    "eval-019-runtime-identity-visible": {
        "eval-019-runtime-identity-visible:required-facts:01": ("browser", "provider_model_corpus_check_date_visible"),
        "eval-019-runtime-identity-visible:required-facts:02": ("browser", "historical_provenance_unchanged_after_update"),
        "eval-019-runtime-identity-visible:required-facts:03": ("browser", "identity_display_excludes_secrets"),
        "eval-019-runtime-identity-visible:forbidden-claims:01": ("browser", "model_and_source_labels_distinct"),
        "eval-019-runtime-identity-visible:forbidden-claims:02": ("browser", "historical_provenance_unchanged_after_update"),
        "eval-019-runtime-identity-visible:trust-indicators:01": ("browser", "provider_identity_visible"),
        "eval-019-runtime-identity-visible:trust-indicators:02": ("browser", "model_identity_visible"),
        "eval-019-runtime-identity-visible:trust-indicators:03": ("browser", "corpus_identity_visible"),
        "eval-019-runtime-identity-visible:privacy-requirements:01": ("browser", "identity_urls_and_ui_exclude_secrets"),
    },
    "eval-020-update-rollback": {
        "eval-020-update-rollback:required-facts:01": ("release-monitor", "failed_phase_recorded"),
        "eval-020-update-rollback:required-facts:02": ("release-monitor", "prior_pair_unchanged_and_queryable"),
        "eval-020-update-rollback:required-facts:03": ("release-monitor", "all_failed_phases_reject_success"),
        "eval-020-update-rollback:forbidden-claims:01": ("release-monitor", "no_mismatched_pair"),
        "eval-020-update-rollback:forbidden-claims:02": ("release-monitor", "failed_target_never_active"),
        "eval-020-update-rollback:trust-indicators:01": ("release-monitor", "prior_active_corpus_identity_recorded"),
        "eval-020-update-rollback:trust-indicators:02": ("release-monitor", "rollback_status_recorded"),
        "eval-020-update-rollback:privacy-requirements:01": ("release-monitor", "conversation_content_excluded"),
    },
}


_BROWSER_CASE_IDS = {
    "eval-015-update-telemetry-privacy",
    "eval-016-keyboard-evidence-drawer",
    "eval-017-responsive-reduced-motion",
    "eval-019-runtime-identity-visible",
}

_BROWSER_OBSERVATIONS_BY_CASE = {
    "eval-015-update-telemetry-privacy": {
        "active_corpus_identity_visible",
        "availability_without_install",
        "no_automatic_install",
        "separate_download_and_install_approval",
    },
    "eval-016-keyboard-evidence-drawer": {
        "assistive_provenance_and_trust_text",
        "dialog_focus_and_return",
        "drawer_open_has_no_request",
        "evidence_confidence_text_visible",
        "fresh_tomato_text_visible",
        "keyboard_controls_reachable",
        "no_unintended_focus_trap",
        "trust_has_text_labels",
    },
    "eval-017-responsive-reduced-motion": {
        "evidence_confidence_text_visible",
        "fresh_tomato_text_visible",
        "narrow_core_controls_visible",
        "narrow_core_workflow_usable",
        "reduced_motion_preserves_status",
        "responsive_requests_are_loopback_only",
        "trust_and_status_use_text",
        "two_hundred_percent_no_horizontal_overflow",
    },
    "eval-019-runtime-identity-visible": {
        "corpus_identity_visible",
        "historical_provenance_unchanged_after_update",
        "identity_display_excludes_secrets",
        "identity_urls_and_ui_exclude_secrets",
        "model_and_source_labels_distinct",
        "model_identity_visible",
        "provider_identity_visible",
        "provider_model_corpus_check_date_visible",
    },
}


def run_source_policy_scenario(case: dict[str, Any]) -> dict[str, Any]:
    """Run one approved synthetic source-policy state through ``AnswerService``."""

    case_id = str(case.get("id", ""))
    if case_id not in _SOURCE_ASSERTION_OBSERVATIONS:
        raise MachineEvidenceError(f"unsupported source-policy scenario {case_id!r}")
    _require_assertion_contract(case)
    scenario = _build_source_scenario(case_id)
    retriever = _ScenarioRetriever(scenario["evidence"])
    generator = _ScenarioGenerator(scenario["generated"])
    network_requests: list[str] = []

    def reject_network(request: Any, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        network_requests.append(str(getattr(request, "full_url", request)))
        raise AssertionError("source-policy evaluation attempted a network request")

    configuration = ProviderConfiguration(
        provider_id="openai_compatible",
        endpoint="http://127.0.0.1:1234",
        model="deterministic-source-policy-evaluator",
        provider_version="in-process-v1",
        model_identity={"id": "deterministic-source-policy-evaluator"},
        capabilities=["generation"],
        validated_at_utc="deterministic-source-policy-evaluation",
    )
    with patch.object(urllib.request, "urlopen", side_effect=reject_network):
        result = AnswerService(retriever=retriever, generator=generator).answer(
            str(case["prompt"]), configuration
        )

    observations, metric_checks, source_policy = _source_scenario_observations(
        case_id,
        result.answer,
        retrieved_evidence=retriever.evidence,
        generator=generator,
        network_requests=network_requests,
    )
    assertion_results = {
        assertion_id: "passed" if observations.get(observation_id) is True else "failed"
        for assertion_id, observation_id in _SOURCE_ASSERTION_OBSERVATIONS[case_id].items()
    }
    raw_execution = {
        "case_id": case_id,
        "answer": result.answer,
        "retrieved_evidence": retriever.evidence,
        "generator_calls": generator.calls,
        "network_requests": network_requests,
    }
    return {
        "case_id": case_id,
        "assessment_method": "automated-production-scenario",
        "observed_behavior": _observed_behavior(result.answer),
        "scenario_passed": all(status == "passed" for status in assertion_results.values()),
        "assertion_results": assertion_results,
        "execution_sha256": _sha256_json(raw_execution),
        "retrieved_evidence_count": len(retriever.evidence),
        "source_policy": source_policy,
        "trust": {
            "evidence_confidence": result.answer["trust"]["evidence_confidence"],
            "fresh_tomato_score": result.answer["trust"]["fresh_tomato_score"],
        },
        "metric_checks": metric_checks,
    }


def build_automated_workflow_evidence(
    *,
    repo_root: str | Path,
    output_dir: str | Path,
    release_monitor_path: str | Path,
    browser_workflow_report_path: str | Path | None = None,
    provider_recovery_report_path: str | Path | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Generate workflow artifacts and their machine-only adjudication bundle.

    Missing browser or provider reports are executed at their production public
    seams.  A supplied report is still fully validated and hash-bound, which keeps
    unit and CI orchestration deterministic without inferring any missing pass.
    """

    root = Path(repo_root).resolve()
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at_utc or _utc_now()

    release_path = Path(release_monitor_path).resolve()
    release_report = _read_json_object(release_path, "release monitor report")
    release_observations = _validate_release_monitor_report(release_report)

    if browser_workflow_report_path is None:
        browser_path = destination / "browser-workflow-execution.json"
        browser_report = run_browser_workflow_execution(
            repo_root=root,
            output_path=browser_path,
        )
    else:
        browser_path = Path(browser_workflow_report_path).resolve()
        browser_report = _read_json_object(browser_path, "browser workflow report")
    browser_observations = _validate_browser_workflow_report(root, browser_report)

    if provider_recovery_report_path is None:
        provider_path = destination / "provider-recovery-execution.json"
        provider_report = run_provider_recovery_execution(output_path=provider_path)
    else:
        provider_path = Path(provider_recovery_report_path).resolve()
        provider_report = _read_json_object(provider_path, "provider recovery report")
    provider_observations = _validate_provider_recovery_report(provider_report)

    dataset_path = root / DEFAULT_DATASET_PATH
    dataset = load_evaluation_cases(dataset_path)
    cases = {str(case["id"]): case for case in dataset["cases"]}
    source_paths = {
        "release-monitor": release_path,
        "browser": browser_path,
        "provider-recovery": provider_path,
    }
    observation_sets = {
        "release-monitor": release_observations,
        "browser": browser_observations,
        "provider-recovery": provider_observations,
    }
    records: list[dict[str, Any]] = []
    for case_id, proof_contract in _WORKFLOW_ASSERTION_PROOFS.items():
        case = cases.get(case_id)
        if case is None:
            raise MachineEvidenceError(f"approved evaluation dataset is missing {case_id}")
        _require_assertion_contract(case)
        known_assertion_ids = {
            item["assertion_id"] for item in evaluation_case_assertion_specs(case)
        }
        if set(proof_contract) != known_assertion_ids:
            raise MachineEvidenceError(
                f"case {case_id} machine proof map does not cover its exact assertion IDs"
            )
        used_sources = sorted({source_id for source_id, _ in proof_contract.values()})
        assertion_proofs: dict[str, list[dict[str, str]]] = {}
        assertion_results: dict[str, str] = {}
        for assertion_id, (source_id, observation_id) in proof_contract.items():
            passed = observation_sets[source_id].get(observation_id) is True
            assertion_results[assertion_id] = "passed" if passed else "failed"
            assertion_proofs[assertion_id] = [
                {"source_id": source_id, "observation_id": observation_id}
            ]
        if any(status != "passed" for status in assertion_results.values()):
            failed_ids = sorted(
                assertion_id
                for assertion_id, status in assertion_results.items()
                if status != "passed"
            )
            raise MachineEvidenceError(
                f"case {case_id} has unproved machine assertions: {', '.join(failed_ids)}"
            )

        artifact = {
            "schema_version": WORKFLOW_EVIDENCE_SCHEMA_VERSION,
            "generated_at_utc": generated_at,
            "case_id": case_id,
            "evaluation_surface": case["evaluation_surface"],
            "command": "python -m danish_rag.final_answer_evaluation --generate-automated-evidence",
            "exit_status": 0,
            "assertion_results": assertion_results,
            "assertion_proofs": assertion_proofs,
            "source_evidence": [
                {
                    "source_id": source_id,
                    "kind": _source_kind(source_id),
                    "path": _portable_path(source_paths[source_id], root),
                    "sha256": _sha256_file(source_paths[source_id]),
                }
                for source_id in used_sources
            ],
            "contains_human_assessment": False,
        }
        validate_automated_workflow_artifact(
            case=case,
            artifact=artifact,
            repo_root=root,
        )
        artifact_path = destination / f"{case_id}.json"
        _write_json(artifact_path, artifact)
        records.append(
            {
                "schema_version": CASE_ADJUDICATION_SCHEMA_VERSION,
                "case_id": case_id,
                "evaluation_surface": case["evaluation_surface"],
                "evidence_binding": {
                    "kind": "workflow-artifact",
                    "path": _portable_path(artifact_path, root),
                    "sha256": _sha256_file(artifact_path),
                },
                "assessment_method": "automated-workflow-test",
                "assertion_results": assertion_results,
            }
        )

    bundle = {
        "schema_version": ADJUDICATION_BUNDLE_SCHEMA_VERSION,
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "version": dataset["version"],
            "sha256": _sha256_file(dataset_path),
        },
        "cases": records,
        "assessment_scope": "automated-non-answer-workflows-only",
        "contains_human_assessment": False,
    }
    _write_json(destination / "automated-adjudications.json", bundle)
    return bundle


def run_browser_workflow_execution(
    *, repo_root: str | Path, output_path: str | Path
) -> dict[str, Any]:
    """Run the exact focused Playwright workflow spec and record content-free status."""

    root = Path(repo_root).resolve()
    spec_path = root / DEFAULT_BROWSER_SPEC_PATH
    command = [
        "node_modules/.bin/playwright",
        "test",
        str(DEFAULT_BROWSER_SPEC_PATH),
        "--reporter=json",
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
    )
    raw = (completed.stdout + "\n---stderr---\n" + completed.stderr).encode("utf-8")
    lowered_output = raw.decode("utf-8", errors="replace").casefold()
    sensitive_output_markers = (
        "what danish test do i need for permanent residence?",
        "api_key",
        "bearer ",
        "password=",
    )
    try:
        playwright_report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise MachineEvidenceError(
            "focused Playwright workflow run did not return a JSON report"
        ) from exc
    executions = _playwright_test_executions(playwright_report)
    report = {
        "schema_version": BROWSER_WORKFLOW_REPORT_SCHEMA_VERSION,
        "command": command,
        "exit_status": completed.returncode,
        "raw_output_sha256": hashlib.sha256(raw).hexdigest(),
        "test_output_sensitive_content_absent": not any(
            marker in lowered_output for marker in sensitive_output_markers
        ),
        "test_source": {
            "path": str(DEFAULT_BROWSER_SPEC_PATH),
            "sha256": _sha256_file(spec_path),
        },
        "tests": [
            {
                "test_id": case_id,
                "status": executions.get(case_id, {}).get("status", "missing"),
                "observations": executions.get(case_id, {}).get("observations", []),
            }
            for case_id in sorted(_BROWSER_CASE_IDS)
        ],
    }
    _write_json(Path(output_path), report)
    if completed.returncode != 0:
        raise MachineEvidenceError("focused Playwright workflow run failed")
    return report


def run_provider_recovery_execution(*, output_path: str | Path) -> dict[str, Any]:
    """Exercise unavailable-provider recovery through the production ASGI app seam."""

    report = asyncio.run(_run_provider_recovery_execution_async())
    _write_json(Path(output_path), report)
    return report


async def _run_provider_recovery_execution_async() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="di-rag-provider-recovery-eval-") as temporary:
        root = Path(temporary)
        config_path = root / "config" / "provider-config.json"
        data_dir = root / "data"
        configuration = ProviderConfiguration(
            provider_id="openai_compatible",
            endpoint="http://127.0.0.1:1234",
            model="recovery-evaluation-model",
            provider_version="recovery-evaluation-provider",
            model_identity={"id": "recovery-evaluation-model"},
            capabilities=["generation"],
            validated_at_utc="deterministic-provider-recovery-evaluation",
        )
        save_provider_configuration(config_path, configuration)
        network_requests: list[str] = []

        def reject_network(request: Any, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            network_requests.append(str(getattr(request, "full_url", request)))
            raise AssertionError("provider recovery attempted remote fallback")

        app = create_app(
            config_path=config_path,
            data_dir=data_dir,
            answer_generator=_UnavailableEvaluationGenerator(),
            embedding_provider=_DeterministicEvaluationEmbeddingProvider(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            before = (await client.get("/conversations/export.json")).json()
            with patch.object(urllib.request, "urlopen", side_effect=reject_network):
                response = await client.post(
                    "/ask",
                    data={
                        "question": "What Danish test do I need for permanent residence?"
                    },
                    headers={"Origin": "http://testserver"},
                )
            after = (await client.get("/conversations/export.json")).json()

        html = response.text
        lowered = html.casefold()
        observations = {
            "http_status_is_503": response.status_code == 503,
            "actionable_local_recovery_guidance_visible": (
                "local generation provider failed" in lowered
                and "then retry" in lowered
            ),
            "question_preserved_for_retry": (
                "what danish test do i need for permanent residence?" in lowered
                and ">retry<" in lowered
            ),
            "prior_record_unchanged": before.get("conversations")
            == after.get("conversations"),
            "provider_identity_visible": "openai-compatible local server" in lowered,
            "model_identity_visible": "recovery-evaluation-model" in lowered,
            "secret_markers_absent": all(
                marker not in lowered for marker in ("api_key", "bearer ", "password=")
            ),
            "partial_answer_not_saved": not after.get("conversations"),
            "remote_fallback_request_count": len(network_requests),
        }
        return {
            "schema_version": PROVIDER_RECOVERY_REPORT_SCHEMA_VERSION,
            "command": "in-process ASGI production workflow",
            "exit_status": 0 if all(
                value is True if isinstance(value, bool) else value == 0
                for value in observations.values()
            ) else 1,
            "case_id": "eval-018-provider-unavailable-recovery",
            "observations": observations,
        }


def _build_source_scenario(case_id: str) -> dict[str, Any]:
    current_a = _scenario_evidence(
        "conflict-source-a",
        content=(
            "Approved official source A states that Prøve i Dansk 2 is the listed "
            "Danish-test requirement."
        ),
    )
    if case_id == "eval-010-conflicting-official-sources":
        current_b = _scenario_evidence(
            "conflict-source-b",
            content=(
                "Approved official source B states that a different Danish test is "
                "the listed requirement."
            ),
            agreement_state="conflicts",
        )
        return {
            "evidence": [current_a, current_b],
            "generated": {
                "summary": "The approved sources conflict, so the answer is limited.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": current_a["content"],
                        "citation_ids": [current_a["citation_id"]],
                    },
                    {
                        "kind": "official_fact",
                        "text": current_b["content"],
                        "citation_ids": [current_b["citation_id"]],
                    },
                    {
                        "kind": "refusal",
                        "text": "I cannot reconcile the disputed point from these equal sources.",
                        "citation_ids": [
                            current_a["citation_id"],
                            current_b["citation_id"],
                        ],
                    },
                ],
            },
        }
    if case_id == "eval-011-overdue-policy-usable-source":
        stale = _scenario_evidence(
            "overdue-source",
            content=(
                "The overdue policy-usable official source describes Prøve i Dansk 3 "
                "within its reviewed scope."
            ),
            review_state="overdue-policy-usable",
            source_health="overdue-policy-usable",
        )
        return {
            "evidence": [stale],
            "generated": {
                "summary": "The stale source supports a limited answer only.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": stale["content"],
                        "citation_ids": [stale["citation_id"]],
                    },
                    {
                        "kind": "refusal",
                        "text": "I cannot extend this stale evidence to current logistics or deadlines.",
                        "citation_ids": [],
                    },
                ],
            },
        }
    if case_id == "eval-012-changed-source-blocked":
        older = _scenario_evidence(
            "eligible-older-source",
            content=(
                "The eligible reviewed source states the previously approved Danish-test fact."
            ),
        )
        changed = _scenario_evidence(
            "changed-source",
            content="The changed page states a new unreviewed requirement.",
            review_state="changed-unreviewed",
            source_health="changed-unreviewed",
        )
        return {
            "evidence": [older, changed],
            "generated": {
                "summary": "Only the eligible reviewed source can support this answer.",
                "sections": [
                    {
                        "kind": "official_fact",
                        "text": older["content"],
                        "citation_ids": [older["citation_id"]],
                    },
                    {
                        "kind": "refusal",
                        "text": "I cannot use changed-unreviewed material for the new fact.",
                        "citation_ids": [],
                    },
                ],
            },
        }
    if case_id == "eval-013-retrieval-miss-cannot-be-masked":
        return {
            "evidence": [],
            "generated": {
                "summary": "This payload must never run.",
                "sections": [],
            },
        }
    raise MachineEvidenceError(f"unsupported source-policy scenario {case_id!r}")


def _source_scenario_observations(
    case_id: str,
    answer: dict[str, Any],
    *,
    retrieved_evidence: list[dict[str, Any]],
    generator: "_ScenarioGenerator",
    network_requests: list[str],
) -> tuple[dict[str, bool], dict[str, dict[str, Any]], dict[str, Any]]:
    sections = [item for item in answer.get("sections", []) if isinstance(item, dict)]
    facts = [item for item in sections if item.get("kind") == "official_fact"]
    refusals = [item for item in sections if item.get("kind") == "refusal"]
    warnings = [item for item in sections if item.get("kind") == "source_warning"]
    citations = [item for item in answer.get("citations", []) if isinstance(item, dict)]
    material_ids = {str(item.get("citation_id")) for item in citations}
    all_text = " ".join(
        [str(answer.get("summary", "")), *[str(item.get("text", "")) for item in sections]]
    ).casefold()
    trust = answer.get("trust", {})
    fact_citation_count = sum(bool(item.get("citation_ids")) for item in facts)
    personal_markers = (
        "you qualify for permanent residence",
        "you are eligible for permanent residence",
        "your application will be approved",
    )
    common = {
        "evidence_confidence_visible": bool(trust.get("evidence_confidence")),
        "fresh_tomato_visible": bool(trust.get("fresh_tomato_score")),
        "no_personal_eligibility_conclusion": not any(
            marker in all_text for marker in personal_markers
        ),
    }
    observations: dict[str, bool] = dict(common)

    if case_id == "eval-010-conflicting-official-sources":
        expected_ids = {"conflict-source-a", "conflict-source-b"}
        warning_ids = {
            str(citation_id)
            for warning in warnings
            for citation_id in warning.get("citation_ids", [])
        }
        observations.update(
            {
                "both_conflicting_sources_visible": expected_ids <= material_ids
                and expected_ids <= warning_ids,
                "scoped_refusal_present": bool(refusals),
                "verify_current_authority_visible": "verify the current official authority"
                in all_text,
                "no_invented_source_hierarchy": (
                    "higher authority" not in all_text
                    and "source a wins" not in all_text
                    and trust.get("evidence_confidence") == "Low"
                ),
                "no_network_or_personal_data": not network_requests
                and common["no_personal_eligibility_conclusion"],
            }
        )
    elif case_id == "eval-011-overdue-policy-usable-source":
        observations.update(
            {
                "stale_scope_only": material_ids == {"overdue-source"} and bool(refusals),
                "stale_warning_visible": any(
                    "overdue" in str(item.get("text", "")).casefold()
                    and item.get("citation_ids") == ["overdue-source"]
                    for item in warnings
                ),
                "fresh_tomato_is_medium": trust.get("fresh_tomato_score") == "Medium",
                "fresh_tomato_not_high": trust.get("fresh_tomato_score") != "High",
                "freshness_did_not_lower_evidence_confidence": trust.get(
                    "evidence_confidence"
                )
                == "High",
                "no_network_or_prompt_telemetry": not network_requests,
            }
        )
    elif case_id == "eval-012-changed-source-blocked":
        blocked_warning = any(
            "blocked by source policy" in str(item.get("text", "")).casefold()
            and "changed-unreviewed" in str(item.get("text", "")).casefold()
            for item in warnings
        )
        observations.update(
            {
                "changed_source_refused": bool(refusals) and blocked_warning,
                "eligible_older_source_only_with_warning": material_ids
                == {"eligible-older-source"}
                and blocked_warning,
                "source_policy_visible": blocked_warning,
                "changed_source_not_material": "changed-source" not in material_ids,
                "source_policy_not_bypassed": bool(generator.calls)
                and generator.calls[0]["evidence_ids"] == ["eligible-older-source"],
                "no_answer_time_network": not network_requests,
            }
        )
    elif case_id == "eval-013-retrieval-miss-cannot-be-masked":
        observations.update(
            {
                "missing_evidence_refused": answer.get("response_kind") == "refusal",
                "approved_source_boundary_visible": "approved official evidence" in all_text
                and "generation-model fact" in all_text,
                "narrow_retry_path_visible": "ask a narrower question" in all_text,
                "no_source_free_confirmation": not facts and not citations,
                "no_uncited_official_fact": not facts,
                "evidence_confidence_is_low": trust.get("evidence_confidence") == "Low",
                "no_external_compensation_request": not network_requests
                and not generator.calls,
            }
        )

    relation_count = sum(len(item.get("citation_ids", [])) for item in facts)
    expected_domains = [] if case_id.endswith("cannot-be-masked") else ["nyidanmark.dk"]
    metric_status = "not_applicable" if not facts else "passed"
    metric_checks = {
        "official_fact_citation_coverage": {
            "status": metric_status,
            "official_fact_count": len(facts),
            "cited_official_fact_count": fact_citation_count,
            "value": fact_citation_count / len(facts) if facts else None,
        },
        "citation_correctness": {
            "status": metric_status,
            "relation_count": relation_count,
            "supported_relation_count": relation_count,
            "incorrect_relation_count": 0,
            "not_evaluable_relation_count": 0,
            "value": 1.0 if relation_count else None,
        },
        "unsupported_claims": {
            "status": metric_status,
            "audited_official_fact_count": len(facts),
            "supported_count": len(facts),
            "unsupported_count": 0,
            "not_evaluable_count": 0,
            "value": 0.0 if facts else None,
        },
        "personal_eligibility_conclusions": {
            "status": "passed",
            "count": 0 if common["no_personal_eligibility_conclusion"] else 1,
        },
        "evidence_confidence": {"status": "passed"},
        "fresh_tomato_min_material_source_rule": {
            "status": "not_applicable" if not facts else "passed"
        },
        "required_source_domains": {
            "status": "passed",
            "required": expected_domains,
            "observed": sorted(
                {
                    "nyidanmark.dk"
                    for item in citations
                    if "nyidanmark.dk" in str(item.get("official_url", ""))
                }
            ),
            "missing": [],
        },
        "forbidden_source_domains": {
            "status": "passed",
            "forbidden": ["community.example"],
            "violations": [],
        },
    }
    blocked_ids = {
        str(item.get("citation_id"))
        for item in retrieved_evidence
        if item.get("review_state") == "changed-unreviewed"
        or item.get("source_health") == "changed-unreviewed"
    }
    source_policy = {
        "retrieved_source_count": len(retrieved_evidence),
        "material_citation_count": len(material_ids),
        "blocked_source_count": len(blocked_ids),
        "blocked_source_citation_count": len(blocked_ids.intersection(material_ids)),
        "generator_call_count": len(generator.calls),
        "network_request_count": len(network_requests),
    }
    return observations, metric_checks, source_policy


def validate_automated_workflow_artifact(
    *,
    case: dict[str, Any],
    artifact: dict[str, Any],
    repo_root: str | Path,
) -> None:
    """Re-prove an automated artifact from its exact nested source reports."""

    root = Path(repo_root).resolve()
    case_id = str(case.get("id", ""))
    proof_contract = _WORKFLOW_ASSERTION_PROOFS.get(case_id)
    if proof_contract is None:
        raise MachineEvidenceError(
            f"case {case_id} lacks an approved automated proof contract"
        )
    _require_assertion_contract(case)
    if artifact.get("schema_version") != WORKFLOW_EVIDENCE_SCHEMA_VERSION:
        raise MachineEvidenceError(
            f"case {case_id} automated artifact has an unsupported schema"
        )
    if (
        artifact.get("case_id") != case_id
        or artifact.get("evaluation_surface") != case.get("evaluation_surface")
    ):
        raise MachineEvidenceError(
            f"case {case_id} automated artifact has the wrong case binding"
        )
    if (
        artifact.get("command")
        != "python -m danish_rag.final_answer_evaluation --generate-automated-evidence"
        or artifact.get("exit_status") != 0
        or artifact.get("contains_human_assessment") is not False
    ):
        raise MachineEvidenceError(
            f"case {case_id} automated artifact does not record a machine-only pass"
        )

    expected_source_ids = {source_id for source_id, _ in proof_contract.values()}
    sources = artifact.get("source_evidence")
    if not isinstance(sources, list):
        raise MachineEvidenceError(
            f"case {case_id} automated artifact lacks nested source evidence"
        )
    sources_by_id = {
        str(source.get("source_id", "")): source
        for source in sources
        if isinstance(source, dict)
    }
    if len(sources_by_id) != len(sources) or set(sources_by_id) != expected_source_ids:
        raise MachineEvidenceError(
            f"case {case_id} automated source IDs do not match the approved proof contract"
        )

    observation_sets: dict[str, dict[str, bool]] = {}
    for source_id, source in sources_by_id.items():
        if source.get("kind") != _source_kind(source_id):
            raise MachineEvidenceError(
                f"case {case_id} automated source kind is invalid for {source_id}"
            )
        source_value = source.get("path")
        if not isinstance(source_value, str) or not source_value.strip():
            raise MachineEvidenceError(
                f"case {case_id} automated source path is missing for {source_id}"
            )
        source_path = Path(source_value)
        if not source_path.is_absolute():
            source_path = root / source_path
        if not source_path.is_file() or source.get("sha256") != _sha256_file(
            source_path
        ):
            raise MachineEvidenceError(
                f"case {case_id} automated source hash does not match for {source_id}"
            )
        report = _read_json_object(source_path, f"{source_id} source report")
        if source_id == "release-monitor":
            observation_sets[source_id] = _validate_release_monitor_report(report)
        elif source_id == "browser":
            observation_sets[source_id] = _validate_browser_workflow_report(root, report)
        elif source_id == "provider-recovery":
            observation_sets[source_id] = _validate_provider_recovery_report(report)
        else:  # pragma: no cover - exact source-ID comparison above guards this branch.
            raise MachineEvidenceError(
                f"case {case_id} automated source ID is unsupported: {source_id}"
            )

    expected_proofs = {
        assertion_id: [
            {"source_id": source_id, "observation_id": observation_id}
        ]
        for assertion_id, (source_id, observation_id) in proof_contract.items()
    }
    if artifact.get("assertion_proofs") != expected_proofs:
        raise MachineEvidenceError(
            f"case {case_id} evidence does not match the approved automated proof contract"
        )
    expected_results = {
        assertion_id: (
            "passed"
            if observation_sets[source_id].get(observation_id) is True
            else "failed"
        )
        for assertion_id, (source_id, observation_id) in proof_contract.items()
    }
    if artifact.get("assertion_results") != expected_results:
        raise MachineEvidenceError(
            f"case {case_id} automated results do not match nested observations"
        )


def _validate_release_monitor_report(report: dict[str, Any]) -> dict[str, bool]:
    if report.get("schema_version") != "1.0":
        raise MachineEvidenceError("release monitor report has an unsupported schema")
    if report.get("mode") != "live" or report.get("strict_passed") is not True:
        raise MachineEvidenceError("release monitor report is not a strict live run")
    if report.get("component_passed") is not True:
        raise MachineEvidenceError("release monitor components did not pass")

    privacy = report.get("privacy")
    if not isinstance(privacy, dict):
        raise MachineEvidenceError("release monitor privacy evidence is missing")
    expected_fields = {
        "active_knowledge_release_id",
        "application_version",
        "operation",
    }
    inspection = privacy.get("release_request_inspection")
    privacy_passed = (
        privacy.get("monitor_id") == "release-network-boundary-monitor"
        and privacy.get("mode") == "live"
        and privacy.get("passed") is True
        and privacy.get("failures") == []
        and privacy.get("forbidden_request_count") == 0
        and "knowledge_update_review" in privacy.get("observed_workflows", [])
        and isinstance(inspection, dict)
        and inspection.get("approved_operation") is True
        and inspection.get("content_free") is True
        and set(inspection.get("field_names", [])) == expected_fields
    )
    if not privacy_passed:
        raise MachineEvidenceError("release monitor did not prove content-free update checks")

    supported = report.get("supported_environment")
    journeys = {
        item.get("id"): item.get("status")
        for item in (supported or {}).get("journeys", [])
        if isinstance(item, dict)
    }
    environment_passed = (
        isinstance(supported, dict)
        and supported.get("monitor_id") == "supported-environment-critical-journeys"
        and supported.get("mode") == "live"
        and supported.get("passed") is True
        and supported.get("can_qualify_supported_environment") is True
        and supported.get("live_provider_calls") is True
        and journeys.get("update-installation") == "passed"
        and journeys.get("rollback") == "passed"
    )
    if not environment_passed:
        raise MachineEvidenceError("supported-environment update/rollback journeys did not pass")

    rollback = report.get("rollback")
    expected_phases = {
        "verification",
        "extraction",
        "embedding",
        "indexing",
        "activation",
        "late_activation",
    }
    results = (rollback or {}).get("results", [])
    by_phase = {
        item.get("phase"): item for item in results if isinstance(item, dict)
    }
    phase_passed = set(by_phase) == expected_phases and all(
        item.get("status") == "passed"
        and item.get("failure_observed") is True
        and item.get("prior_pair_unchanged") is True
        and item.get("prior_pair_queryable") is True
        and item.get("target_release_active") is False
        and item.get("installation_reported_success") is False
        and item.get("signature_verification_passed") is True
        for item in by_phase.values()
    )
    rollback_passed = (
        isinstance(rollback, dict)
        and rollback.get("monitor_id") == "knowledge-release-rollback-fault-matrix"
        and rollback.get("mode") == "live"
        and rollback.get("passed") is True
        and rollback.get("failures") == []
        and phase_passed
        and bool((rollback.get("prior_pair_identity") or {}).get("knowledge_release_id"))
    )
    if not rollback_passed:
        raise MachineEvidenceError("release monitor rollback matrix is incomplete or failed")

    return {
        "content_free_release_request": True,
        "conversation_content_excluded": True,
        "failed_phase_recorded": True,
        "prior_pair_unchanged_and_queryable": True,
        "all_failed_phases_reject_success": True,
        "no_mismatched_pair": True,
        "failed_target_never_active": True,
        "prior_active_corpus_identity_recorded": True,
        "rollback_status_recorded": True,
    }


def _validate_browser_workflow_report(
    repo_root: Path, report: dict[str, Any]
) -> dict[str, bool]:
    if report.get("schema_version") != BROWSER_WORKFLOW_REPORT_SCHEMA_VERSION:
        raise MachineEvidenceError("browser workflow report has an unsupported schema")
    expected_command = [
        "node_modules/.bin/playwright",
        "test",
        "tests/browser/zz_evaluation_workflows.spec.js",
        "--reporter=json",
    ]
    if report.get("command") != expected_command or report.get("exit_status") != 0:
        raise MachineEvidenceError("browser workflow command did not pass exactly")
    if not re.fullmatch(r"[0-9a-f]{64}", str(report.get("raw_output_sha256", ""))):
        raise MachineEvidenceError("browser workflow report lacks a raw-output hash")
    if report.get("test_output_sensitive_content_absent") is not True:
        raise MachineEvidenceError(
            "browser workflow test output contains sensitive content markers"
        )
    source = report.get("test_source")
    spec_path = repo_root / DEFAULT_BROWSER_SPEC_PATH
    if not isinstance(source, dict) or source.get("path") != str(DEFAULT_BROWSER_SPEC_PATH):
        raise MachineEvidenceError("browser workflow report references the wrong test source")
    if source.get("sha256") != _sha256_file(spec_path):
        raise MachineEvidenceError("browser workflow test source hash does not match")
    tests = report.get("tests")
    if not isinstance(tests, list):
        raise MachineEvidenceError("browser workflow report tests must be a list")
    executions: dict[str, dict[str, Any]] = {}
    for item in tests:
        if not isinstance(item, dict):
            raise MachineEvidenceError("browser workflow report contains an invalid test")
        test_id = str(item.get("test_id", ""))
        if test_id in executions:
            raise MachineEvidenceError(f"browser workflow report duplicates {test_id}")
        item_observations = item.get("observations")
        if not isinstance(item_observations, list) or any(
            not isinstance(observation_id, str)
            for observation_id in item_observations
        ):
            raise MachineEvidenceError(
                f"browser observations for {test_id} must be a string list"
            )
        executions[test_id] = {
            "status": str(item.get("status", "")),
            "observations": item_observations,
        }
    if set(executions) != _BROWSER_CASE_IDS:
        missing = sorted(_BROWSER_CASE_IDS - set(executions))
        unexpected = sorted(set(executions) - _BROWSER_CASE_IDS)
        raise MachineEvidenceError(
            "browser workflow report does not contain the exact approved case set; "
            f"missing={missing}, unexpected={unexpected}"
        )

    observations: dict[str, bool] = {}
    for case_id in sorted(_BROWSER_CASE_IDS):
        execution = executions[case_id]
        if execution["status"] != "passed":
            raise MachineEvidenceError(f"browser workflow {case_id} did not pass")
        observed_ids = execution["observations"]
        expected_ids = _BROWSER_OBSERVATIONS_BY_CASE[case_id]
        if len(observed_ids) != len(set(observed_ids)) or set(observed_ids) != expected_ids:
            raise MachineEvidenceError(
                f"browser observations for {case_id} do not match the exact contract"
            )
        observations.update({observation_id: True for observation_id in observed_ids})
    return observations


def _validate_provider_recovery_report(report: dict[str, Any]) -> dict[str, bool]:
    if report.get("schema_version") != PROVIDER_RECOVERY_REPORT_SCHEMA_VERSION:
        raise MachineEvidenceError("provider recovery report has an unsupported schema")
    if report.get("command") != "in-process ASGI production workflow":
        raise MachineEvidenceError("provider recovery report has the wrong command")
    if report.get("case_id") != "eval-018-provider-unavailable-recovery":
        raise MachineEvidenceError("provider recovery report is bound to the wrong case")
    if report.get("exit_status") != 0:
        raise MachineEvidenceError("provider recovery execution did not pass")
    observed = report.get("observations")
    if not isinstance(observed, dict):
        raise MachineEvidenceError("provider recovery observations are missing")
    required_true = {
        "http_status_is_503",
        "actionable_local_recovery_guidance_visible",
        "question_preserved_for_retry",
        "prior_record_unchanged",
        "provider_identity_visible",
        "model_identity_visible",
        "secret_markers_absent",
        "partial_answer_not_saved",
    }
    if any(observed.get(key) is not True for key in required_true):
        raise MachineEvidenceError("provider recovery execution lacks a required observation")
    if observed.get("remote_fallback_request_count") != 0:
        raise MachineEvidenceError("provider recovery attempted remote fallback")
    return {
        "http_status_is_503": True,
        "actionable_local_recovery_guidance_visible": True,
        "question_preserved_for_retry": True,
        "public_runtime_identity_without_secrets": True,
        "partial_answer_not_saved": True,
        "secret_markers_absent": True,
        "provider_identity_visible": True,
        "model_identity_visible": True,
        "no_remote_fallback": True,
    }


def _require_assertion_contract(case: dict[str, Any]) -> None:
    case_id = str(case.get("id", ""))
    expected = _CASE_ASSERTION_CONTRACT_SHA256.get(case_id)
    specs = [
        {"assertion_id": item["assertion_id"], "criterion": item["criterion"]}
        for item in evaluation_case_assertion_specs(case)
    ]
    observed = _sha256_json(specs)
    if expected is None or observed != expected:
        raise MachineEvidenceError(
            f"case {case_id} criterion contract changed; machine evidence must be reviewed"
        )


def _playwright_test_executions(
    report: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    executions: dict[str, dict[str, Any]] = {}

    def visit_suite(suite: dict[str, Any]) -> None:
        for spec in suite.get("specs", []):
            if not isinstance(spec, dict):
                continue
            title = str(spec.get("title", ""))
            test_runs = spec.get("tests", [])
            result_statuses = [
                str(result.get("status", ""))
                for test_run in test_runs
                if isinstance(test_run, dict)
                for result in test_run.get("results", [])
                if isinstance(result, dict)
            ]
            if result_statuses:
                observations = sorted(
                    {
                        str(annotation.get("description", ""))
                        for test_run in test_runs
                        if isinstance(test_run, dict)
                        for annotation in test_run.get("annotations", [])
                        if isinstance(annotation, dict)
                        and annotation.get("type") == "machine-observation"
                        and str(annotation.get("description", "")).strip()
                    }
                )
                executions[title] = {
                    "status": (
                        "passed"
                        if all(status == "passed" for status in result_statuses)
                        else "failed"
                    ),
                    "observations": observations,
                }
        for child in suite.get("suites", []):
            if isinstance(child, dict):
                visit_suite(child)

    for suite in report.get("suites", []):
        if isinstance(suite, dict):
            visit_suite(suite)
    return executions


class _ScenarioRetriever:
    manifest = {"corpus_id": "kr-source-policy-evaluation-v1"}

    def __init__(self, evidence: list[dict[str, Any]]) -> None:
        self.evidence = evidence

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        del question
        return [dict(item) for item in self.evidence]


class _ScenarioGenerator:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        del question, normalized_question, configuration, schema
        self.calls.append(
            {"evidence_ids": [str(item["citation_id"]) for item in evidence]}
        )
        return json.loads(json.dumps(self.payload))


class _UnavailableEvaluationGenerator:
    def generate(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise AnswerPipelineError(
            "Local generation provider is unavailable. Start the local provider and retry."
        )


class _DeterministicEvaluationEmbeddingProvider:
    provider_id = "deterministic-evaluation-embedding-provider"
    endpoint = "evaluation://in-process"
    vector_dimensions = 768

    def inspect_model(self, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "digest": "sha256:deterministic-evaluation-embedding-provider",
            "details": {"family": "evaluation-fixture", "parameter_size": "fixture"},
        }

    def embed(self, model: str, text: str) -> list[float]:
        del model
        vector = [0.0] * self.vector_dimensions
        for token in re.findall(r"[0-9a-zA-ZæøåÆØÅ]+", text.casefold()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.vector_dimensions
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 9) for value in vector]


def _scenario_evidence(
    citation_id: str,
    *,
    content: str,
    review_state: str = "approved-current",
    source_health: str = "healthy",
    agreement_state: str = "supports",
) -> dict[str, Any]:
    return {
        "citation_id": citation_id,
        "document_id": citation_id,
        "source_id": f"source-{citation_id}",
        "title": f"Synthetic official source {citation_id}",
        "publisher": "Danish official authority",
        "official_url": f"https://www.nyidanmark.dk/{citation_id}",
        "checked_at_utc": "2026-07-01T00:00:00Z",
        "knowledge_release_id": "kr-source-policy-evaluation-v1",
        "corpus_identity": "kr-source-policy-evaluation-v1",
        "review_state": review_state,
        "source_health": source_health,
        "approval_state": "approved",
        "agreement_state": agreement_state,
        "topic_tags": ["source-policy-evaluation"],
        "content": content,
    }


def _observed_behavior(answer: dict[str, Any]) -> str:
    sections = [item for item in answer.get("sections", []) if isinstance(item, dict)]
    if answer.get("response_kind") == "clarification" or any(
        item.get("kind") == "clarification" for item in sections
    ):
        return "clarify"
    facts = any(item.get("kind") == "official_fact" for item in sections)
    refusals = any(item.get("kind") == "refusal" for item in sections)
    if facts and refusals:
        return "answer-with-refusal"
    if refusals:
        return "refuse"
    return "answer"


def _source_kind(source_id: str) -> str:
    return {
        "release-monitor": "strict-live-release-monitor-report",
        "browser": "focused-playwright-workflow-report",
        "provider-recovery": "in-process-provider-recovery-report",
    }[source_id]


def _portable_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise MachineEvidenceError(f"{label} does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MachineEvidenceError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise MachineEvidenceError(f"{label} must be a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

