"""Executable release monitors for privacy, rollback, and environment journeys.

The monitor evidence deliberately records identities, field names, and outcomes only.
Question text, generated answer text, retrieved evidence, and conversation identifiers are
used inside temporary local workspaces but never copied into the returned evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from urllib.parse import parse_qs, urlparse

import httpx

from .github_release_client import (
    ArtifactDownloadApproval,
    DEFAULT_REPOSITORY,
    GitHubReleaseClient,
)
from .knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    active_corpus_summary,
    install_knowledge_release,
    verify_knowledge_release,
)
from .local_app import create_app
from .privacy_boundary import (
    build_release_network_request,
    validate_runtime_policy_privacy_boundary,
    validate_update_request_fields,
)
from .provider_setup import ProviderConfiguration, save_provider_configuration
from .retrieval import HybridRetriever
from .runtime_policy import is_loopback_url, load_runtime_policy
from .source_maintenance import build_publishable_knowledge_release
from .supported_environment_monitor import (
    LiveEnvironmentWorkspace,
    execute_live_supported_environment_journeys,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_POLICY_PATH = ROOT / "config" / "runtime-policy.json"
MonitorMode = Literal["fixture", "live"]

WORKFLOW_ORDER = [
    "question",
    "retrieval",
    "generation",
    "evidence_inspection",
    "history",
    "deletion",
    "export",
    "local_indexing",
    "knowledge_update_review",
]

SUPPORTED_ENVIRONMENT_JOURNEY_ORDER = [
    "setup",
    "supported-answer",
    "refusal",
    "evidence-inspection",
    "history-persistence",
    "deletion-export",
    "update-installation",
    "rollback",
]
class ReleaseMonitorError(RuntimeError):
    """Raised when a release monitor cannot execute its public seam."""


class _FixtureEmbeddingProvider:
    provider_id = "fixture-deterministic-embedding-provider"
    endpoint = "fixture://deterministic-embedding-provider"
    vector_dimensions = 768

    def inspect_model(self, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "digest": "sha256:release-monitor-fixture-embedding",
            "details": {"family": "gemma3", "parameter_size": "fixture"},
        }

    def embed(self, model: str, text: str) -> list[float]:
        vector = [0.0] * self.vector_dimensions
        for token in re.findall(r"[0-9a-zA-ZæøåÆØÅ]+", text.casefold()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.vector_dimensions
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 9) for value in vector]


class _FixtureAnswerGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        citation_id = str(evidence[0]["citation_id"])
        return {
            "summary": "Fixture answer grounded in the selected official evidence.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": "A Danish language test can be required.",
                    "citation_ids": [citation_id],
                }
            ],
        }


class _NetworkCallObserver:
    """Observe urllib calls while retaining metadata, never request values."""

    def __init__(
        self,
        *,
        allow_live_loopback: bool,
        external_response_factory: Callable[[Any, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.allow_live_loopback = allow_live_loopback
        self._external_response_factory = external_response_factory
        self.calls: list[dict[str, Any]] = []
        self.observed_workflows: set[str] = set()
        self._active_workflows: tuple[str, ...] = ()
        self._release_approved = False
        self._original_opener_open: Any | None = None

    def __enter__(self) -> "_NetworkCallObserver":
        self._original_opener_open = urllib.request.OpenerDirector.open
        observer = self

        def observed_open(
            opener: urllib.request.OpenerDirector,
            request: Any,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            return observer._opener_open(opener, request, *args, **kwargs)

        urllib.request.OpenerDirector.open = observed_open  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._original_opener_open is not None:
            urllib.request.OpenerDirector.open = self._original_opener_open  # type: ignore[assignment]

    @contextmanager
    def workflow(
        self,
        *workflow_ids: str,
        release_approved: bool = False,
    ) -> Iterator[None]:
        previous_workflows = self._active_workflows
        previous_approval = self._release_approved
        self._active_workflows = tuple(workflow_ids)
        self._release_approved = release_approved
        self.observed_workflows.update(workflow_ids)
        try:
            yield
        finally:
            self._active_workflows = previous_workflows
            self._release_approved = previous_approval

    def _opener_open(
        self,
        opener: urllib.request.OpenerDirector,
        request: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        record = _sanitized_request_record(
            request,
            workflows=self._active_workflows,
            release_approved=self._release_approved,
        )
        self.calls.append(record)
        if record["allowed"] and record["destination_class"] == "loopback":
            if self.allow_live_loopback and self._original_opener_open is not None:
                return self._original_opener_open(opener, request, *args, **kwargs)
        if (
            record["allowed"]
            and record["destination_class"] == "external"
            and self._external_response_factory is not None
        ):
            return self._external_response_factory(request, record)
        raise ReleaseMonitorError("Network boundary blocked an observed request.")


def _sanitized_request_record(
    request: Any,
    *,
    workflows: tuple[str, ...],
    release_approved: bool,
) -> dict[str, Any]:
    url = str(getattr(request, "full_url", request))
    parsed = urlparse(url)
    query_field_names = sorted(parse_qs(parsed.query, keep_blank_values=True))
    body_field_names = _request_body_field_names(getattr(request, "data", None))
    field_names = sorted(set(query_field_names) | set(body_field_names))
    destination_class = "loopback" if is_loopback_url(url) else "external"
    operation = ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query.get("operation"):
        operation = str(query["operation"][0])
    github_operation = _github_release_operation(parsed)
    if not operation:
        operation = github_operation
    release_operation = operation in {
        "knowledge_release_discovery",
        "approved_knowledge_release_artifact_retrieval",
        "project_release_discovery",
    }
    artifact_retrieval = operation == "approved_knowledge_release_artifact_retrieval"
    trusted_github_discovery = (
        github_operation == "knowledge_release_discovery" and not artifact_retrieval
    )
    allowed = destination_class == "loopback" or (
        destination_class == "external"
        and release_operation
        and (release_approved or trusted_github_discovery)
    )
    return {
        "allowed": allowed,
        "destination_class": destination_class,
        "field_names": field_names,
        "header_names": sorted(
            str(key).casefold() for key, _value in getattr(request, "header_items", lambda: [])()
        ),
        "host": parsed.hostname or "",
        "method": str(getattr(request, "method", None) or "GET"),
        "operation": operation,
        "port": parsed.port,
        "scheme": parsed.scheme,
        "release_approved": release_approved,
        "workflows": sorted(workflows),
    }


def _github_release_operation(parsed: Any) -> str:
    owner, repository = DEFAULT_REPOSITORY.split("/", 1)
    path = parsed.path.casefold()
    if (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "api.github.com"
        and path == f"/repos/{owner}/{repository}/releases".casefold()
        and not parsed.query
    ):
        return "knowledge_release_discovery"
    artifact_prefix = f"/{owner}/{repository}/releases/download/".casefold()
    if (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "github.com"
        and path.startswith(artifact_prefix)
        and not parsed.query
    ):
        return "approved_knowledge_release_artifact_retrieval"
    return ""


def _request_body_field_names(data: Any) -> list[str]:
    if not data:
        return []
    try:
        decoded = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    return sorted(str(key) for key in payload)


class _FixtureSocket:
    def settimeout(self, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise TimeoutError("Fixture network timeout expired.")


class _FixtureGitHubNetworkResponse:
    def __init__(self, body: bytes, *, url: str, content_type: str) -> None:
        self._body = io.BytesIO(body)
        self._url = url
        self.status = 200
        self.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": content_type,
        }
        socket = _FixtureSocket()
        self.fp = type("FixtureFP", (), {})()
        self.fp.raw = type("FixtureRaw", (), {"_sock": socket})()

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        self._body.close()


class _FixtureGitHubResponseFactory:
    release_id = "kr-2099-01-01.1"
    asset_id = 81001

    def __init__(self) -> None:
        owner, repository = DEFAULT_REPOSITORY.split("/", 1)
        self.metadata_url = (
            f"https://api.github.com/repos/{owner}/{repository}/releases"
        )
        self.asset_name = f"{self.release_id}.zip"
        self.asset_url = (
            f"https://github.com/{owner}/{repository}/releases/download/"
            f"{self.release_id}/{self.asset_name}"
        )
        self.artifact = b"fixture-signed-knowledge-release-archive"
        artifact_sha256 = hashlib.sha256(self.artifact).hexdigest()
        self.metadata = json.dumps(
            [
                {
                    "id": 71001,
                    "tag_name": self.release_id,
                    "name": "Fixture knowledge release",
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2099-01-01T12:00:00Z",
                    "html_url": (
                        f"https://github.com/{owner}/{repository}/releases/tag/"
                        f"{self.release_id}"
                    ),
                    "assets": [
                        {
                            "id": self.asset_id,
                            "name": self.asset_name,
                            "content_type": "application/zip",
                            "size": len(self.artifact),
                            "state": "uploaded",
                            "browser_download_url": self.asset_url,
                            "digest": f"sha256:{artifact_sha256}",
                        }
                    ],
                }
            ],
            separators=(",", ":"),
        ).encode("utf-8")

    def __call__(self, request: Any, record: dict[str, Any]) -> Any:
        if getattr(request, "data", None) is not None:
            raise ReleaseMonitorError("Fixture GitHub request unexpectedly contained a body.")
        method = str(getattr(request, "method", None) or "GET")
        if method != "GET":
            raise ReleaseMonitorError("Fixture GitHub request did not use GET.")
        if (
            record["operation"] == "knowledge_release_discovery"
            and str(getattr(request, "full_url", request)) == self.metadata_url
        ):
            return _FixtureGitHubNetworkResponse(
                self.metadata,
                url=self.metadata_url,
                content_type="application/json",
            )
        if (
            record["operation"]
            == "approved_knowledge_release_artifact_retrieval"
            and str(getattr(request, "full_url", request)) == self.asset_url
        ):
            return _FixtureGitHubNetworkResponse(
                self.artifact,
                url=self.asset_url,
                content_type="application/zip",
            )
        raise ReleaseMonitorError("Unexpected fixture GitHub release request.")


async def run_network_boundary_monitor(
    *,
    mode: MonitorMode,
    policy_path: str | Path = DEFAULT_RUNTIME_POLICY_PATH,
) -> dict[str, Any]:
    """Exercise local workflows under outbound-call observation and inspect update fields."""

    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be 'fixture' or 'live'")
    policy = load_runtime_policy(policy_path)
    workflow_results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="di-rag-network-monitor-") as temporary:
        workspace = Path(temporary)
        data_dir = workspace / "data"
        config_path = workspace / "provider-config.json"
        release_catalog = workspace / "release-catalog"
        prior_release = workspace / "prior-release" / "kr-2026-07-06.1"
        embedding_provider = _FixtureEmbeddingProvider() if mode == "fixture" else None
        answer_generator: Any | None = _FixtureAnswerGenerator() if mode == "fixture" else None
        signing_private_key_path, trust_root_path = (
            _create_ephemeral_release_signing_material(workspace)
        )
        _save_monitor_provider_configuration(config_path, policy, mode=mode)
        _build_fixture_release(
            prior_release,
            signing_private_key_path=signing_private_key_path,
            trust_root_path=trust_root_path,
            created_at_utc="2026-07-06T00:00:00Z",
        )
        _build_fixture_release(
            release_catalog / "kr-2099-01-01.1",
            signing_private_key_path=signing_private_key_path,
            trust_root_path=trust_root_path,
        )

        github_responses = _FixtureGitHubResponseFactory()
        observer = _NetworkCallObserver(
            allow_live_loopback=mode == "live",
            external_response_factory=github_responses,
        )
        with observer:
            await _execute_network_observed_workflows(
                observer=observer,
                data_dir=data_dir,
                config_path=config_path,
                release_catalog=release_catalog,
                prior_release=prior_release,
                trust_root_path=trust_root_path,
                embedding_provider=embedding_provider,
                answer_generator=answer_generator,
                workflow_results=workflow_results,
            )
            _execute_observed_github_release_transport(
                observer=observer,
                workspace=workspace,
                responses=github_responses,
                workflow_results=workflow_results,
            )
        unapproved_artifact_transport_blocked = (
            _verify_unapproved_artifact_transport_is_blocked(github_responses)
        )

    release_request = build_release_network_request(
        policy,
        operation="knowledge_release_discovery",
        base_url="https://updates.example.test",
        application_version="0.1.0",
        active_knowledge_release_id="kr-2026-07-06.1",
    )
    release_fields = sorted(parse_qs(urlparse(release_request.full_url).query))
    release_field_failures = validate_update_request_fields(
        policy,
        {field: "redacted" for field in release_fields},
    )
    required_workflows = list(policy["network"]["answer_path_observed_workflows"])
    observed_workflows = [
        workflow for workflow in required_workflows if workflow in observer.observed_workflows
    ]
    missing_workflows = sorted(set(required_workflows) - observer.observed_workflows)
    forbidden_calls = [call for call in observer.calls if not call["allowed"]]
    actual_github_requests = [
        call
        for call in observer.calls
        if call["operation"]
        in {
            "knowledge_release_discovery",
            "approved_knowledge_release_artifact_retrieval",
        }
    ]
    actual_operations = [str(call["operation"]) for call in actual_github_requests]
    expected_actual_operations = [
        "knowledge_release_discovery",
        "approved_knowledge_release_artifact_retrieval",
    ]
    prohibited_update_fields = set(
        policy["network"]["prohibited_update_request_fields"]
    )
    actual_github_content_free = all(
        not (set(call["field_names"]) & prohibited_update_fields)
        for call in actual_github_requests
    )
    actual_github_transport_observed = actual_operations == expected_actual_operations
    policy_failures = validate_runtime_policy_privacy_boundary(policy)
    failures = [
        *[f"policy:{index}" for index, _failure in enumerate(policy_failures, start=1)],
        *[f"release-fields:{index}" for index, _failure in enumerate(release_field_failures, start=1)],
        *[f"missing-workflow:{workflow}" for workflow in missing_workflows],
        *[f"workflow:{result['id']}" for result in workflow_results if result["status"] != "passed"],
    ]
    if forbidden_calls:
        failures.append("forbidden-network-request")
    if not actual_github_transport_observed:
        failures.append("github-release-transport-not-observed")
    if not actual_github_content_free:
        failures.append("github-release-request-contained-prohibited-fields")
    if not unapproved_artifact_transport_blocked:
        failures.append("unapproved-github-artifact-transport-not-blocked")
    return {
        "monitor_id": "release-network-boundary-monitor",
        "mode": "live" if mode == "live" else "fixture-non-live",
        "observed_workflows": observed_workflows,
        "workflow_results": workflow_results,
        "network_requests": observer.calls,
        "allowed_request_count": sum(1 for call in observer.calls if call["allowed"]),
        "forbidden_request_count": len(forbidden_calls),
        "release_request_inspection": {
            "approved_operation": True,
            "content_free": not release_field_failures and actual_github_content_free,
            "field_names": release_fields,
            "actual_github_transport_observed": actual_github_transport_observed,
            "actual_operations": actual_operations,
            "unapproved_artifact_transport_blocked": (
                unapproved_artifact_transport_blocked
            ),
        },
        "failures": failures,
        "passed": not failures,
    }


async def _execute_network_observed_workflows(
    *,
    observer: _NetworkCallObserver,
    data_dir: Path,
    config_path: Path,
    release_catalog: Path,
    prior_release: Path,
    trust_root_path: Path,
    embedding_provider: Any | None,
    answer_generator: Any | None,
    workflow_results: list[dict[str, Any]],
) -> None:
    with observer.workflow("local_indexing"):
        installation = install_knowledge_release(
            data_dir,
            release_dir=prior_release,
            embedding_provider=embedding_provider,
            trust_root_path=trust_root_path,
        )
    workflow_results.append({"id": "local_indexing", "status": "passed"})

    with observer.workflow("retrieval"):
        results = HybridRetriever.from_data_dir(
            data_dir,
            embedding_provider=embedding_provider,
        ).retrieve("What Danish test do I need for permanent residence?")
    workflow_results.append(
        {"id": "retrieval", "status": "passed" if results else "failed"}
    )

    app = create_app(
        config_path=config_path,
        data_dir=data_dir,
        answer_generator=answer_generator,
        release_catalog_dir=release_catalog,
        embedding_provider=embedding_provider,
        trust_root_path=trust_root_path,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        with observer.workflow("question", "generation"):
            answer = await client.post(
                "/ask",
                data={"question": "What Danish test do I need for permanent residence?"},
                headers={"Origin": "http://testserver"},
            )
        workflow_results.extend(
            [
                {"id": "question", "status": "passed" if answer.status_code == 200 else "failed"},
                {"id": "generation", "status": "passed" if answer.status_code == 200 else "failed"},
            ]
        )
        conversation_id = _conversation_id_from_html(answer.text)

        with observer.workflow("evidence_inspection"):
            evidence = await client.get(f"/conversations/{conversation_id}")
        workflow_results.append(
            {
                "id": "evidence_inspection",
                "status": "passed"
                if evidence.status_code == 200 and "Inspect evidence" in evidence.text
                else "failed",
            }
        )

        with observer.workflow("history"):
            history = await client.get(f"/conversations/{conversation_id}")
        workflow_results.append(
            {"id": "history", "status": "passed" if history.status_code == 200 else "failed"}
        )

        with observer.workflow("export"):
            exported = await client.get(f"/conversations/{conversation_id}/export.json")
        workflow_results.append(
            {"id": "export", "status": "passed" if exported.status_code == 200 else "failed"}
        )

        with observer.workflow("knowledge_update_review"):
            checked = await client.post(
                "/knowledge-updates/check",
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            reviewed = await client.get("/")
        workflow_results.append(
            {
                "id": "knowledge_update_review",
                "status": "passed"
                if checked.status_code == 303 and "Knowledge update available" in reviewed.text
                else "failed",
            }
        )

        with observer.workflow("deletion"):
            deleted = await client.post(
                f"/conversations/{conversation_id}/delete",
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
        workflow_results.append(
            {"id": "deletion", "status": "passed" if deleted.status_code == 303 else "failed"}
        )

    if installation["manifest"]["knowledge_release_id"] != "kr-2026-07-06.1":
        workflow_results.append({"id": "local_indexing_identity", "status": "failed"})


def _execute_observed_github_release_transport(
    *,
    observer: _NetworkCallObserver,
    workspace: Path,
    responses: _FixtureGitHubResponseFactory,
    workflow_results: list[dict[str, Any]],
) -> None:
    """Exercise the default GitHub transport against an in-memory network response."""

    client = GitHubReleaseClient(application_version="0.1.0")
    try:
        with observer.workflow("knowledge_update_review"):
            releases = client.list_published_releases()
        metadata_passed = (
            len(releases) == 1
            and releases[0].tag_name == responses.release_id
            and len(releases[0].assets) == 1
        )
    except Exception:
        releases = ()
        metadata_passed = False
    workflow_results.append(
        {
            "id": "github-release-metadata-discovery",
            "status": "passed" if metadata_passed else "failed",
        }
    )
    if not metadata_passed:
        workflow_results.append(
            {"id": "github-release-approved-artifact", "status": "failed"}
        )
        return

    asset = releases[0].assets[0]
    approval = ArtifactDownloadApproval(
        approved=True,
        requested_knowledge_release_id=responses.release_id,
        artifact_id=responses.asset_id,
        artifact_name=responses.asset_name,
    )
    try:
        with observer.workflow("knowledge_update_review", release_approved=True):
            downloaded = client.download_artifact(
                asset,
                workspace / "github-release-download",
                approval=approval,
            )
        artifact_passed = (
            downloaded.bytes_written == len(responses.artifact)
            and downloaded.sha256 == hashlib.sha256(responses.artifact).hexdigest()
            and downloaded.github_digest_verified
        )
    except Exception:
        artifact_passed = False
    workflow_results.append(
        {
            "id": "github-release-approved-artifact",
            "status": "passed" if artifact_passed else "failed",
        }
    )


def _verify_unapproved_artifact_transport_is_blocked(
    responses: _FixtureGitHubResponseFactory,
) -> bool:
    response_factory_called = False

    def unexpected_response_factory(request: Any, record: dict[str, Any]) -> Any:
        nonlocal response_factory_called
        response_factory_called = True
        raise ReleaseMonitorError(
            "Unapproved artifact request reached the response factory."
        )

    observer = _NetworkCallObserver(
        allow_live_loopback=False,
        external_response_factory=unexpected_response_factory,
    )
    blocked = False
    with observer:
        request = urllib.request.Request(responses.asset_url, method="GET")
        try:
            urllib.request.build_opener().open(request, timeout=1.0)
        except ReleaseMonitorError:
            blocked = True
    return (
        blocked
        and not response_factory_called
        and len(observer.calls) == 1
        and observer.calls[0]["operation"]
        == "approved_knowledge_release_artifact_retrieval"
        and observer.calls[0]["allowed"] is False
        and observer.calls[0]["release_approved"] is False
    )


def _conversation_id_from_html(html: str) -> str:
    match = re.search(r'href="/conversations/([^"]+)"', html)
    if match is None:
        raise ReleaseMonitorError("Supported-answer workflow did not expose local history.")
    return match.group(1)


def _save_monitor_provider_configuration(
    path: Path,
    policy: dict[str, Any],
    *,
    mode: MonitorMode,
) -> None:
    if mode == "live":
        provider = policy["providers"]["initial"]
        generation = policy["models"]["generation"]
        configuration = ProviderConfiguration(
            provider_id=str(provider["id"]),
            endpoint=str(provider["default_endpoint"]),
            model=str(generation["initial"]),
            provider_version=str(provider["minimum_version"]),
            model_identity=dict(generation["identity"]),
            capabilities=["generation"],
            validated_at_utc="release-monitor-live",
        )
    else:
        configuration = ProviderConfiguration(
            provider_id="openai_compatible",
            endpoint="http://127.0.0.1:1234",
            model="release-monitor-fixture-generation",
            provider_version="fixture",
            model_identity={"id": "release-monitor-fixture-generation"},
            capabilities=["generation"],
            validated_at_utc="release-monitor-fixture",
        )
    save_provider_configuration(path, configuration)


def _create_ephemeral_release_signing_material(workspace: Path) -> tuple[Path, Path]:
    """Create a monitor-only Ed25519 key and matching trust root in a temp workspace."""

    signing_dir = workspace / "ephemeral-release-signing"
    signing_dir.mkdir(parents=True, exist_ok=True)
    private_key_path = signing_dir / "private-key.pem"
    public_key_path = signing_dir / "public-key.pem"
    trust_root_path = signing_dir / "trust-root.json"
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
            capture_output=True,
        )
        private_key_path.chmod(0o600)
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
        public_key_pem = public_key_path.read_text(encoding="ascii")
    except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        raise ReleaseMonitorError(
            "Could not create ephemeral release-monitor signing material."
        ) from exc

    trust_root_path.write_text(
        json.dumps(
            {
                "algorithm": "ed25519",
                "private_key_committed": False,
                "public_key_pem": public_key_pem,
                "schema_version": "1.0",
                "status": "active",
                "trust_root_id": "release-monitor-ephemeral-ed25519-v1",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return private_key_path, trust_root_path


def _build_fixture_release(
    release_dir: Path,
    *,
    signing_private_key_path: Path,
    trust_root_path: Path,
    created_at_utc: str = "2099-01-01T00:00:00Z",
) -> None:
    manifest = json.loads(
        (BUNDLED_MINIMAL_RELEASE / "manifest.json").read_text(encoding="utf-8")
    )
    documents = json.loads(
        (BUNDLED_MINIMAL_RELEASE / "corpus" / "documents.json").read_text(
            encoding="utf-8"
        )
    )
    build_publishable_knowledge_release(
        release_dir=release_dir,
        release_id=release_dir.name,
        source_registry_version="sr-release-monitor-fixture",
        sources=[dict(source) for source in manifest["sources"]],
        documents=[dict(document) for document in documents],
        created_at_utc=created_at_utc,
        minimum_application_version="0.1.0",
        signing_private_key_path=signing_private_key_path,
        trust_root_path=trust_root_path,
    )


def run_rollback_fault_matrix(
    *,
    mode: MonitorMode,
) -> dict[str, Any]:
    """Inject every published installation fault and prove atomic rollback behavior."""

    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be 'fixture' or 'live'")
    phases = [
        "verification",
        "extraction",
        "embedding",
        "indexing",
        "activation",
        "late_activation",
    ]
    results: list[dict[str, Any]] = []
    prior_identity: dict[str, str] | None = None
    for case_number, phase in enumerate(phases, start=1):
        with tempfile.TemporaryDirectory(prefix="di-rag-rollback-monitor-") as temporary:
            workspace = Path(temporary)
            data_dir = workspace / "data"
            release_dir = workspace / "release-catalog" / f"kr-2099-01-01.{case_number}"
            prior_release = workspace / "prior-release" / "kr-2026-07-06.1"
            embedding_provider = _FixtureEmbeddingProvider() if mode == "fixture" else None
            signing_private_key_path, trust_root_path = (
                _create_ephemeral_release_signing_material(workspace)
            )
            _build_fixture_release(
                prior_release,
                signing_private_key_path=signing_private_key_path,
                trust_root_path=trust_root_path,
                created_at_utc="2026-07-06T00:00:00Z",
            )
            install_knowledge_release(
                data_dir,
                release_dir=prior_release,
                embedding_provider=embedding_provider,
                trust_root_path=trust_root_path,
            )
            before = active_corpus_summary(data_dir)
            prior_identity = prior_identity or before
            _build_fixture_release(
                release_dir,
                signing_private_key_path=signing_private_key_path,
                trust_root_path=trust_root_path,
            )
            try:
                verify_knowledge_release(
                    release_dir,
                    trust_root_path=trust_root_path,
                )
                signature_verification_passed = True
            except Exception:
                signature_verification_passed = False
            progress: list[dict[str, Any]] = []
            activation_calls = 0
            fault_injected = False

            if phase == "verification" and signature_verification_passed:
                signature_path = release_dir / "manifest.sig"
                tampered_signature = bytearray(signature_path.read_bytes())
                tampered_signature[0] ^= 1
                signature_path.write_bytes(tampered_signature)
                fault_injected = True

            def inject_fault(current_phase: str) -> None:
                nonlocal activation_calls, fault_injected
                if phase == "verification":
                    return
                if phase == "late_activation":
                    if current_phase != "activation":
                        return
                    activation_calls += 1
                    if activation_calls != 4:
                        return
                elif current_phase != phase:
                    return
                fault_injected = True
                raise ReleaseMonitorError(f"simulated-{phase}-fault")

            failure_observed = False
            failure_type = ""
            installation_returned = False
            try:
                install_knowledge_release(
                    data_dir,
                    release_dir=release_dir,
                    embedding_provider=embedding_provider,
                    trust_root_path=trust_root_path,
                    progress_callback=progress.append,
                    fault_injector=inject_fault,
                )
                installation_returned = True
            except Exception as exc:  # Evidence records the type, never request content.
                failure_observed = True
                failure_type = type(exc).__name__

            try:
                after = active_corpus_summary(data_dir)
                prior_pair_unchanged = after == before
                target_release_active = (
                    after["knowledge_release_id"] == release_dir.name
                )
            except Exception:
                after = {}
                prior_pair_unchanged = False
                target_release_active = False

            try:
                retrieved = HybridRetriever.from_data_dir(
                    data_dir,
                    embedding_provider=embedding_provider,
                ).retrieve("What Danish test is required for permanent residence?")
                prior_pair_queryable = bool(retrieved) and all(
                    result.get("knowledge_release_id") == before["knowledge_release_id"]
                    for result in retrieved
                )
            except Exception:
                prior_pair_queryable = False

            success_phase_reported = any(
                entry.get("phase") in {"complete", "already_active"}
                for entry in progress
            )
            installation_reported_success = installation_returned or success_phase_reported
            signature_rejection_observed = phase != "verification" or (
                failure_observed and failure_type == "KnowledgeReleaseError"
            )
            passed = all(
                (
                    signature_verification_passed,
                    signature_rejection_observed,
                    fault_injected,
                    failure_observed,
                    prior_pair_unchanged,
                    prior_pair_queryable,
                    not target_release_active,
                    not installation_reported_success,
                )
            )
            results.append(
                {
                    "phase": phase,
                    "status": "passed" if passed else "failed",
                    "signature_verification_passed": signature_verification_passed,
                    "signature_rejection_observed": signature_rejection_observed,
                    "fault_injected": fault_injected,
                    "failure_observed": failure_observed,
                    "failure_type": failure_type,
                    "prior_pair_unchanged": prior_pair_unchanged,
                    "prior_pair_queryable": prior_pair_queryable,
                    "target_release_active": target_release_active,
                    "installation_reported_success": installation_reported_success,
                    "completed_phase_observed": success_phase_reported,
                }
            )
            if (data_dir / ".installing").exists():
                shutil.rmtree(data_dir / ".installing")

    return {
        "monitor_id": "knowledge-release-rollback-fault-matrix",
        "mode": "live" if mode == "live" else "fixture-non-live",
        "prior_pair_identity": prior_identity or {},
        "results": results,
        "failures": [result["phase"] for result in results if result["status"] != "passed"],
        "passed": all(result["status"] == "passed" for result in results),
    }


def _numeric_version(value: Any, *, width: int = 3) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", str(value))[:width]]
    return tuple([*parts, *([0] * (width - len(parts)))])


def validate_observed_supported_environment_identity(
    observed: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Validate observed runtime facts, then normalize them to policy categories."""

    safe_observed = {
        key: str(observed.get(key, ""))
        for key in (
            "host_os",
            "windows_version",
            "windows_build",
            "wsl_version",
            "distribution_id",
            "distribution_version",
            "architecture",
            "python_version",
            "ollama_version",
            "browser_name",
            "browser_version",
        )
    }
    browser_release_baseline = policy.get("supported_environment", {}).get(
        "browser_release_baseline", {}
    )
    minimum_chromium_major = browser_release_baseline.get(
        "minimum_chromium_major"
    )
    valid_browser_baseline = (
        isinstance(minimum_chromium_major, int)
        and not isinstance(minimum_chromium_major, bool)
        and minimum_chromium_major > 0
    )
    machine = safe_observed["architecture"].casefold().replace("-", "_")
    browser_name = safe_observed["browser_name"].casefold()
    checks = {
        "windows_11": (
            safe_observed["host_os"].casefold() == "windows"
            and safe_observed["windows_version"] == "11"
            and _numeric_version(safe_observed["windows_build"], width=1) >= (22000,)
        ),
        "wsl2_ubuntu": (
            safe_observed["wsl_version"] == "2"
            and safe_observed["distribution_id"].casefold() == "ubuntu"
        ),
        "architecture": machine in {"x86_64", "amd64"},
        "python": _numeric_version(safe_observed["python_version"]) >= (3, 11, 0),
        "ollama": _numeric_version(safe_observed["ollama_version"]) >= (0, 30, 6),
        "browser": (
            valid_browser_baseline
            and browser_name in {"chromium", "chrome"}
            and _numeric_version(safe_observed["browser_version"], width=1)
            >= (minimum_chromium_major,)
        ),
    }
    normalized = {
        "host": (
            "Windows 11 with WSL2 Ubuntu"
            if checks["windows_11"] and checks["wsl2_ubuntu"]
            else " ".join(
                value
                for value in (
                    safe_observed["host_os"],
                    safe_observed["windows_version"],
                    f"WSL{safe_observed['wsl_version']}"
                    if safe_observed["wsl_version"]
                    else "",
                    safe_observed["distribution_id"],
                )
                if value
            )
        ),
        "architecture": (
            "x86-64" if checks["architecture"] else safe_observed["architecture"]
        ),
        "python": "3.11+" if checks["python"] else safe_observed["python_version"],
        "ollama": "0.30.6+" if checks["ollama"] else safe_observed["ollama_version"],
        "browser": (
            "evergreen local browser"
            if checks["browser"]
            else " ".join(
                value
                for value in (
                    safe_observed["browser_name"],
                    safe_observed["browser_version"],
                )
                if value
            )
        ),
    }
    policy_matches = normalized == policy["supported_environment"]["first_verified"]
    checks["policy_contract"] = policy_matches
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "browser_release_baseline": {
            "minimum_chromium_major": minimum_chromium_major,
            "reviewed_on": browser_release_baseline.get("reviewed_on", ""),
        },
        "normalized": normalized,
        "observed": safe_observed,
    }


