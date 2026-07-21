import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from danish_rag.github_release_client import (
    ArtifactDownloadApproval,
    GitHubReleaseClient,
    GitHubReleaseClientError,
)


REPOSITORY = "EricleungDK/Danish-Immigration-Assistant"
RELEASE_TAG = "kr-2026-07-07.1"
ASSET_NAME = f"{RELEASE_TAG}.zip"
ASSET_URL = (
    "https://github.com/EricleungDK/Danish-Immigration-Assistant/"
    f"releases/download/{RELEASE_TAG}/{ASSET_NAME}"
)


class ManualClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class ScriptedClock:
    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._last = values[-1]

    def __call__(self) -> float:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
        clock: ManualClock | None = None,
        seconds_per_read: float = 0.0,
    ) -> None:
        self._body = io.BytesIO(body)
        self._url = url
        self.status = status
        self.headers = headers or {"Content-Length": str(len(body))}
        self._clock = clock
        self._seconds_per_read = seconds_per_read

    def read(self, size: int = -1, *, timeout_seconds: float | None = None) -> bytes:
        if self._clock is not None:
            self._clock.value += self._seconds_per_read
        return self._body.read(size)

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class RecordingTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def open(self, request, *, timeout_seconds: float, purpose: str):
        self.calls.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "data": request.data,
                "method": request.get_method(),
                "timeout_seconds": timeout_seconds,
                "purpose": purpose,
            }
        )
        if not self.responses:
            raise AssertionError("No fake response remains.")
        return self.responses.pop(0)


class DelayedRecordingTransport(RecordingTransport):
    def __init__(
        self,
        responses: list[FakeResponse],
        *,
        clock: ManualClock,
        open_seconds: float,
    ) -> None:
        super().__init__(responses)
        self._clock = clock
        self._open_seconds = open_seconds

    def open(self, request, *, timeout_seconds: float, purpose: str):
        response = super().open(
            request,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
        )
        self._clock.value += self._open_seconds
        return response


class MalformedPortTransport:
    def open(self, request, *, timeout_seconds: float, purpose: str):
        raise ValueError("Port could not be cast to an integer")


class DeadlineEnforcingResponse(FakeResponse):
    def __init__(
        self,
        body: bytes,
        *,
        url: str,
        clock: ManualClock,
        seconds_per_read: float,
    ) -> None:
        super().__init__(body, url=url, headers={"Content-Type": "application/json"})
        self._clock = clock
        self._seconds_per_read = seconds_per_read
        self.read_timeouts: list[float] = []

    def read(self, size: int = -1, *, timeout_seconds: float) -> bytes:
        self.read_timeouts.append(timeout_seconds)
        if self._seconds_per_read >= timeout_seconds:
            self._clock.value += timeout_seconds
            raise TimeoutError("simulated deadline-bound socket read")
        self._clock.value += self._seconds_per_read
        return self._body.read(size)


class ReadFailureResponse(FakeResponse):
    def read(self, size: int = -1, *, timeout_seconds: float | None = None) -> bytes:
        raise TimeoutError("simulated socket read timeout")


class MalformedPortResponse(FakeResponse):
    def geturl(self) -> str:
        raise ValueError("Port could not be cast to an integer")


