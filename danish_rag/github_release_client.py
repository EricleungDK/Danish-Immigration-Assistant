"""Bounded, content-free GitHub Releases transport for knowledge releases.

This module deliberately does not know about questions, answers, evidence, or
conversation records.  Listing published release metadata and downloading an
explicitly approved asset are separate public operations so the caller cannot
turn an update check into an implicit artifact download.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, ContextManager, Literal, Protocol, cast
from urllib.parse import ParseResult, unquote, urlparse

from .evidence_integrity import reject_duplicate_json_object


GITHUB_API_VERSION = "2026-03-10"
GITHUB_API_ORIGIN = "https://api.github.com"
DEFAULT_REPOSITORY = "EricleungDK/Danish-Immigration-Assistant"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
MAX_REQUEST_TIMEOUT_SECONDS = 30.0
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_RELEASES_PER_CHECK = 30
MAX_ASSETS_PER_RELEASE = 100
READ_CHUNK_BYTES = 64 * 1024

_REPOSITORY_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\Z"
)
_APPLICATION_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}\Z")
_SHA256_DIGEST_PATTERN = re.compile(r"sha256:([0-9a-fA-F]{64})\Z")
_ARTIFACT_REDIRECT_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "objects-origin.githubusercontent.com",
    "github-releases.githubusercontent.com",
    "release-assets.githubusercontent.com",
}

RequestPurpose = Literal["metadata", "artifact"]


class GitHubReleaseClientError(ValueError):
    """Raised when release-network input or output fails closed."""


@dataclass(frozen=True)
class GitHubReleaseAsset:
    """Content-free metadata for one uploaded GitHub Release asset."""

    repository: str
    release_tag: str
    asset_id: int
    name: str
    content_type: str
    size_bytes: int
    browser_download_url: str
    github_sha256: str | None


@dataclass(frozen=True)
class GitHubReleaseMetadata:
    """Published release metadata; untrusted free-form release prose is omitted."""

    repository: str
    github_release_id: int
    tag_name: str
    name: str
    published_at_utc: str
    html_url: str
    assets: tuple[GitHubReleaseAsset, ...]


@dataclass(frozen=True)
class ArtifactDownloadApproval:
    """Approval bound to the exact knowledge release and GitHub asset."""

    approved: bool
    requested_knowledge_release_id: str
    artifact_id: int
    artifact_name: str


@dataclass(frozen=True)
class DownloadedReleaseArtifact:
    """Locally written release artifact and its observed integrity metadata."""

    path: Path
    bytes_written: int
    sha256: str
    github_digest_verified: bool


class ReleaseNetworkResponse(Protocol):
    status: int
    headers: Any

    def read(self, size: int = -1, *, timeout_seconds: float) -> bytes: ...

    def geturl(self) -> str: ...


class ReleaseNetworkTransport(Protocol):
    """Injectable transport seam used by the release-network boundary."""

    def open(
        self,
        request: urllib.request.Request,
        *,
        timeout_seconds: float,
        purpose: RequestPurpose,
    ) -> ContextManager[ReleaseNetworkResponse]: ...


class GitHubReleaseClient:
    """Read GitHub release metadata and fetch only explicitly approved assets."""

    def __init__(
        self,
        *,
        repository: str = DEFAULT_REPOSITORY,
        application_version: str,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        max_metadata_bytes: int = MAX_METADATA_BYTES,
        max_artifact_bytes: int = MAX_ARTIFACT_BYTES,
        transport: ReleaseNetworkTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(
            repository
        ):
            raise GitHubReleaseClientError("GitHub repository must be an owner/name pair.")
        if not isinstance(
            application_version, str
        ) or not _APPLICATION_VERSION_PATTERN.fullmatch(application_version):
            raise GitHubReleaseClientError("Application version has an unsafe format.")
        if isinstance(request_timeout_seconds, bool) or not isinstance(
            request_timeout_seconds, (int, float)
        ):
            raise GitHubReleaseClientError("Request timeout must be a number.")
        if not 0 < float(request_timeout_seconds) <= MAX_REQUEST_TIMEOUT_SECONDS:
            raise GitHubReleaseClientError(
                f"Request timeout must be greater than zero and at most "
                f"{MAX_REQUEST_TIMEOUT_SECONDS:g} seconds."
            )
        self._require_size_limit(
            "Metadata",
            max_metadata_bytes,
            hard_maximum=MAX_METADATA_BYTES,
        )
        self._require_size_limit(
            "Artifact",
            max_artifact_bytes,
            hard_maximum=MAX_ARTIFACT_BYTES,
        )
        self.repository = repository
        self.application_version = application_version
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.max_metadata_bytes = max_metadata_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self._transport = transport or _SafeGitHubTransport(clock=clock)
        self._clock = clock

    def list_published_releases(self) -> tuple[GitHubReleaseMetadata, ...]:
        """List one bounded page of published, non-prerelease GitHub releases.

        This call retrieves metadata only.  It never follows an asset download
        URL and deliberately omits the free-form GitHub release body.
        """

        owner, repository_name = self.repository.split("/", 1)
        endpoint = f"{GITHUB_API_ORIGIN}/repos/{owner}/{repository_name}/releases"
        request = urllib.request.Request(
            endpoint,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": f"Danish-Immigration-RAG/{self.application_version}",
            },
            method="GET",
        )
        started = self._clock()
        try:
            with self._open(
                request,
                purpose="metadata",
                started=started,
            ) as response:
                self._require_success(response, purpose="metadata")
                self._require_final_url(response, purpose="metadata")
                body = self._read_limited(
                    response,
                    maximum_bytes=self.max_metadata_bytes,
                    started=started,
                    purpose="Release metadata",
                )
        except GitHubReleaseClientError:
            raise
        except (OSError, TimeoutError) as exc:
            raise GitHubReleaseClientError(
                "GitHub release metadata request failed."
            ) from exc
        return self._parse_releases(body)

    def download_artifact(
        self,
        asset: GitHubReleaseAsset,
        destination_dir: str | Path,
        *,
        approval: ArtifactDownloadApproval | None,
    ) -> DownloadedReleaseArtifact:
        """Download one approved asset without unpacking or activating it."""

        self._validate_asset(asset)
        self._require_matching_approval(asset, approval)
        if asset.size_bytes > self.max_artifact_bytes:
            raise GitHubReleaseClientError(
                "Release artifact exceeds the configured size limit."
            )

        resolved_destination_dir = Path(destination_dir)
        try:
            resolved_destination_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise GitHubReleaseClientError(
                "Could not prepare the local release download directory."
            ) from exc
        destination = resolved_destination_dir / asset.name
        if destination.exists():
            raise GitHubReleaseClientError(
                "Refusing to overwrite an existing release artifact."
            )
        temporary = resolved_destination_dir / (
            f".{asset.name}.part-{uuid.uuid4().hex}"
        )
        request = urllib.request.Request(
            asset.browser_download_url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": f"Danish-Immigration-RAG/{self.application_version}",
            },
            method="GET",
        )
        started = self._clock()
        digest = hashlib.sha256()
        bytes_written = 0
        try:
            with self._open(
                request,
                purpose="artifact",
                started=started,
            ) as response:
                self._require_success(response, purpose="artifact")
                self._require_final_url(response, purpose="artifact")
                content_length = self._content_length(response)
                if content_length is not None:
                    if content_length > self.max_artifact_bytes:
                        raise GitHubReleaseClientError(
                            "Release artifact exceeds the configured size limit."
                        )
                    if content_length != asset.size_bytes:
                        raise GitHubReleaseClientError(
                            "Release artifact byte count differs from GitHub metadata."
                        )
                with temporary.open("xb") as output:
                    while content_length is None or bytes_written < content_length:
                        chunk = response.read(
                            READ_CHUNK_BYTES,
                            timeout_seconds=self._remaining_timeout(
                                started,
                                "Release artifact",
                            ),
                        )
                        self._require_within_elapsed_budget(started, "Release artifact")
                        if not chunk:
                            break
                        if not isinstance(chunk, bytes):
                            raise GitHubReleaseClientError(
                                "Release artifact response was not bytes."
                            )
                        bytes_written += len(chunk)
                        if bytes_written > self.max_artifact_bytes:
                            raise GitHubReleaseClientError(
                                "Release artifact exceeds the configured size limit."
                            )
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())

            if bytes_written != asset.size_bytes:
                raise GitHubReleaseClientError(
                    "Release artifact byte count differs from GitHub metadata."
                )
            observed_sha256 = digest.hexdigest()
            if asset.github_sha256 is not None and observed_sha256 != asset.github_sha256:
                raise GitHubReleaseClientError(
                    "Release artifact digest differs from GitHub metadata."
                )
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise GitHubReleaseClientError(
                    "Refusing to overwrite an existing release artifact."
                ) from exc
            except OSError as exc:
                raise GitHubReleaseClientError(
                    "Could not finalize the local release artifact."
                ) from exc
            return DownloadedReleaseArtifact(
                path=destination,
                bytes_written=bytes_written,
                sha256=observed_sha256,
                github_digest_verified=asset.github_sha256 is not None,
            )
        except GitHubReleaseClientError:
            raise
        except (OSError, TimeoutError) as exc:
            raise GitHubReleaseClientError(
                "Release artifact download failed locally."
            ) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _parse_releases(self, encoded: bytes) -> tuple[GitHubReleaseMetadata, ...]:
        try:
            payload = json.loads(
                encoded.decode("utf-8"),
                object_pairs_hook=reject_duplicate_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise GitHubReleaseClientError(
                "GitHub release metadata must be unambiguous valid JSON."
            ) from exc
        if not isinstance(payload, list):
            raise GitHubReleaseClientError(
                "GitHub release metadata must be a JSON array."
            )
        if len(payload) > MAX_RELEASES_PER_CHECK:
            raise GitHubReleaseClientError(
                "GitHub returned more releases than the bounded request allowed."
            )

        releases: list[GitHubReleaseMetadata] = []
        for raw_release in payload:
            if not isinstance(raw_release, dict):
                raise GitHubReleaseClientError("GitHub release entry must be an object.")
            draft = raw_release.get("draft")
            prerelease = raw_release.get("prerelease")
            if not isinstance(draft, bool) or not isinstance(prerelease, bool):
                raise GitHubReleaseClientError(
                    "GitHub release publication state is invalid."
                )
            if draft or prerelease:
                continue
            releases.append(self._parse_published_release(raw_release))
        return tuple(releases)

    def _parse_published_release(
        self, raw_release: dict[str, Any]
    ) -> GitHubReleaseMetadata:
        github_release_id = _require_positive_integer(
            raw_release.get("id"), "GitHub release ID"
        )
        tag_name = _require_safe_text(
            raw_release.get("tag_name"), "GitHub release tag", maximum_length=255
        )
        name_value = raw_release.get("name")
        name = (
            tag_name
            if name_value is None
            else _require_safe_text(
                name_value, "GitHub release name", maximum_length=255
            )
        )
        published_at = _require_publication_time(
            raw_release.get("published_at"),
        )
        html_url = _require_safe_text(
            raw_release.get("html_url"),
            "GitHub release page URL",
            maximum_length=2048,
        )
        _require_repository_release_page(
            html_url,
            repository=self.repository,
            release_tag=tag_name,
        )
        raw_assets = raw_release.get("assets")
        if not isinstance(raw_assets, list):
            raise GitHubReleaseClientError("GitHub release assets must be an array.")
        if len(raw_assets) > MAX_ASSETS_PER_RELEASE:
            raise GitHubReleaseClientError(
                "GitHub release contains too many asset records."
            )
        assets: list[GitHubReleaseAsset] = []
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                raise GitHubReleaseClientError(
                    "GitHub release asset entry must be an object."
                )
            if raw_asset.get("state") != "uploaded":
                continue
            asset = self._parse_asset(tag_name, raw_asset)
            assets.append(asset)
        return GitHubReleaseMetadata(
            repository=self.repository,
            github_release_id=github_release_id,
            tag_name=tag_name,
            name=name,
            published_at_utc=published_at,
            html_url=html_url,
            assets=tuple(assets),
        )

    def _parse_asset(
        self,
        release_tag: str,
        raw_asset: dict[str, Any],
    ) -> GitHubReleaseAsset:
        asset_id = _require_positive_integer(raw_asset.get("id"), "GitHub asset ID")
        name = _require_safe_asset_name(raw_asset.get("name"))
        content_type = _require_safe_text(
            raw_asset.get("content_type"),
            "GitHub asset content type",
            maximum_length=255,
        )
        size = raw_asset.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise GitHubReleaseClientError("GitHub asset size must be non-negative.")
        browser_download_url = _require_safe_text(
            raw_asset.get("browser_download_url"),
            "GitHub asset download URL",
            maximum_length=4096,
        )
        _require_repository_asset_url(
            browser_download_url,
            repository=self.repository,
            release_tag=release_tag,
            asset_name=name,
        )
        raw_digest = raw_asset.get("digest")
        github_sha256: str | None
        if raw_digest is None:
            github_sha256 = None
        elif isinstance(raw_digest, str) and (
            digest_match := _SHA256_DIGEST_PATTERN.fullmatch(raw_digest)
        ):
            github_sha256 = digest_match.group(1).lower()
        else:
            raise GitHubReleaseClientError(
                "GitHub asset digest must be a SHA-256 digest when present."
            )
        asset = GitHubReleaseAsset(
            repository=self.repository,
            release_tag=release_tag,
            asset_id=asset_id,
            name=name,
            content_type=content_type,
            size_bytes=size,
            browser_download_url=browser_download_url,
            github_sha256=github_sha256,
        )
        self._validate_asset(asset)
        return asset

    def _validate_asset(self, asset: GitHubReleaseAsset) -> None:
        if not isinstance(asset, GitHubReleaseAsset):
            raise GitHubReleaseClientError("Release asset metadata is invalid.")
        if asset.repository != self.repository:
            raise GitHubReleaseClientError(
                "Release asset belongs to a different GitHub repository."
            )
        _require_safe_text(asset.release_tag, "GitHub release tag", maximum_length=255)
        _require_positive_integer(asset.asset_id, "GitHub asset ID")
        _require_safe_asset_name(asset.name)
        if (
            isinstance(asset.size_bytes, bool)
            or not isinstance(asset.size_bytes, int)
            or asset.size_bytes < 0
        ):
            raise GitHubReleaseClientError("GitHub asset size must be non-negative.")
        _require_repository_asset_url(
            asset.browser_download_url,
            repository=self.repository,
            release_tag=asset.release_tag,
            asset_name=asset.name,
        )
        if asset.github_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", asset.github_sha256
        ):
            raise GitHubReleaseClientError("GitHub asset SHA-256 is invalid.")

    def _require_matching_approval(
        self,
        asset: GitHubReleaseAsset,
        approval: ArtifactDownloadApproval | None,
    ) -> None:
        if approval is None or approval.approved is not True:
            raise GitHubReleaseClientError(
                "Release artifact download requires explicit user approval."
            )
        if (
            approval.requested_knowledge_release_id != asset.release_tag
            or approval.artifact_id != asset.asset_id
            or approval.artifact_name != asset.name
        ):
            raise GitHubReleaseClientError(
                "Release artifact approval does not match the requested asset."
            )

    def _open(
        self,
        request: urllib.request.Request,
        *,
        purpose: RequestPurpose,
        started: float,
    ) -> ContextManager[ReleaseNetworkResponse]:
        try:
            return self._transport.open(
                request,
                timeout_seconds=self._remaining_timeout(
                    started,
                    f"GitHub release {purpose}",
                ),
                purpose=purpose,
            )
        except GitHubReleaseClientError:
            raise
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            raise GitHubReleaseClientError(
                f"GitHub release {purpose} request failed."
            ) from exc

    def _read_limited(
        self,
        response: ReleaseNetworkResponse,
        *,
        maximum_bytes: int,
        started: float,
        purpose: str,
    ) -> bytes:
        content_length = self._content_length(response)
        if content_length is not None and content_length > maximum_bytes:
            raise GitHubReleaseClientError(
                f"{purpose} exceeds the configured size limit."
            )
        chunks: list[bytes] = []
        total = 0
        while content_length is None or total < content_length:
            chunk = response.read(
                min(READ_CHUNK_BYTES, maximum_bytes - total + 1),
                timeout_seconds=self._remaining_timeout(started, purpose),
            )
            self._require_within_elapsed_budget(started, purpose)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise GitHubReleaseClientError(f"{purpose} response was not bytes.")
            total += len(chunk)
            if total > maximum_bytes:
                raise GitHubReleaseClientError(
                    f"{purpose} exceeds the configured size limit."
                )
            chunks.append(chunk)
        if content_length is not None and total != content_length:
            raise GitHubReleaseClientError(
                f"{purpose} byte count differs from Content-Length."
            )
        return b"".join(chunks)

    def _require_within_elapsed_budget(self, started: float, purpose: str) -> None:
        self._remaining_timeout(started, purpose)

    def _remaining_timeout(self, started: float, purpose: str) -> float:
        remaining = self.request_timeout_seconds - (self._clock() - started)
        if remaining <= 0:
            raise GitHubReleaseClientError(
                f"{purpose} request exceeded the configured elapsed-time budget."
            )
        return min(self.request_timeout_seconds, remaining)

    @staticmethod
    def _content_length(response: ReleaseNetworkResponse) -> int | None:
        value = response.headers.get("Content-Length")
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise GitHubReleaseClientError(
                "Release response Content-Length is invalid."
            ) from exc
        if parsed < 0:
            raise GitHubReleaseClientError(
                "Release response Content-Length is invalid."
            )
        return parsed

    @staticmethod
    def _require_success(
        response: ReleaseNetworkResponse,
        *,
        purpose: RequestPurpose,
    ) -> None:
        status = getattr(response, "status", None)
        if not isinstance(status, int) or not 200 <= status < 300:
            raise GitHubReleaseClientError(
                f"GitHub release {purpose} request did not return success."
            )

    def _require_final_url(
        self,
        response: ReleaseNetworkResponse,
        *,
        purpose: RequestPurpose,
    ) -> None:
        try:
            final_url = response.geturl()
        except (AttributeError, TypeError, ValueError) as exc:
            raise GitHubReleaseClientError(
                "Release response did not identify its final HTTPS origin."
            ) from exc
        _require_allowed_url(final_url, purpose=purpose)
        if purpose == "metadata":
            parsed = urlparse(final_url)
            owner, repository_name = self.repository.split("/", 1)
            expected_path = f"/repos/{owner}/{repository_name}/releases"
            if parsed.path.casefold() != expected_path.casefold():
                raise GitHubReleaseClientError(
                    "GitHub metadata response came from an unexpected endpoint."
                )

    @staticmethod
    def _require_size_limit(name: str, value: int, *, hard_maximum: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= hard_maximum:
            raise GitHubReleaseClientError(
                f"{name} size limit must be positive and at most {hard_maximum} bytes."
            )


class _SafeGitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 2

    def __init__(
        self,
        purpose: RequestPurpose,
        *,
        deadline: float,
        clock: Callable[[], float],
    ) -> None:
        super().__init__()
        self._purpose = purpose
        self._deadline = deadline
        self._clock = clock

    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _require_allowed_url(newurl, purpose=self._purpose)
        remaining = _remaining_deadline_timeout(
            self._deadline,
            self._clock,
            f"GitHub release {self._purpose}",
        )
        _set_response_socket_timeout(fp, remaining)
        request.timeout = remaining
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class _DeadlineHTTPSRequestHandler(urllib.request.BaseHandler):
    """Refresh urllib's socket timeout before each redirect connection."""

    handler_order = 100

    def __init__(self, *, deadline: float, clock: Callable[[], float]) -> None:
        self._deadline = deadline
        self._clock = clock

    def https_request(
        self,
        request: urllib.request.Request,
    ) -> urllib.request.Request:
        request.timeout = _remaining_deadline_timeout(
            self._deadline,
            self._clock,
            "GitHub release network",
        )
        return request


