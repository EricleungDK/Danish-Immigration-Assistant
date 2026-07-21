import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from danish_rag.answer_pipeline import (
    AnswerPipelineError,
    LocalProviderAnswerGenerator,
    answer_schema,
)
from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    active_corpus_summary,
    install_minimal_knowledge_release,
)
from danish_rag.local_app import create_app
from danish_rag.privacy_boundary import (
    PrivacyBoundaryError,
    build_release_network_request,
    validate_runtime_policy_privacy_boundary,
)
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from danish_rag.retrieval import HybridRetriever
from danish_rag.runtime_policy import load_runtime_policy
from danish_rag.source_maintenance import build_publishable_knowledge_release
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture
from tests.release_trust_fixture import create_test_release_trust_fixture


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "runtime-policy.json"


class NetworkObserver:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self._patcher = patch("urllib.request.urlopen", side_effect=self._urlopen)

    def __enter__(self) -> "NetworkObserver":
        self._patcher.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._patcher.__exit__(exc_type, exc, traceback)

    def _urlopen(self, request, *args, **kwargs):
        url = getattr(request, "full_url", str(request))
        self.requests.append(url)
        raise AssertionError(f"Unexpected network request during local workflow: {url}")


class FixtureAnswerGenerator:
    def __init__(self) -> None:
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
        self.calls.append(
            {
                "question": question,
                "normalized_question": normalized_question,
                "evidence_ids": [item["citation_id"] for item in evidence],
                "schema": schema,
            }
        )
        citation_id = evidence[0]["citation_id"]
        return {
            "summary": "The official source supports a Danish language requirement answer.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": (
                        "Permanent opholdstilladelse can require documentation for "
                        "passed Prøve i Dansk 2 or an equivalent Danish test."
                    ),
                    "citation_ids": [citation_id],
                },
                {
                    "kind": "interpretation",
                    "text": "Use the cited official source to verify the exact requirement.",
                    "citation_ids": [citation_id],
                },
            ],
        }