def release_payload(
    artifact: bytes = b"signed release archive",
    *,
    browser_download_url: str = ASSET_URL,
    asset_size: int | None = None,
    digest: str | None = None,
) -> list[dict[str, Any]]:
    import hashlib

    return [
        {
            "id": 8101,
            "tag_name": RELEASE_TAG,
            "name": "Reviewed knowledge release",
            "html_url": (
                "https://github.com/EricleungDK/Danish-Immigration-Assistant/"
                f"releases/tag/{RELEASE_TAG}"
            ),
            "draft": False,
            "prerelease": False,
            "published_at": "2026-07-07T13:00:00Z",
            "body": "Untrusted release prose is deliberately not exposed by the client.",
            "assets": [
                {
                    "id": 9101,
                    "name": ASSET_NAME,
                    "state": "uploaded",
                    "content_type": "application/zip",
                    "size": len(artifact) if asset_size is None else asset_size,
                    "digest": digest
                    or f"sha256:{hashlib.sha256(artifact).hexdigest()}",
                    "browser_download_url": browser_download_url,
                }
            ],
        },
        {
            "id": 8100,
            "tag_name": "kr-2026-07-07.0-rc",
            "name": "Draft candidate",
            "html_url": (
                "https://github.com/EricleungDK/Danish-Immigration-Assistant/"
                "releases/tag/kr-2026-07-07.0-rc"
            ),
            "draft": True,
            "prerelease": False,
            "published_at": None,
            "assets": [],
        },
        {
            "id": 8099,
            "tag_name": "kr-2026-07-06.9-rc",
            "name": "Prerelease candidate",
            "html_url": (
                "https://github.com/EricleungDK/Danish-Immigration-Assistant/"
                "releases/tag/kr-2026-07-06.9-rc"
            ),
            "draft": False,
            "prerelease": True,
            "published_at": "2026-07-06T13:00:00Z",
            "assets": [],
        },
    ]


def encoded_payload(**kwargs: Any) -> bytes:
    return json.dumps(release_payload(**kwargs)).encode("utf-8")