class _SafeGitHubTransport:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def open(
        self,
        request: urllib.request.Request,
        *,
        timeout_seconds: float,
        purpose: RequestPurpose,
    ) -> ContextManager[ReleaseNetworkResponse]:
        _require_allowed_url(request.full_url, purpose=purpose)
        deadline = self._clock() + timeout_seconds
        opener = urllib.request.build_opener(
            _DeadlineHTTPSRequestHandler(deadline=deadline, clock=self._clock),
            _SafeGitHubRedirectHandler(
                purpose,
                deadline=deadline,
                clock=self._clock,
            ),
        )
        response = opener.open(
            request,
            timeout=_remaining_deadline_timeout(
                deadline,
                self._clock,
                f"GitHub release {purpose}",
            ),
        )
        return _DeadlineBoundNetworkResponse(response)


class _DeadlineBoundNetworkResponse:
    """Adapt urllib responses so every blocking read receives its remaining budget."""

    def __init__(self, response: Any) -> None:
        self._response = response

    @property
    def status(self) -> Any:
        return self._response.status

    @property
    def headers(self) -> Any:
        return self._response.headers

    def read(self, size: int = -1, *, timeout_seconds: float) -> bytes:
        _set_response_socket_timeout(self._response, timeout_seconds)
        return cast(bytes, self._response.read(size))

    def geturl(self) -> str:
        return cast(str, self._response.geturl())

    def __enter__(self) -> "_DeadlineBoundNetworkResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._response.close()