def _valid_rollback_evidence(
    rollback_evidence: dict[str, Any], *, expected_mode: str
) -> bool:
    if not isinstance(rollback_evidence, dict):
        return False
    required_phases = [
        "verification",
        "extraction",
        "embedding",
        "indexing",
        "activation",
        "late_activation",
    ]
    results = rollback_evidence.get("results") or []
    return all(
        (
            rollback_evidence.get("monitor_id")
            == "knowledge-release-rollback-fault-matrix",
            rollback_evidence.get("mode") == expected_mode,
            rollback_evidence.get("passed") is True,
            [item.get("phase") for item in results if isinstance(item, dict)]
            == required_phases,
            all(
                isinstance(item, dict) and item.get("status") == "passed"
                for item in results
            ),
        )
    )


def _live_execution_diagnostic(
    journey_id: str,
    *,
    stage: str,
    reason_code: str,
    error: BaseException | None = None,
) -> dict[str, str | None]:
    return {
        "journey_id": journey_id,
        "stage": stage,
        "reason_code": reason_code,
        "exception_type": type(error).__name__ if error is not None else None,
    }


def _sanitize_live_diagnostic(diagnostic: dict[str, Any]) -> dict[str, str | None]:
    allowed_stages = {
        "browser-process-evidence-validation",
        "environment-identity-validation",
        "journey-validation",
        "live-process-browser-execution",
        "prerequisite-check",
        "process-restart-validation",
        "rollback-evidence-validation",
        "runtime-identity-validation",
    }
    allowed_reasons = {
        "browser-evidence-unavailable",
        "invalid-rollback-evidence",
        "journey-check-failed",
        "journey-exception",
        "prerequisite-failed",
        "restart-not-observed",
        "runtime-identity-incomplete",
        "unsupported-observed-environment",
    }
    raw_stage = str(diagnostic.get("stage") or "")
    raw_reason = str(diagnostic.get("reason_code") or "")
    raw_exception_type = str(diagnostic.get("exception_type") or "")
    return {
        "journey_id": str(diagnostic.get("journey_id", "")),
        "stage": (
            raw_stage
            if raw_stage in allowed_stages
            else "journey-validation"
        ),
        "reason_code": (
            raw_reason
            if raw_reason in allowed_reasons
            else "journey-check-failed"
        ),
        "exception_type": (
            raw_exception_type
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", raw_exception_type)
            else None
        ),
    }