class Issue21PrivacyBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "config" / "provider-config.json"
        self.data_dir = self.root / "data"
        self.release_catalog = self.root / "release-catalog"
        self.embedding_provider = DeterministicEmbeddingProviderFixture()
        self.release_trust = create_test_release_trust_fixture(
            self.root / "test-only-release-trust"
        )
        save_provider_configuration(
            self.config_path,
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model="privacy-fixture-model",
                provider_version="privacy-fixture-provider",
                model_identity={"id": "privacy-fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-07T12:00:00+00:00",
            ),
        )

    def make_newer_release(self, *, release_id: str = "kr-2026-07-07.1") -> None:
        current_manifest = json.loads(
            (BUNDLED_MINIMAL_RELEASE / "manifest.json").read_text(encoding="utf-8")
        )
        current_documents = json.loads(
            (BUNDLED_MINIMAL_RELEASE / "corpus" / "documents.json").read_text(
                encoding="utf-8"
            )
        )
        sources = []
        for source in current_manifest["sources"]:
            updated = dict(source)
            if updated["source_id"] == "nyidanmark-permanent-residence-language-requirements":
                updated["last_checked_at_utc"] = "2026-07-07T12:00:00Z"
                updated["reviewed_at_utc"] = "2026-07-07T12:30:00Z"
                updated["source_content_sha256"] = (
                    "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
                )
                updated["normalized_document_sha256"] = (
                    "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100"
                )
            sources.append(updated)

        documents = []
        for document in current_documents:
            updated = dict(document)
            if updated["source_id"] == "nyidanmark-permanent-residence-language-requirements":
                updated["checked_at_utc"] = "2026-07-07T12:00:00Z"
                updated["content"] = updated["content"] + "\nReviewed July update."
            documents.append(updated)

        build_publishable_knowledge_release(
            release_dir=self.release_catalog / release_id,
            release_id=release_id,
            source_registry_version="sr-2026-07-07.1",
            sources=sources,
            documents=documents,
            created_at_utc="2026-07-07T13:00:00Z",
            minimum_application_version="0.1.0",
            signing_private_key_path=self.release_trust.signing_private_key_path,
            trust_root_path=self.release_trust.trust_root_path,
        )

    def conversation_id_from(self, html: str, title: str) -> str:
        match = re.search(rf'href="/conversations/([^"]+)">{re.escape(title)}', html)
        self.assertIsNotNone(match, html)
        return match.group(1)

    async def wait_for_install_terminal_status(
        self, client: httpx.AsyncClient
    ) -> httpx.Response:
        for _ in range(150):
            status = await client.get("/knowledge-updates/install-status")
            if any(
                message in status.text
                for message in {
                    "Knowledge update installed",
                    "Knowledge update rolled back",
                    "Knowledge update needs attention",
                }
            ):
                return status
            await asyncio.sleep(0.02)
        self.fail("Knowledge installation did not reach a terminal state")

    async def test_observed_user_workflows_make_no_network_requests(self):
        self.make_newer_release()
        generator = FixtureAnswerGenerator()
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=generator,
            release_catalog_dir=self.release_catalog,
            embedding_provider=self.embedding_provider,
            trust_root_path=self.release_trust.trust_root_path,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)

        with NetworkObserver() as observer:
            installation = install_minimal_knowledge_release(
                self.data_dir,
                embedding_provider=self.embedding_provider,
            )
            retrieval_results = HybridRetriever.from_data_dir(
                self.data_dir,
                embedding_provider=self.embedding_provider,
            ).retrieve("What Danish test do I need for permanent residence?")
            home = await client.get("/")
            answer = await client.post(
                "/ask",
                data={"question": "What Danish test do I need for permanent residence?"},
                headers={"Origin": "http://testserver"},
            )
            conversation_id = self.conversation_id_from(
                answer.text,
                "What Danish test do I need for permanent residence?",
            )
            reopened = await client.get(f"/conversations/{conversation_id}")
            one_export = await client.get(f"/conversations/{conversation_id}/export.json")
            all_export = await client.get("/conversations/export.json")
            update_check = await client.post(
                "/knowledge-updates/check",
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            update_review = await client.get("/")
            dismiss = await client.post(
                "/knowledge-updates/dismiss",
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            await client.post(
                "/knowledge-updates/check",
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            install = await client.post(
                "/knowledge-updates/install",
                data={"release_id": "kr-2026-07-07.1"},
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            install_status = await self.wait_for_install_terminal_status(client)
            delete = await client.post(
                f"/conversations/{conversation_id}/delete",
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )

        self.assertEqual(observer.requests, [])
        self.assertEqual(installation["manifest"]["knowledge_release_id"], "kr-2026-07-06.1")
        self.assertTrue(retrieval_results)
        self.assertEqual(home.status_code, 200)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(
            generator.calls[0]["schema"],
            answer_schema(
                [
                    "di-rag-doc-permanent-residence-language",
                    "di-rag-doc-equivalent-tests-language-test-2",
                    "di-rag-doc-equivalent-tests-language-test-3",
                ]
            ),
        )
        self.assertIn("Inspect evidence", reopened.text)
        self.assertEqual(one_export.status_code, 200)
        self.assertEqual(all_export.status_code, 200)
        self.assertEqual(update_check.status_code, 303)
        self.assertIn("Knowledge update available", update_review.text)
        self.assertEqual(dismiss.status_code, 303)
        self.assertEqual(install.status_code, 303)
        self.assertIn("Knowledge update installed", install_status.text)
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-07.1",
        )
        self.assertEqual(delete.status_code, 303)

    def test_local_generation_refuses_non_loopback_provider_before_network(self):
        generator = LocalProviderAnswerGenerator()
        configuration = ProviderConfiguration(
            provider_id="openai_compatible",
            endpoint="https://api.example.test",
            model="remote-model",
        )

        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(AnswerPipelineError, "loopback endpoint"):
                generator.generate(
                    question="What Danish test do I need?",
                    normalized_question="What Danish test do I need?",
                    evidence=[],
                    configuration=configuration,
                    schema=answer_schema(),
                )

        urlopen.assert_not_called()

    def test_policy_allows_only_content_free_release_update_requests(self):
        policy = load_runtime_policy(POLICY_PATH)

        self.assertEqual(validate_runtime_policy_privacy_boundary(policy), [])
        self.assertFalse(policy["privacy"]["account_required_for_mvp"])
        self.assertFalse(policy["privacy"]["cloud_history_required_for_mvp"])
        self.assertFalse(
            policy["privacy"]["remote_inference_credentials_required_for_mvp"]
        )
        self.assertFalse(policy["privacy"]["provider_credentials_required_for_mvp"])
        self.assertFalse(
            policy["privacy"][
                "send_questions_answers_evidence_or_conversation_records_to_updates"
            ]
        )

        request = build_release_network_request(
            policy,
            operation="knowledge_release_discovery",
            base_url="https://updates.example.test",
            application_version="0.1.0",
            active_knowledge_release_id="kr-2026-07-06.1",
        )
        serialized_request = "\n".join(
            [
                request.full_url,
                json.dumps(dict(request.header_items()), sort_keys=True),
                str(request.data),
            ]
        ).casefold()

        for marker in {
            "what danish test",
            "answer",
            "evidence",
            "conversation_id",
            "conversation_record",
            "turn_index",
            "citation_id",
            "normalized_question",
            "prompt",
            "messages",
        }:
            self.assertNotIn(marker, serialized_request)

        with self.assertRaises(PrivacyBoundaryError):
            build_release_network_request(
                policy,
                operation="remote_inference",
                base_url="https://updates.example.test",
                application_version="0.1.0",
            )
        with self.assertRaises(PrivacyBoundaryError):
            build_release_network_request(
                policy,
                operation="knowledge_release_discovery",
                base_url="https://updates.example.test",
                application_version="0.1.0",
                extra_fields={"question": "What Danish test do I need?"},
            )


if __name__ == "__main__":
    unittest.main()