def _set_response_socket_timeout(response: Any, timeout_seconds: float) -> None:
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    socket_candidate = getattr(raw, "_sock", None)
    settimeout = getattr(socket_candidate, "settimeout", None)
    if not callable(settimeout):
        raise GitHubReleaseClientError(
            "Could not enforce the release response elapsed-time budget."
        )
    try:
        settimeout(timeout_seconds)
    except (OSError, TypeError, ValueError) as exc:
        raise GitHubReleaseClientError(
            "Could not enforce the release response elapsed-time budget."
        ) from exc


def _remaining_deadline_timeout(
    deadline: float,
    clock: Callable[[], float],
    purpose: str,
) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise GitHubReleaseClientError(
            f"{purpose} request exceeded the configured elapsed-time budget."
        )
    return remaining


def _require_allowed_url(url: str, *, purpose: RequestPurpose) -> None:
    parsed, host, port = _parse_url_authority(url)
    if parsed.scheme != "https" or not host:
        raise GitHubReleaseClientError("Release network URL must use HTTPS.")
    if parsed.username is not None or parsed.password is not None or port not in {
        None,
        443,
    }:
        raise GitHubReleaseClientError("Release network URL has an unsafe authority.")
    if purpose == "metadata":
        if host != "api.github.com":
            raise GitHubReleaseClientError(
                "Release metadata URL must use the GitHub API origin."
            )
    elif host not in _ARTIFACT_REDIRECT_HOSTS:
        raise GitHubReleaseClientError(
            "Release artifact redirect left GitHub-controlled HTTPS origins."
        )