def _content_free_model_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed_fields = {
        "architecture",
        "digest",
        "family",
        "format",
        "model",
        "parameter_size",
        "quantization_level",
    }
    return {
        str(key): item
        for key, item in value.items()
        if key in allowed_fields and isinstance(item, str | int | float | bool)
    }


def _safe_evidence_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(parsed, 0)


def _failed_live_supported_environment_execution(error: BaseException) -> dict[str, Any]:
    return {
        "journey_status": {},
        "diagnostics": [
            _live_execution_diagnostic(
                "setup",
                stage="live-process-browser-execution",
                reason_code="journey-exception",
                error=error,
            )
        ],
        "runtime_configuration": {},
        "corpus_identity": {},
        "observed_environment_identity": {},
        "execution_evidence": {},
    }


def _build_live_supported_environment_report(
    *,
    raw_execution: dict[str, Any],
    rollback_evidence: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    journey_status = {
        journey_id: "failed" for journey_id in SUPPORTED_ENVIRONMENT_JOURNEY_ORDER
    }
    raw_status = raw_execution.get("journey_status")
    if isinstance(raw_status, dict):
        for journey_id in SUPPORTED_ENVIRONMENT_JOURNEY_ORDER[:-1]:
            if raw_status.get(journey_id) in {"passed", "failed"}:
                journey_status[journey_id] = str(raw_status[journey_id])

    diagnostics_by_journey: dict[str, dict[str, str | None]] = {}
    for diagnostic in raw_execution.get("diagnostics") or []:
        if not isinstance(diagnostic, dict):
            continue
        journey_id = str(diagnostic.get("journey_id", ""))
        if journey_id not in SUPPORTED_ENVIRONMENT_JOURNEY_ORDER:
            continue
        diagnostics_by_journey.setdefault(
            journey_id,
            _sanitize_live_diagnostic(diagnostic),
        )

    raw_execution_evidence = raw_execution.get("execution_evidence")
    if not isinstance(raw_execution_evidence, dict):
        raw_execution_evidence = {}
    execution_evidence = {
        "transport": str((raw_execution_evidence or {}).get("transport", "")),
        "browser_driver": str(
            (raw_execution_evidence or {}).get("browser_driver", "")
        ),
        "browser_phase_count": _safe_evidence_count(
            raw_execution_evidence.get("browser_phase_count", 0)
        ),
        "app_process_start_count": _safe_evidence_count(
            raw_execution_evidence.get("app_process_start_count", 0)
        ),
        "app_process_stop_count": _safe_evidence_count(
            raw_execution_evidence.get("app_process_stop_count", 0)
        ),
        "history_restart_observed": (
            raw_execution_evidence.get("history_restart_observed") is True
        ),
        "browser_evidence_available": (
            raw_execution_evidence.get("browser_evidence_available") is True
        ),
    }
    process_browser_passed = all(
        (
            execution_evidence["transport"] == "loopback-bound-process",
            execution_evidence["browser_driver"] == "playwright",
            execution_evidence["browser_phase_count"] == 2,
            execution_evidence["app_process_start_count"] == 2,
            execution_evidence["app_process_stop_count"] == 2,
            execution_evidence["browser_evidence_available"],
        )
    )
    if not process_browser_passed:
        for journey_id in SUPPORTED_ENVIRONMENT_JOURNEY_ORDER[:-1]:
            journey_status[journey_id] = "failed"
        diagnostics_by_journey.setdefault(
            "setup",
            _live_execution_diagnostic(
                "setup",
                stage="browser-process-evidence-validation",
                reason_code="browser-evidence-unavailable",
            ),
        )
    if not execution_evidence["history_restart_observed"]:
        journey_status["history-persistence"] = "failed"
        diagnostics_by_journey.setdefault(
            "history-persistence",
            _live_execution_diagnostic(
                "history-persistence",
                stage="process-restart-validation",
                reason_code="restart-not-observed",
            ),
        )

    observed_identity = raw_execution.get("observed_environment_identity")
    identity_validation = validate_observed_supported_environment_identity(
        observed_identity if isinstance(observed_identity, dict) else {},
        policy,
    )
    if not identity_validation["passed"]:
        journey_status["setup"] = "failed"
        diagnostics_by_journey.setdefault(
            "setup",
            _live_execution_diagnostic(
                "setup",
                stage="environment-identity-validation",
                reason_code="unsupported-observed-environment",
            ),
        )

    rollback_passed = _valid_rollback_evidence(
        rollback_evidence,
        expected_mode="live",
    )
    journey_status["rollback"] = "passed" if rollback_passed else "failed"
    if not rollback_passed:
        diagnostics_by_journey.setdefault(
            "rollback",
            _live_execution_diagnostic(
                "rollback",
                stage="rollback-evidence-validation",
                reason_code="invalid-rollback-evidence",
            ),
        )

    runtime_configuration = raw_execution.get("runtime_configuration")
    if not isinstance(runtime_configuration, dict):
        runtime_configuration = {}
    corpus_identity = raw_execution.get("corpus_identity")
    if not isinstance(corpus_identity, dict):
        corpus_identity = {}
    provider_identity = {
        "provider_id": str(runtime_configuration.get("provider_id", "")),
        "provider_version": str(runtime_configuration.get("provider_version", "")),
    }
    detected_model_identity = runtime_configuration.get("model_identity")
    raw_expected_model_identity = policy["models"]["generation"].get("identity")
    expected_model_identity = (
        raw_expected_model_identity
        if isinstance(raw_expected_model_identity, dict)
        else {}
    )
    model_identity = {
        "generation_model": str(runtime_configuration.get("model", "")),
        "generation_model_identity": _content_free_model_identity(
            detected_model_identity
        ),
        "embedding_model": str(corpus_identity.get("embedding_model", "")),
    }
    identity_sections_passed = all(
        (
            provider_identity["provider_id"]
            == str(policy["providers"]["initial"]["id"]),
            bool(provider_identity["provider_version"]),
            provider_identity["provider_version"]
            == identity_validation["observed"]["ollama_version"],
            model_identity["generation_model"]
            == str(policy["models"]["generation"]["initial"]),
            bool(expected_model_identity),
            all(
                model_identity["generation_model_identity"].get(key) == str(value)
                for key, value in expected_model_identity.items()
            ),
            model_identity["embedding_model"]
            == str(policy["models"]["embedding"]["initial_supported"]),
            all(
                corpus_identity.get(field) not in {None, ""}
                for field in (
                    "knowledge_release_id",
                    "corpus_id",
                    "source_registry_version",
                    "embedding_model",
                    "embedding_vector_dimensions",
                    "index_schema_version",
                )
            ),
        )
    )
    if not identity_sections_passed:
        journey_status["setup"] = "failed"
        diagnostics_by_journey.setdefault(
            "setup",
            _live_execution_diagnostic(
                "setup",
                stage="runtime-identity-validation",
                reason_code="runtime-identity-incomplete",
            ),
        )

    for journey_id in SUPPORTED_ENVIRONMENT_JOURNEY_ORDER:
        if (
            journey_status[journey_id] != "passed"
            and journey_id not in diagnostics_by_journey
        ):
            diagnostics_by_journey[journey_id] = _live_execution_diagnostic(
                journey_id,
                stage="prerequisite-check",
                reason_code="prerequisite-failed",
            )
    journeys = [
        {"id": journey_id, "status": journey_status[journey_id]}
        for journey_id in SUPPORTED_ENVIRONMENT_JOURNEY_ORDER
    ]
    failures = [item["id"] for item in journeys if item["status"] != "passed"]
    passed = not failures
    safe_corpus_identity = {
        key: str(value)
        for key, value in corpus_identity.items()
        if key
        in {
            "knowledge_release_id",
            "corpus_id",
            "source_registry_version",
            "embedding_model",
            "embedding_vector_dimensions",
            "index_schema_version",
        }
    }
    return {
        "monitor_id": "supported-environment-critical-journeys",
        "mode": "live",
        "qualification_scope": "live-supported-environment",
        "can_qualify_supported_environment": passed,
        "live_provider_calls": (
            provider_identity["provider_id"]
            == str(policy["providers"]["initial"]["id"])
            and journey_status["supported-answer"] == "passed"
            and journey_status["refusal"] == "passed"
        ),
        "supported_environment_identity": identity_validation["normalized"],
        "observed_environment_identity": identity_validation["observed"],
        "environment_identity_validation": {
            "passed": identity_validation["passed"],
            "checks": identity_validation["checks"],
        },
        "execution_evidence": execution_evidence,
        "provider_identity": provider_identity,
        "model_identity": model_identity,
        "corpus_identity": safe_corpus_identity,
        "journeys": journeys,
        "failures": failures,
        "diagnostics": [
            diagnostics_by_journey[journey_id]
            for journey_id in SUPPORTED_ENVIRONMENT_JOURNEY_ORDER
            if journey_id in diagnostics_by_journey
        ],
        "passed": passed,
    }


def _execute_live_supported_environment_journeys(
    *, policy: dict[str, Any]
) -> dict[str, Any]:
    def prepare_workspace(workspace: Path) -> LiveEnvironmentWorkspace:
        data_dir = workspace / "data"
        config_path = workspace / "provider-config.json"
        release_catalog = workspace / "release-catalog"
        prior_release = workspace / "prior-release" / "kr-2026-07-06.1"
        target_release_id = "kr-2099-01-01.1"
        signing_private_key_path, trust_root_path = (
            _create_ephemeral_release_signing_material(workspace)
        )
        _build_fixture_release(
            prior_release,
            signing_private_key_path=signing_private_key_path,
            trust_root_path=trust_root_path,
            created_at_utc="2026-07-06T00:00:00Z",
        )
        _build_fixture_release(
            release_catalog / target_release_id,
            signing_private_key_path=signing_private_key_path,
            trust_root_path=trust_root_path,
        )
        install_knowledge_release(
            data_dir,
            release_dir=prior_release,
            embedding_provider=None,
            trust_root_path=trust_root_path,
        )
        return LiveEnvironmentWorkspace(
            data_dir=data_dir,
            config_path=config_path,
            release_catalog_dir=release_catalog,
            trust_root_path=trust_root_path,
            target_release_id=target_release_id,
        )

    return execute_live_supported_environment_journeys(
        policy=policy,
        prepare_workspace=prepare_workspace,
        project_root=ROOT,
    )


async def run_supported_environment_critical_journeys(
    *,
    mode: MonitorMode,
    rollback_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the release-blocking journeys through the local application's public seams.

    Fixture execution proves the harness without qualifying a supported environment. Live
    execution uses the runtime policy's Ollama defaults and qualifies only when every journey,
    including independently supplied rollback evidence, passes. Returned evidence contains
    identities, outcomes, and content-free failure diagnostics only; application content
    remains in the temporary workspace.
    """

    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be 'fixture' or 'live'")

    policy = load_runtime_policy(DEFAULT_RUNTIME_POLICY_PATH)
    if mode == "live":
        if rollback_evidence is None:
            try:
                rollback_evidence = run_rollback_fault_matrix(mode="live")
            except Exception:
                rollback_evidence = {}
        try:
            raw_execution = _execute_live_supported_environment_journeys(policy=policy)
            if not isinstance(raw_execution, dict):
                raise TypeError("Live supported-environment evidence must be an object.")
        except Exception as error:
            raw_execution = _failed_live_supported_environment_execution(error)
        return _build_live_supported_environment_report(
            raw_execution=raw_execution,
            rollback_evidence=rollback_evidence,
            policy=policy,
        )

    provider_policy = policy["providers"]["initial"]
    generation_policy = policy["models"]["generation"]
    embedding_policy = policy["models"]["embedding"]
    journey_order = [
        "setup",
        "supported-answer",
        "refusal",
        "evidence-inspection",
        "history-persistence",
        "deletion-export",
        "update-installation",
        "rollback",
    ]
    journey_status = {journey_id: "failed" for journey_id in journey_order}
    diagnostics_by_journey: dict[str, dict[str, str | None]] = {}

    def record_failure(
        journey_id: str,
        *,
        stage: str,
        reason_code: str,
        error: BaseException | None = None,
    ) -> None:
        journey_status[journey_id] = "failed"
        diagnostics_by_journey.setdefault(
            journey_id,
            {
                "journey_id": journey_id,
                "stage": stage,
                "reason_code": reason_code,
                "exception_type": type(error).__name__ if error is not None else None,
            },
        )

    corpus_identity: dict[str, str] = {}
    runtime_configuration: dict[str, Any] = {}

    if rollback_evidence is None:
        try:
            rollback_evidence = run_rollback_fault_matrix(mode=mode)
        except Exception as error:
            rollback_evidence = {}
            record_failure(
                "rollback",
                stage="rollback-monitor-execution",
                reason_code="journey-exception",
                error=error,
            )

    with tempfile.TemporaryDirectory(prefix="di-rag-environment-monitor-") as temporary:
        workspace = Path(temporary)
        data_dir = workspace / "data"
        config_path = workspace / "provider-config.json"
        release_catalog = workspace / "release-catalog"
        prior_release = workspace / "prior-release" / "kr-2026-07-06.1"
        target_release_id = "kr-2099-01-01.1"
        embedding_provider = _FixtureEmbeddingProvider() if mode == "fixture" else None
        answer_generator: Any | None = _FixtureAnswerGenerator() if mode == "fixture" else None
        signing_private_key_path, trust_root_path = (
            _create_ephemeral_release_signing_material(workspace)
        )
        _build_fixture_release(
            prior_release,
            signing_private_key_path=signing_private_key_path,
            trust_root_path=trust_root_path,
            created_at_utc="2026-07-06T00:00:00Z",
        )
        _build_fixture_release(
            release_catalog / target_release_id,
            signing_private_key_path=signing_private_key_path,
            trust_root_path=trust_root_path,
        )
        try:
            install_knowledge_release(
                data_dir,
                release_dir=prior_release,
                embedding_provider=embedding_provider,
                trust_root_path=trust_root_path,
            )
            prior_installation_passed = True
        except Exception as error:
            prior_installation_passed = False
            record_failure(
                "setup",
                stage="prior-release-installation",
                reason_code="journey-exception",
                error=error,
            )

        capability_tester: Any | None = None
        if mode == "fixture":
            def fixture_capability_tester(
                _configuration: ProviderConfiguration,
            ) -> dict[str, Any]:
                return {
                    "ok": True,
                    "reason": "passed",
                    "message": "Fixture capability boundary passed.",
                    "provider_version": "fixture",
                    "model_identity": {"id": "fixture-generation"},
                    "capabilities": ["generation"],
                }

            capability_tester = fixture_capability_tester

        app = create_app(
            config_path=config_path,
            data_dir=data_dir,
            answer_generator=answer_generator,
            capability_tester=capability_tester,
            release_catalog_dir=release_catalog,
            embedding_provider=embedding_provider,
            trust_root_path=trust_root_path,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            setup_provider_id = "openai_compatible" if mode == "fixture" else str(
                provider_policy["id"]
            )
            setup_endpoint = (
                "http://127.0.0.1:1234"
                if mode == "fixture"
                else str(provider_policy["default_endpoint"])
            )
            setup_model = (
                "fixture-generation" if mode == "fixture" else str(generation_policy["initial"])
            )
            try:
                setup = await client.post(
                    "/setup",
                    data={
                        "provider_id": setup_provider_id,
                        "endpoint": setup_endpoint,
                        "model": setup_model,
                    },
                    headers={"Origin": "http://testserver"},
                    follow_redirects=False,
                )
                status = await client.get("/status")
                status_payload = status.json() if status.status_code == 200 else {}
                configuration = status_payload.get("configuration") or {}
                if isinstance(configuration, dict):
                    runtime_configuration = configuration
                setup_passed = all(
                    (
                        prior_installation_passed,
                        setup.status_code == 303,
                        status_payload.get("configured") is True,
                        configuration.get("provider_id") == setup_provider_id,
                        configuration.get("model") == setup_model,
                    )
                )
                journey_status["setup"] = "passed" if setup_passed else "failed"
                if not setup_passed:
                    record_failure(
                        "setup",
                        stage="setup-response-validation",
                        reason_code="journey-check-failed",
                    )
            except Exception as error:
                setup_passed = False
                record_failure(
                    "setup",
                    stage="setup-request",
                    reason_code="journey-exception",
                    error=error,
                )

            conversation_id = ""
            if setup_passed:
                try:
                    supported = await client.post(
                        "/ask",
                        data={
                            "question": "What Danish test do I need for permanent residence?"
                        },
                        headers={"Origin": "http://testserver"},
                    )
                    if supported.status_code == 200:
                        conversation_id = _conversation_id_from_html(supported.text)
                    supported_passed = (
                        bool(conversation_id) and "Inspect evidence" in supported.text
                    )
                    journey_status["supported-answer"] = (
                        "passed" if supported_passed else "failed"
                    )
                    if not supported_passed:
                        record_failure(
                            "supported-answer",
                            stage="supported-answer-validation",
                            reason_code="journey-check-failed",
                        )
                except Exception as error:
                    supported_passed = False
                    record_failure(
                        "supported-answer",
                        stage="supported-answer-request",
                        reason_code="journey-exception",
                        error=error,
                    )

                if supported_passed:
                    try:
                        refused = await client.post(
                            "/ask",
                            data={
                                "question": (
                                    "Give me legal advice on how to argue that my Danish test "
                                    "should count."
                                ),
                                "conversation_id": conversation_id,
                            },
                            headers={"Origin": "http://testserver"},
                        )
                        refusal_export = await client.get(
                            f"/conversations/{conversation_id}/export.json"
                        )
                        refusal_payload = (
                            refusal_export.json() if refusal_export.status_code == 200 else {}
                        )
                        refusal_turns = (
                            refusal_payload.get("conversation", {}).get("turns") or []
                        )
                        refusal_passed = (
                            refused.status_code == 200
                            and len(refusal_turns) == 2
                            and refusal_turns[-1].get("answer", {}).get("response_kind")
                            == "refusal"
                        )
                        journey_status["refusal"] = (
                            "passed" if refusal_passed else "failed"
                        )
                        if not refusal_passed:
                            record_failure(
                                "refusal",
                                stage="refusal-validation",
                                reason_code="journey-check-failed",
                            )
                    except Exception as error:
                        refusal_passed = False
                        record_failure(
                            "refusal",
                            stage="refusal-request",
                            reason_code="journey-exception",
                            error=error,
                        )

                    try:
                        inspected = await client.get(f"/conversations/{conversation_id}")
                        inspection_passed = (
                            inspected.status_code == 200
                            and "Inspect evidence" in inspected.text
                        )
                        journey_status["evidence-inspection"] = (
                            "passed" if inspection_passed else "failed"
                        )
                        if not inspection_passed:
                            record_failure(
                                "evidence-inspection",
                                stage="evidence-inspection-validation",
                                reason_code="journey-check-failed",
                            )
                    except Exception as error:
                        inspection_passed = False
                        record_failure(
                            "evidence-inspection",
                            stage="evidence-inspection-request",
                            reason_code="journey-exception",
                            error=error,
                        )

                    try:
                        history = await client.get(f"/conversations/{conversation_id}")
                        history_export = await client.get(
                            f"/conversations/{conversation_id}/export.json"
                        )
                        history_payload = (
                            history_export.json() if history_export.status_code == 200 else {}
                        )
                        history_passed = (
                            history.status_code == 200
                            and len(
                                history_payload.get("conversation", {}).get("turns") or []
                            )
                            == 2
                        )
                        journey_status["history-persistence"] = (
                            "passed" if history_passed else "failed"
                        )
                        if not history_passed:
                            record_failure(
                                "history-persistence",
                                stage="history-persistence-validation",
                                reason_code="journey-check-failed",
                            )
                    except Exception as error:
                        history_passed = False
                        record_failure(
                            "history-persistence",
                            stage="history-persistence-request",
                            reason_code="journey-exception",
                            error=error,
                        )

                    try:
                        exported = await client.get(
                            f"/conversations/{conversation_id}/export.json"
                        )
                        exported_all = await client.get("/conversations/export.json")
                        deleted = await client.post(
                            f"/conversations/{conversation_id}/delete",
                            headers={"Origin": "http://testserver"},
                            follow_redirects=False,
                        )
                        absent = await client.get(f"/conversations/{conversation_id}")
                        deletion_export_passed = all(
                            (
                                exported.status_code == 200,
                                exported_all.status_code == 200,
                                "attachment" in exported.headers.get(
                                    "content-disposition", ""
                                ),
                                deleted.status_code == 303,
                                absent.status_code == 404,
                            )
                        )
                        journey_status["deletion-export"] = (
                            "passed" if deletion_export_passed else "failed"
                        )
                        if not deletion_export_passed:
                            record_failure(
                                "deletion-export",
                                stage="deletion-export-validation",
                                reason_code="journey-check-failed",
                            )
                    except Exception as error:
                        deletion_export_passed = False
                        record_failure(
                            "deletion-export",
                            stage="deletion-export-request",
                            reason_code="journey-exception",
                            error=error,
                        )

                try:
                    checked = await client.post(
                        "/knowledge-updates/check",
                        headers={"Origin": "http://testserver"},
                        follow_redirects=False,
                    )
                    reviewed = await client.get("/")
                    installed = await client.post(
                        "/knowledge-updates/install",
                        data={"release_id": target_release_id},
                        headers={"Origin": "http://testserver"},
                        follow_redirects=False,
                    )
                    final_status: httpx.Response | None = None
                    final_payload: dict[str, Any] = {}
                    final_corpus: dict[str, Any] = {}
                    for _ in range(200):
                        final_status = await client.get("/status")
                        final_payload = (
                            final_status.json()
                            if final_status.status_code == 200
                            else {}
                        )
                        final_corpus = final_payload.get("corpus") or {}
                        if (
                            final_corpus.get("knowledge_release_id")
                            == target_release_id
                        ):
                            break
                        await asyncio.sleep(0.05)
                    update_passed = all(
                        (
                            checked.status_code == 303,
                            "Knowledge update available" in reviewed.text,
                            installed.status_code == 303,
                            final_status is not None,
                            final_corpus.get("knowledge_release_id") == target_release_id,
                        )
                    )
                    journey_status["update-installation"] = (
                        "passed" if update_passed else "failed"
                    )
                    if not update_passed:
                        record_failure(
                            "update-installation",
                            stage="update-installation-validation",
                            reason_code="journey-check-failed",
                        )
                    if update_passed:
                        corpus_identity = {
                            key: str(final_corpus[key])
                            for key in (
                                "knowledge_release_id",
                                "corpus_id",
                                "source_registry_version",
                                "embedding_model",
                                "embedding_vector_dimensions",
                                "index_schema_version",
                            )
                            if key in final_corpus
                        }
                except Exception as error:
                    update_passed = False
                    record_failure(
                        "update-installation",
                        stage="update-installation-request",
                        reason_code="journey-exception",
                        error=error,
                    )

    required_rollback_phases = [
        "verification",
        "extraction",
        "embedding",
        "indexing",
        "activation",
        "late_activation",
    ]
    try:
        expected_rollback_mode = "live" if mode == "live" else "fixture-non-live"
        rollback_results = rollback_evidence.get("results") or []
        rollback_passed = all(
            (
                rollback_evidence.get("monitor_id")
                == "knowledge-release-rollback-fault-matrix",
                rollback_evidence.get("mode") == expected_rollback_mode,
                rollback_evidence.get("passed") is True,
                [result.get("phase") for result in rollback_results]
                == required_rollback_phases,
                all(result.get("status") == "passed" for result in rollback_results),
            )
        )
    except Exception as error:
        rollback_passed = False
        record_failure(
            "rollback",
            stage="rollback-evidence-validation",
            reason_code="journey-exception",
            error=error,
        )
    journey_status["rollback"] = "passed" if rollback_passed else "failed"
    if not rollback_passed:
        record_failure(
            "rollback",
            stage="rollback-evidence-validation",
            reason_code="invalid-rollback-evidence",
        )

    for journey_id in journey_order:
        if (
            journey_status[journey_id] != "passed"
            and journey_id not in diagnostics_by_journey
        ):
            record_failure(
                journey_id,
                stage="prerequisite-check",
                reason_code="prerequisite-failed",
            )
    diagnostics = [
        diagnostics_by_journey[journey_id]
        for journey_id in journey_order
        if journey_id in diagnostics_by_journey
    ]

    journeys = [
        {"id": journey_id, "status": journey_status[journey_id]}
        for journey_id in journey_order
    ]
    failures = [journey["id"] for journey in journeys if journey["status"] != "passed"]
    passed = not failures
    live_execution = mode == "live"
    if mode == "fixture":
        provider_identity = {
            "provider_id": "fixture-local-provider",
            "provider_version": "fixture",
        }
        model_identity = {
            "generation_model": "fixture-generation",
            "embedding_model": str(embedding_policy["initial_supported"]),
        }
    else:
        provider_identity = {
            "provider_id": str(
                runtime_configuration.get("provider_id") or provider_policy["id"]
            ),
            "provider_version": str(runtime_configuration.get("provider_version") or ""),
        }
        detected_model_identity = runtime_configuration.get("model_identity")
        model_identity = {
            "generation_model": str(
                runtime_configuration.get("model") or generation_policy["initial"]
            ),
            "generation_model_identity": (
                dict(detected_model_identity)
                if isinstance(detected_model_identity, dict)
                else {}
            ),
            "embedding_model": str(embedding_policy["initial_supported"]),
        }

    return {
        "monitor_id": "supported-environment-critical-journeys",
        "mode": "live" if live_execution else "fixture-non-live",
        "qualification_scope": (
            "live-supported-environment" if live_execution else "non-live-fixture-only"
        ),
        "can_qualify_supported_environment": live_execution and passed,
        "live_provider_calls": live_execution,
        "supported_environment_identity": dict(
            policy["supported_environment"]["first_verified"]
        ),
        "provider_identity": provider_identity,
        "model_identity": model_identity,
        "corpus_identity": corpus_identity,
        "journeys": journeys,
        "failures": failures,
        "diagnostics": diagnostics,
        "passed": passed,
    }


async def run_release_qualification_monitors(
    *,
    mode: MonitorMode,
    generated_at_utc: str,
) -> dict[str, Any]:
    """Run privacy, rollback, and supported-environment monitors as one evidence set."""

    rollback = run_rollback_fault_matrix(mode=mode)
    network = await run_network_boundary_monitor(mode=mode)
    environment = await run_supported_environment_critical_journeys(
        mode=mode,
        rollback_evidence=rollback,
    )
    component_passed = all(
        result.get("passed") is True for result in (network, rollback, environment)
    )
    live_execution = mode == "live"
    return {
        "schema_version": "1.0",
        "generated_at_utc": generated_at_utc,
        "mode": "live" if live_execution else "fixture-non-live",
        "privacy": network,
        "rollback": rollback,
        "supported_environment": environment,
        "component_passed": component_passed,
        "strict_passed": (
            live_execution
            and component_passed
            and environment.get("can_qualify_supported_environment") is True
        ),
    }


def write_release_monitor_report(report: dict[str, Any], output_path: str | Path) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run release privacy, rollback, and supported-environment monitors."
    )
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--output", required=True)
    parser.add_argument("--generated-at-utc", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(
            run_release_qualification_monitors(
                mode=args.mode,
                generated_at_utc=args.generated_at_utc,
            )
        )
        write_release_monitor_report(report, args.output)
    except Exception as exc:
        print(f"release monitors failed: {exc}", file=sys.stderr)
        return 2
    if args.strict and not report["strict_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