class GitHubReleaseClientTests(unittest.TestCase):
    def test_discovery_reads_only_published_metadata_with_content_free_request(self):
        body = encoded_payload()
        transport = RecordingTransport(
            [
                FakeResponse(
                    body,
                    url=(
                        "https://api.github.com/repos/EricleungDK/"
                        "Danish-Immigration-Assistant/releases"
                    ),
                )
            ]
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=transport,
        )

        releases = client.list_published_releases()

        self.assertEqual(len(releases), 1)
        release = releases[0]
        self.assertEqual(release.tag_name, RELEASE_TAG)
        self.assertEqual(release.published_at_utc, "2026-07-07T13:00:00Z")
        self.assertEqual(len(release.assets), 1)
        self.assertEqual(release.assets[0].name, ASSET_NAME)
        self.assertFalse(hasattr(release, "body"))
        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["purpose"], "metadata")
        self.assertEqual(call["method"], "GET")
        self.assertEqual(
            call["url"],
            "https://api.github.com/repos/EricleungDK/"
            "Danish-Immigration-Assistant/releases",
        )
        self.assertIsNone(call["data"])
        self.assertGreater(call["timeout_seconds"], 0.0)
        self.assertLessEqual(call["timeout_seconds"], 10.0)
        serialized = json.dumps(call, sort_keys=True).casefold()
        for prohibited in {
            "question",
            "normalized_question",
            "answer",
            "evidence",
            "conversation_id",
            "conversation_record",
            "citation_id",
            "prompt",
            "messages",
        }:
            self.assertNotIn(prohibited, serialized)
        self.assertEqual(
            call["headers"]["Accept"],
            "application/vnd.github+json",
        )
        self.assertEqual(call["headers"]["X-github-api-version"], "2026-03-10")

    def test_artifact_download_refuses_absent_false_or_mismatched_approval_before_network(self):
        metadata = encoded_payload()
        transport = RecordingTransport(
            [
                FakeResponse(
                    metadata,
                    url=(
                        "https://api.github.com/repos/EricleungDK/"
                        "Danish-Immigration-Assistant/releases"
                    ),
                )
            ]
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=transport,
        )
        asset = client.list_published_releases()[0].assets[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            for approval in (
                None,
                ArtifactDownloadApproval(
                    approved=False,
                    requested_knowledge_release_id=RELEASE_TAG,
                    artifact_id=asset.asset_id,
                    artifact_name=ASSET_NAME,
                ),
                ArtifactDownloadApproval(
                    approved=True,
                    requested_knowledge_release_id="kr-2026-07-08.1",
                    artifact_id=asset.asset_id,
                    artifact_name=ASSET_NAME,
                ),
                ArtifactDownloadApproval(
                    approved=True,
                    requested_knowledge_release_id=RELEASE_TAG,
                    artifact_id=asset.asset_id,
                    artifact_name="different.zip",
                ),
            ):
                with self.subTest(approval=approval):
                    with self.assertRaisesRegex(
                        GitHubReleaseClientError,
                        "explicit user approval|does not match",
                    ):
                        client.download_artifact(
                            asset,
                            tmpdir,
                            approval=approval,
                        )

        self.assertEqual(len(transport.calls), 1)

    def test_approved_artifact_download_is_bounded_verified_and_written_atomically(self):
        import hashlib

        artifact = b"signed release archive"
        metadata = encoded_payload(artifact=artifact)
        transport = RecordingTransport(
            [
                FakeResponse(
                    metadata,
                    url=(
                        "https://api.github.com/repos/EricleungDK/"
                        "Danish-Immigration-Assistant/releases"
                    ),
                ),
                FakeResponse(
                    artifact,
                    url=(
                        "https://release-assets.githubusercontent.com/"
                        "github-production-release-asset/fixture"
                    ),
                ),
            ]
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=transport,
        )
        asset = client.list_published_releases()[0].assets[0]
        approval = ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=RELEASE_TAG,
            artifact_id=asset.asset_id,
            artifact_name=asset.name,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = client.download_artifact(asset, tmpdir, approval=approval)
            self.assertEqual(result.path, Path(tmpdir) / ASSET_NAME)
            self.assertEqual(result.path.read_bytes(), artifact)
            self.assertEqual(result.bytes_written, len(artifact))
            self.assertEqual(result.sha256, hashlib.sha256(artifact).hexdigest())
            self.assertTrue(result.github_digest_verified)
            self.assertEqual(list(Path(tmpdir).glob("*.part-*")), [])

        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[1]["purpose"], "artifact")
        self.assertEqual(transport.calls[1]["url"], ASSET_URL)
        serialized = json.dumps(transport.calls[1], sort_keys=True).casefold()
        self.assertNotIn("question", serialized)
        self.assertNotIn("conversation", serialized)
        self.assertNotIn("evidence", serialized)

    def test_metadata_and_artifact_size_limits_fail_closed_without_partial_file(self):
        oversized_metadata = RecordingTransport(
            [
                FakeResponse(
                    b"[]",
                    url=(
                        "https://api.github.com/repos/EricleungDK/"
                        "Danish-Immigration-Assistant/releases"
                    ),
                    headers={"Content-Length": str(2 * 1024 * 1024 + 1)},
                )
            ]
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=oversized_metadata,
        )
        with self.assertRaisesRegex(GitHubReleaseClientError, "size limit"):
            client.list_published_releases()

        declared_too_large = encoded_payload(asset_size=256 * 1024 * 1024 + 1)
        artifact_transport = RecordingTransport(
            [
                FakeResponse(
                    declared_too_large,
                    url=(
                        "https://api.github.com/repos/EricleungDK/"
                        "Danish-Immigration-Assistant/releases"
                    ),
                )
            ]
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=artifact_transport,
        )
        asset = client.list_published_releases()[0].assets[0]
        approval = ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=RELEASE_TAG,
            artifact_id=asset.asset_id,
            artifact_name=asset.name,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(GitHubReleaseClientError, "size limit"):
                client.download_artifact(asset, tmpdir, approval=approval)
            self.assertEqual(list(Path(tmpdir).iterdir()), [])
        self.assertEqual(len(artifact_transport.calls), 1)

    def test_elapsed_budget_and_socket_timeout_configuration_are_bounded(self):
        clock = ManualClock()
        body = encoded_payload()
        transport = RecordingTransport(
            [
                FakeResponse(
                    body,
                    url=(
                        "https://api.github.com/repos/EricleungDK/"
                        "Danish-Immigration-Assistant/releases"
                    ),
                    clock=clock,
                    seconds_per_read=10.1,
                )
            ]
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=transport,
            clock=clock,
        )

        with self.assertRaisesRegex(GitHubReleaseClientError, "elapsed-time budget"):
            client.list_published_releases()

        with self.assertRaisesRegex(GitHubReleaseClientError, "at most 30"):
            GitHubReleaseClient(
                repository=REPOSITORY,
                application_version="0.1.0",
                request_timeout_seconds=30.1,
            )

    def test_metadata_open_receives_only_the_remaining_total_deadline(self):
        endpoint = (
            "https://api.github.com/repos/EricleungDK/"
            "Danish-Immigration-Assistant/releases"
        )
        clock = ScriptedClock([100.0, 102.25])
        transport = RecordingTransport([FakeResponse(b"[]", url=endpoint)])
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            request_timeout_seconds=10.0,
            transport=transport,
            clock=clock,
        )

        self.assertEqual(client.list_published_releases(), ())

        self.assertEqual(transport.calls[0]["timeout_seconds"], 7.75)

    def test_repeated_metadata_reads_share_one_total_deadline(self):
        endpoint = (
            "https://api.github.com/repos/EricleungDK/"
            "Danish-Immigration-Assistant/releases"
        )
        clock = ManualClock()
        response = DeadlineEnforcingResponse(
            b"[" + (b" " * 80_000),
            url=endpoint,
            clock=clock,
            seconds_per_read=4.0,
        )
        transport = DelayedRecordingTransport(
            [response],
            clock=clock,
            open_seconds=4.0,
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            request_timeout_seconds=10.0,
            transport=transport,
            clock=clock,
        )

        with self.assertRaisesRegex(GitHubReleaseClientError, "request failed"):
            client.list_published_releases()

        self.assertEqual(transport.calls[0]["timeout_seconds"], 10.0)
        self.assertEqual(response.read_timeouts, [6.0, 2.0])
        self.assertEqual(clock.value, 10.0)

    def test_repeated_artifact_reads_share_one_total_deadline_and_clean_up(self):
        endpoint = (
            "https://api.github.com/repos/EricleungDK/"
            "Danish-Immigration-Assistant/releases"
        )
        artifact = b"a" * 80_000
        clock = ManualClock()
        artifact_response = DeadlineEnforcingResponse(
            artifact,
            url=(
                "https://release-assets.githubusercontent.com/"
                "github-production-release-asset/fixture"
            ),
            clock=clock,
            seconds_per_read=4.0,
        )
        transport = DelayedRecordingTransport(
            [
                FakeResponse(encoded_payload(artifact=artifact), url=endpoint),
                artifact_response,
            ],
            clock=clock,
            open_seconds=4.0,
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            request_timeout_seconds=10.0,
            transport=transport,
            clock=clock,
        )
        asset = client.list_published_releases()[0].assets[0]
        approval = ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=RELEASE_TAG,
            artifact_id=asset.asset_id,
            artifact_name=asset.name,
        )
        clock.value = 0.0

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(
                GitHubReleaseClientError,
                "download failed locally",
            ):
                client.download_artifact(asset, tmpdir, approval=approval)
            self.assertEqual(list(Path(tmpdir).iterdir()), [])

        self.assertEqual(artifact_response.read_timeouts, [6.0, 2.0])
        self.assertEqual(clock.value, 10.0)

    def test_untrusted_repository_asset_url_and_malformed_digest_are_rejected(self):
        for payload in (
            encoded_payload(
                browser_download_url=(
                    "https://attacker.example/releases/download/"
                    f"{RELEASE_TAG}/{ASSET_NAME}"
                )
            ),
            encoded_payload(
                browser_download_url=(
                    "https://github.com:not-a-port/EricleungDK/"
                    "Danish-Immigration-Assistant/releases/download/"
                    f"{RELEASE_TAG}/{ASSET_NAME}"
                )
            ),
            encoded_payload(digest="sha512:deadbeef"),
        ):
            transport = RecordingTransport(
                [
                    FakeResponse(
                        payload,
                        url=(
                            "https://api.github.com/repos/EricleungDK/"
                            "Danish-Immigration-Assistant/releases"
                        ),
                    )
                ]
            )
            client = GitHubReleaseClient(
                repository=REPOSITORY,
                application_version="0.1.0",
                transport=transport,
            )
            with self.assertRaises(GitHubReleaseClientError):
                client.list_published_releases()

    def test_malformed_final_response_port_fails_with_the_domain_error(self):
        endpoint = (
            "https://api.github.com/repos/EricleungDK/"
            "Danish-Immigration-Assistant/releases"
        )
        transport = RecordingTransport(
            [MalformedPortResponse(b"[]", url=endpoint)]
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=transport,
        )

        with self.assertRaisesRegex(
            GitHubReleaseClientError,
            "final HTTPS origin",
        ):
            client.list_published_releases()

    def test_malformed_port_during_open_fails_with_the_domain_error(self):
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=MalformedPortTransport(),
        )

        with self.assertRaisesRegex(
            GitHubReleaseClientError,
            "metadata request failed",
        ):
            client.list_published_releases()

    def test_metadata_rejects_cross_tag_asset_and_invalid_publication_time(self):
        wrong_tag_payload = release_payload()
        wrong_tag_payload[0]["assets"][0]["browser_download_url"] = (
            "https://github.com/EricleungDK/Danish-Immigration-Assistant/"
            f"releases/download/kr-2026-07-08.1/{ASSET_NAME}"
        )
        invalid_time_payload = release_payload()
        invalid_time_payload[0]["published_at"] = "tomorrow"

        for payload in (wrong_tag_payload, invalid_time_payload):
            encoded = json.dumps(payload).encode("utf-8")
            transport = RecordingTransport(
                [
                    FakeResponse(
                        encoded,
                        url=(
                            "https://api.github.com/repos/EricleungDK/"
                            "Danish-Immigration-Assistant/releases"
                        ),
                    )
                ]
            )
            client = GitHubReleaseClient(
                repository=REPOSITORY,
                application_version="0.1.0",
                transport=transport,
            )
            with self.assertRaises(GitHubReleaseClientError):
                client.list_published_releases()

    def test_truncated_metadata_and_socket_read_timeout_fail_closed(self):
        endpoint = (
            "https://api.github.com/repos/EricleungDK/"
            "Danish-Immigration-Assistant/releases"
        )
        for response, message in (
            (
                FakeResponse(
                    b"[]",
                    url=endpoint,
                    headers={"Content-Length": "100"},
                ),
                "byte count",
            ),
            (
                ReadFailureResponse(b"[]", url=endpoint),
                "request failed",
            ),
        ):
            transport = RecordingTransport([response])
            client = GitHubReleaseClient(
                repository=REPOSITORY,
                application_version="0.1.0",
                transport=transport,
            )
            with self.assertRaisesRegex(GitHubReleaseClientError, message):
                client.list_published_releases()

    def test_digest_or_byte_count_mismatch_removes_partial_artifact(self):
        advertised = b"advertised archive"
        received = b"tampered archive"
        metadata = encoded_payload(artifact=advertised)
        transport = RecordingTransport(
            [
                FakeResponse(
                    metadata,
                    url=(
                        "https://api.github.com/repos/EricleungDK/"
                        "Danish-Immigration-Assistant/releases"
                    ),
                ),
                FakeResponse(
                    received,
                    url=(
                        "https://release-assets.githubusercontent.com/"
                        "github-production-release-asset/fixture"
                    ),
                ),
            ]
        )
        client = GitHubReleaseClient(
            repository=REPOSITORY,
            application_version="0.1.0",
            transport=transport,
        )
        asset = client.list_published_releases()[0].assets[0]
        approval = ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=RELEASE_TAG,
            artifact_id=asset.asset_id,
            artifact_name=asset.name,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(
                GitHubReleaseClientError,
                "byte count|digest",
            ):
                client.download_artifact(asset, tmpdir, approval=approval)
            self.assertEqual(list(Path(tmpdir).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