def _require_repository_release_page(
    url: str,
    *,
    repository: str,
    release_tag: str,
) -> None:
    parsed, host, port = _parse_url_authority(url)
    owner, repository_name = repository.split("/", 1)
    expected_prefix = f"/{owner}/{repository_name}/releases/tag/"
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or host != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not decoded_path.casefold().startswith(expected_prefix.casefold())
        or decoded_path[len(expected_prefix) :] != release_tag
        or parsed.query
        or parsed.fragment
    ):
        raise GitHubReleaseClientError(
            "GitHub release page URL does not belong to the configured repository."
        )


def _require_repository_asset_url(
    url: str,
    *,
    repository: str,
    release_tag: str,
    asset_name: str,
) -> None:
    parsed, host, port = _parse_url_authority(url)
    owner, repository_name = repository.split("/", 1)
    decoded_path = unquote(parsed.path)
    expected_prefix = f"/{owner}/{repository_name}/releases/download/"
    release_and_asset = decoded_path[len(expected_prefix) :]
    path_release_tag, separator, path_asset_name = release_and_asset.rpartition("/")
    if (
        parsed.scheme != "https"
        or host != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not decoded_path.casefold().startswith(expected_prefix.casefold())
        or not separator
        or path_release_tag != release_tag
        or path_asset_name != asset_name
        or parsed.query
        or parsed.fragment
    ):
        raise GitHubReleaseClientError(
            "GitHub asset URL does not belong to the configured repository."
        )


def _parse_url_authority(url: Any) -> tuple[ParseResult, str, int | None]:
    if not isinstance(url, str):
        raise GitHubReleaseClientError("Release network URL must be text.")
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError as exc:
        raise GitHubReleaseClientError(
            "Release network URL has an invalid authority."
        ) from exc
    return parsed, host, port


def _require_positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GitHubReleaseClientError(f"{name} must be a positive integer.")
    return value


def _require_safe_text(value: Any, name: str, *, maximum_length: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum_length
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise GitHubReleaseClientError(f"{name} has an unsafe format.")
    return value


def _require_safe_asset_name(value: Any) -> str:
    name = _require_safe_text(value, "GitHub asset name", maximum_length=255)
    if name in {".", ".."} or Path(name).name != name or "\\" in name:
        raise GitHubReleaseClientError("GitHub asset name is not a safe filename.")
    return name


def _require_publication_time(value: Any) -> str:
    published_at = _require_safe_text(
        value,
        "GitHub release publication time",
        maximum_length=40,
    )
    try:
        datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise GitHubReleaseClientError(
            "GitHub release publication time must be an RFC 3339 UTC timestamp."
        ) from exc
    return published_at

