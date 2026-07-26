from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from danish_rag.source_review import (
    SourceReviewError,
    main,
    write_completed_source_review_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = (
    "<!doctype html><html lang=\"da\"><head>"
    "<style>html { visibility: hidden; opacity: 0; }</style>"
    "</head><body><main>"
    "<h1>Ansøg om permanent opholdstilladelse</h1>"
    "<p>Sidst opdateret 07-07-2026 - Publiceret af: Udlændingestyrelsen</p>"
    "<p>Du skal have bestået Prøve i Dansk 2.</p>"
    "</main></body></html>"
).encode("utf-8")
STALE_URL_RESPONSE = b"old permanent-residence link not found"


class _OfficialSourceHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/registry":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(STALE_URL_RESPONSE)))
            self.end_headers()
            self.wfile.write(STALE_URL_RESPONSE)
            return
        if self.path == "/current":
            self.send_response(302)
            self.send_header("Location", "/nested")
            self.end_headers()
            return
        if self.path == "/nested":
            self.send_response(301)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        if self.path == "/final":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(SNAPSHOT)))
            self.end_headers()
            self.wfile.write(SNAPSHOT)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class SourceReviewCliTests(unittest.TestCase):
    def test_cli_archives_snapshot_with_registry_url_and_redirect_provenance(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OfficialSourceHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            config = {
                "schema_version": "official-source-review-set-v1",
                "sources": [
                    {
                        "source_id": (
                            "nyidanmark-permanent-residence-language-requirements"
                        ),
                        "registry_url": f"{base_url}/registry",
                        "requested_url": f"{base_url}/current",
                        "publisher": "The Danish Immigration Service",
                        "topic": "permanent-residence language requirements",
                        "language": "da",
                        "reason_for_inclusion": (
                            "Official language-requirement source."
                        ),
                        "in_scope_evidence": (
                            "Issue #1 includes permanent-residence requirements."
                        ),
                        "initial_owner_ids": ["owner"],
                        "review_flags": [
                            {
                                "code": "permanent-residence-url-change",
                                "summary": (
                                    "The registry URL is stale; the current official "
                                    "page uses a nested percent-encoded URL."
                                ),
                                "requires_human_materiality_decision": True,
                            }
                        ],
                    }
                ],
            }

            with tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                config_path = workspace / "review-set.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                output_dir = workspace / "review-bundle"

                exit_code = main(
                    [
                        "--repo-root",
                        str(ROOT),
                        "--config",
                        str(config_path),
                        "--output",
                        str(output_dir),
                        "--retrieved-at-utc",
                        "2026-07-26T10:00:00Z",
                    ]
                )

                self.assertEqual(exit_code, 0)
                bundle = json.loads(
                    (output_dir / "review-bundle.json").read_text(encoding="utf-8")
                )
                source = bundle["sources"][0]
                snapshot_path = output_dir / source["snapshot"]["path"]
                registry_observation_path = (
                    output_dir / source["registry_url_observation"]["response"]["path"]
                )
                self.assertEqual(snapshot_path.read_bytes(), SNAPSHOT)
                self.assertEqual(
                    registry_observation_path.read_bytes(),
                    STALE_URL_RESPONSE,
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(
            source["registry_url"],
            f"{base_url}/registry",
        )
        self.assertEqual(source["retrieval"]["requested_url"], f"{base_url}/current")
        self.assertEqual(
            source["retrieval"]["redirect_chain"],
            [
                {
                    "from_url": f"{base_url}/current",
                    "http_status": 302,
                    "location": f"{base_url}/nested",
                },
                {
                    "from_url": f"{base_url}/nested",
                    "http_status": 301,
                    "location": f"{base_url}/final",
                },
            ],
        )
        self.assertEqual(source["retrieval"]["final_url"], f"{base_url}/final")
        self.assertEqual(source["retrieval"]["http_status"], 200)
        self.assertEqual(
            source["retrieval"]["retrieved_at_utc"],
            "2026-07-26T10:00:00Z",
        )
        self.assertEqual(
            source["snapshot"],
            {
                "bytes": 295,
                "path": (
                    "snapshots/"
                    "nyidanmark-permanent-residence-language-requirements.html"
                ),
                "sha256": "00d837ddbc93c6d6032cb61c3c1e3761c6641c45247a40273ca3c49d2273ce5f",
            },
        )
        self.assertEqual(
            source["url_resolution"],
            {
                "registry_url": f"{base_url}/registry",
                "candidate_current_url": f"{base_url}/current",
                "observed_final_url": f"{base_url}/final",
                "status": "requires-human-resolution",
                "human_decision": None,
                "notes": None,
            },
        )
        self.assertEqual(
            source["registry_url_observation"],
            {
                "final_url": f"{base_url}/registry",
                "http_status": 404,
                "redirect_chain": [],
                "requested_url": f"{base_url}/registry",
                "response": {
                    "bytes": 38,
                    "path": (
                        "observations/"
                        "nyidanmark-permanent-residence-language-requirements-"
                        "registry-url.html"
                    ),
                    "sha256": (
                        "fb05685549be3bf596a17a978b4c34fa916431102dbf0bfe10855c982cd2bc41"
                    ),
                },
                "retrieved_at_utc": "2026-07-26T10:00:00Z",
            },
        )
        self.assertEqual(
            source["review_flags"],
            [
                {
                    "classification": "unverified-machine-observation",
                    "code": "permanent-residence-url-change",
                    "counts_as_human_review": False,
                    "requires_human_materiality_decision": True,
                    "summary": (
                        "The registry URL is stale; the current official page "
                        "uses a nested percent-encoded URL."
                    ),
                }
            ],
        )

    def test_cli_prepares_normalized_extraction_and_blank_human_review(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OfficialSourceHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            config = {
                "schema_version": "official-source-review-set-v1",
                "sources": [
                    {
                        "source_id": (
                            "nyidanmark-permanent-residence-language-requirements"
                        ),
                        "registry_url": f"{base_url}/registry",
                        "requested_url": f"{base_url}/final",
                        "publisher": "The Danish Immigration Service",
                        "topic": "permanent-residence language requirements",
                        "language": "da",
                        "reason_for_inclusion": (
                            "Official language-requirement source."
                        ),
                        "in_scope_evidence": (
                            "Issue #1 includes permanent-residence requirements."
                        ),
                        "initial_owner_ids": ["owner"],
                        "review_flags": [],
                    }
                ],
            }

            with tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                config_path = workspace / "review-set.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                output_dir = workspace / "review-bundle"

                self.assertEqual(
                    main(
                        [
                            "--repo-root",
                            str(ROOT),
                            "--config",
                            str(config_path),
                            "--output",
                            str(output_dir),
                            "--retrieved-at-utc",
                            "2026-07-26T10:00:00Z",
                        ]
                    ),
                    0,
                )

                bundle = json.loads(
                    (output_dir / "review-bundle.json").read_text(encoding="utf-8")
                )
                source = bundle["sources"][0]
                normalized_path = output_dir / source["normalized_extraction"]["path"]
                review_page_path = output_dir / source["offline_review_page"]["path"]
                self.assertEqual(
                    normalized_path.read_text(encoding="utf-8"),
                    (
                        "Ansøg om permanent opholdstilladelse\n"
                        "Sidst opdateret 07-07-2026 - Publiceret af: "
                        "Udlændingestyrelsen\n"
                        "Du skal have bestået Prøve i Dansk 2.\n"
                    ),
                )
                review_page = review_page_path.read_text(encoding="utf-8")
                self.assertIn(
                    "Offline review copy derived from the archived official snapshot",
                    review_page,
                )
                self.assertIn(
                    "html { visibility: visible !important; opacity: 1 !important; }",
                    review_page,
                )
                self.assertIn(
                    "default-src &#x27;none&#x27;; style-src &#x27;unsafe-inline&#x27;",
                    review_page,
                )
                self.assertIn("Du skal have bestået Prøve i Dansk 2.", review_page)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(
            source["normalized_extraction"],
            {
                "bytes": 143,
                "extraction_schema_version": "visible-main-text-v1",
                "path": (
                    "normalized/"
                    "nyidanmark-permanent-residence-language-requirements.txt"
                ),
                "sha256": "5aa3daa765866cfbeb2c4e88f3c432daf788a3a83b2d58e4b5219ea8dafed8fc",
            },
        )
        self.assertEqual(
            source["observed_metadata"],
            {
                "publisher": "The Danish Immigration Service",
                "visible_publisher": "Udlændingestyrelsen",
                "visible_source_date": "07-07-2026",
            },
        )
        self.assertEqual(
            source["offline_review_page"]["evidence_status"],
            "derived-review-aid-not-source-snapshot",
        )
        self.assertEqual(
            source["offline_review_page"]["derived_from_snapshot_sha256"],
            source["snapshot"]["sha256"],
        )
        self.assertEqual(
            source["candidate_admission"],
            {
                "reason_for_inclusion": "Official language-requirement source.",
                "in_scope_evidence": (
                    "Issue #1 includes permanent-residence requirements."
                ),
                "initial_owner_ids": ["owner"],
                "human_status": "pending",
            },
        )
        self.assertEqual(
            source["curator_admission"],
            {
                "decision": None,
                "curator_ids": [],
                "admitted_at_utc": None,
                "scope_rationale": None,
                "topic": "permanent-residence language requirements",
                "language": "da",
                "monitoring_owner_ids": [],
            },
        )
        self.assertEqual(
            source["human_review"],
            {
                "decision": None,
                "reviewer_ids": [],
                "second_reviewer_ids": [],
                "reviewed_at_utc": None,
                "materiality": None,
                "notes": None,
                "interpretation_risks": [],
                "single_maintainer_fallback": None,
                "post_publication_review_due_utc": None,
            },
        )
        self.assertFalse(source["production_release_eligible"])
        self.assertEqual(source["eligibility_status"], "blocked-pending-human-review")

    def test_completed_bundle_binds_human_decisions_and_reviewed_artifacts(self) -> None:
        snapshot = b"<html><body><main>Official content</main></body></html>"
        normalized = b"Official content\n"
        snapshot_sha256 = hashlib.sha256(snapshot).hexdigest()
        normalized_sha256 = hashlib.sha256(normalized).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            machine_bundle_dir = workspace / "machine-bundle"
            (machine_bundle_dir / "snapshots").mkdir(parents=True)
            (machine_bundle_dir / "normalized").mkdir()
            (machine_bundle_dir / "snapshots/source.html").write_bytes(snapshot)
            (machine_bundle_dir / "normalized/source.txt").write_bytes(normalized)
            machine_bundle = {
                "schema_version": "official-source-review-bundle-v1",
                "source_count": 1,
                "sources": [
                    {
                        "source_id": "source",
                        "registry_url": "https://example.com/source",
                        "retrieval": {
                            "requested_url": "https://example.com/source",
                            "redirect_chain": [],
                            "final_url": "https://example.com/source",
                            "retrieved_at_utc": "2026-07-26T21:00:00Z",
                            "http_status": 200,
                        },
                        "snapshot": {
                            "path": "snapshots/source.html",
                            "sha256": snapshot_sha256,
                        },
                        "normalized_extraction": {
                            "path": "normalized/source.txt",
                            "sha256": normalized_sha256,
                        },
                    }
                ],
            }
            machine_bundle_path = machine_bundle_dir / "review-bundle.json"
            machine_bundle_path.write_text(
                json.dumps(machine_bundle),
                encoding="utf-8",
            )
            decisions = {
                "schema_version": "official-source-human-decisions-v1",
                "recorded_at_utc": "2026-07-26T21:20:10Z",
                "review_bundle": {
                    "sha256": hashlib.sha256(
                        machine_bundle_path.read_bytes()
                    ).hexdigest(),
                },
                "human_identity": {
                    "curator_ids": ["curator"],
                    "monitoring_owner_ids": ["owner"],
                    "reviewer_ids": ["reviewer"],
                },
                "single_maintainer_fallback": {
                    "selected": True,
                    "reason": "One-person MVP.",
                    "post_publication_second_review": {
                        "required": True,
                        "status": "pending-publication",
                        "schedule": "Set a due date when the source is published.",
                    },
                },
                "source_decisions": [
                    {
                        "source_id": "source",
                        "curator_admission": {
                            "decision": "approve",
                            "curator_ids": ["curator"],
                            "admitted_at_utc": "2026-07-26T21:20:10Z",
                            "scope_rationale": "Official source is in scope.",
                            "confirmed_topic": "topic",
                            "confirmed_language": "en",
                            "monitoring_owner_ids": ["owner"],
                        },
                        "url_resolution": {
                            "decision": "approve-current",
                            "approved_url": "https://example.com/source",
                        },
                        "human_review": {
                            "decision": "approve",
                            "reviewed_at_utc": "2026-07-26T21:20:10Z",
                            "reviewer_ids": ["reviewer"],
                            "official_source_snapshot_sha256": snapshot_sha256,
                            "normalized_extraction_sha256": normalized_sha256,
                            "materiality": "material",
                            "notes": "Reviewed and approved.",
                            "interpretation_risks": [],
                        },
                        "source_review_gate_status": (
                            "approved-under-mvp-single-maintainer-fallback"
                        ),
                        "eligible_for_follow_on_rebuild": True,
                    }
                ],
                "packet_g": {
                    "knowledge_release_id": "kr-2026-07-06.1",
                    "status": "unchanged",
                    "qualification_scope": (
                        "Packet G remains canonical only for kr-2026-07-06.1 "
                        "and does not qualify a future replacement corpus."
                    ),
                },
            }
            decisions_path = workspace / "human-decisions.json"
            decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
            completed_dir = workspace / "completed"

            completed = write_completed_source_review_bundle(
                machine_bundle_dir=machine_bundle_dir,
                decisions_path=decisions_path,
                output_dir=completed_dir,
            )

            self.assertEqual(
                json.loads(
                    (completed_dir / "completed-review.json").read_text(
                        encoding="utf-8"
                    )
                ),
                completed,
            )
            self.assertEqual(
                (completed_dir / "snapshots/source.html").read_bytes(),
                snapshot,
            )
            self.assertEqual(
                (completed_dir / "normalized/source.txt").read_bytes(),
                normalized,
            )

            decisions["source_decisions"][0]["url_resolution"] = {
                "decision": "reject",
            }
            rejected_decisions_path = workspace / "rejected-decisions.json"
            rejected_decisions_path.write_text(
                json.dumps(decisions),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SourceReviewError,
                "claims eligibility without all required approvals",
            ):
                write_completed_source_review_bundle(
                    machine_bundle_dir=machine_bundle_dir,
                    decisions_path=rejected_decisions_path,
                    output_dir=workspace / "rejected-completed",
                )

            decisions["source_decisions"][0]["url_resolution"] = {
                "decision": "approve-current",
                "approved_url": "https://example.com/source",
            }
            decisions["source_decisions"][0]["human_review"]["reviewer_ids"] = [
                "mvp-fixture-reviewer"
            ]
            fixture_decisions_path = workspace / "fixture-decisions.json"
            fixture_decisions_path.write_text(
                json.dumps(decisions),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SourceReviewError,
                "cannot use fixture reviewer identities",
            ):
                write_completed_source_review_bundle(
                    machine_bundle_dir=machine_bundle_dir,
                    decisions_path=fixture_decisions_path,
                    output_dir=workspace / "fixture-completed",
                )

            decisions["source_decisions"][0]["human_review"]["reviewer_ids"] = [
                "reviewer"
            ]
            decisions["source_decisions"][0]["human_review"][
                "reviewed_at_utc"
            ] = "2026-07-06T12:00:00Z"
            fixture_timestamp_path = workspace / "fixture-timestamp.json"
            fixture_timestamp_path.write_text(
                json.dumps(decisions),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SourceReviewError,
                "fixture timestamps cannot be used",
            ):
                write_completed_source_review_bundle(
                    machine_bundle_dir=machine_bundle_dir,
                    decisions_path=fixture_timestamp_path,
                    output_dir=workspace / "fixture-timestamp-completed",
                )

            decisions["source_decisions"][0]["human_review"][
                "reviewed_at_utc"
            ] = "2026-07-26T21:20:10Z"
            decisions["source_decisions"][0]["url_resolution"][
                "approved_url"
            ] = "https://example.com/unobserved"
            unobserved_url_path = workspace / "unobserved-url.json"
            unobserved_url_path.write_text(
                json.dumps(decisions),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SourceReviewError,
                "approved URL does not match retrieved evidence",
            ):
                write_completed_source_review_bundle(
                    machine_bundle_dir=machine_bundle_dir,
                    decisions_path=unobserved_url_path,
                    output_dir=workspace / "unobserved-url-completed",
                )

            decisions["source_decisions"][0]["url_resolution"][
                "approved_url"
            ] = "https://example.com/source"
            decisions["packet_g"]["qualification_scope"] = (
                "Packet G qualifies every future corpus."
            )
            packet_scope_path = workspace / "packet-scope.json"
            packet_scope_path.write_text(
                json.dumps(decisions),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SourceReviewError,
                "must preserve packet G",
            ):
                write_completed_source_review_bundle(
                    machine_bundle_dir=machine_bundle_dir,
                    decisions_path=packet_scope_path,
                    output_dir=workspace / "packet-scope-completed",
                )

        self.assertEqual(
            completed["qualification_status"],
            "source-review-complete-ready-for-follow-on-rebuild",
        )
        self.assertEqual(completed["approved_source_count"], 1)
        self.assertEqual(completed["blocked_source_count"], 0)
        self.assertTrue(completed["sources"][0]["eligible_for_follow_on_rebuild"])
        self.assertEqual(
            completed["sources"][0]["official_source_snapshot_sha256"],
            snapshot_sha256,
        )
        self.assertEqual(completed["packet_g"]["status"], "unchanged")


if __name__ == "__main__":
    unittest.main()
