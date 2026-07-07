import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    KnowledgeReleaseError,
    active_corpus_summary,
    install_knowledge_release,
    install_minimal_knowledge_release,
)
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from danish_rag.retrieval import HybridRetriever
from danish_rag.source_maintenance import build_publishable_knowledge_release


class FixtureAnswerGenerator:
    def generate(
        self,
        *,
        question: str,
        normalized_question: str,
        evidence: list[dict[str, Any]],
        configuration: ProviderConfiguration,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        citation_id = evidence[0]["citation_id"]
        release_id = evidence[0]["knowledge_release_id"]
        return {
            "summary": f"Supported by {release_id}",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": (
                        "Permanent opholdstilladelse can require documented Danish "
                        "language evidence."
                    ),
                    "citation_ids": [citation_id],
                }
            ],
        }


class Issue19AtomicKnowledgeInstallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.data_dir = self.root / "data"
        self.release_catalog = self.root / "release-catalog"
        self.config_path = self.root / "config" / "provider-config.json"
        install_minimal_knowledge_release(self.data_dir)
        save_provider_configuration(
            self.config_path,
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model="atomic-install-fixture-model",
                provider_version="atomic-install-fixture-provider",
                model_identity={"id": "atomic-install-fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-07T10:00:00+00:00",
            ),
        )

    def make_newer_release(
        self,
        *,
        release_id: str = "kr-2026-07-07.1",
        minimum_application_version: str = "0.1.0",
    ) -> Path:
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
            minimum_application_version=minimum_application_version,
        )
        return self.release_catalog / release_id

    def assert_previous_release_still_queryable(self) -> None:
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-06.1",
        )
        results = HybridRetriever.from_data_dir(self.data_dir).retrieve(
            "What Danish test do I need for permanent residence?"
        )
        self.assertTrue(results)
        self.assertEqual(results[0]["knowledge_release_id"], "kr-2026-07-06.1")

    def test_successful_install_reports_progress_and_activates_after_staged_validation(self):
        release_dir = self.make_newer_release()
        streamed_progress: list[dict[str, Any]] = []

        result = install_knowledge_release(
            self.data_dir,
            release_dir=release_dir,
            progress_callback=streamed_progress.append,
        )

        self.assertEqual(result["manifest"]["knowledge_release_id"], "kr-2026-07-07.1")
        self.assertEqual(
            active_corpus_summary(self.data_dir)["knowledge_release_id"],
            "kr-2026-07-07.1",
        )
        self.assertEqual(result["progress"], streamed_progress)
        phases = [event["phase"] for event in result["progress"]]
        for expected in {
            "verification",
            "extraction",
            "indexing",
            "embedding",
            "compatibility",
            "activation",
            "complete",
        }:
            self.assertIn(expected, phases)
        self.assertEqual(result["progress"][-1]["percent"], 100)
        self.assertTrue((self.data_dir / "corpus" / "kr-2026-07-07.1").exists())
        self.assertTrue((self.data_dir / "index" / "kr-2026-07-07.1").exists())
        self.assertTrue(HybridRetriever.from_data_dir(self.data_dir).retrieve("permanent residence"))

        repeated = install_knowledge_release(self.data_dir, release_dir=release_dir)
        self.assertEqual(repeated["progress"][-1]["phase"], "already_active")
        self.assertEqual(repeated["progress"][-1]["percent"], 100)

    def test_simulated_install_faults_leave_prior_release_active_and_queryable(self):
        phases = [
            "verification",
            "extraction",
            "indexing",
            "embedding",
            "compatibility",
            "activation",
        ]
        for phase in phases:
            with self.subTest(phase=phase):
                shutil.rmtree(self.data_dir)
                install_minimal_knowledge_release(self.data_dir)
                release_dir = self.make_newer_release(
                    release_id=f"kr-2026-07-07.{phases.index(phase) + 1}"
                )

                def fault_injector(current_phase: str) -> None:
                    if current_phase == phase:
                        raise RuntimeError(f"simulated {phase} failure")

                with self.assertRaisesRegex(RuntimeError, phase):
                    install_knowledge_release(
                        self.data_dir,
                        release_dir=release_dir,
                        fault_injector=fault_injector,
                    )

                self.assert_previous_release_still_queryable()

    def test_late_activation_fault_restores_promoted_directories_before_pointer_update(self):
        release_dir = self.make_newer_release()
        activation_calls = 0

        def fault_injector(current_phase: str) -> None:
            nonlocal activation_calls
            if current_phase != "activation":
                return
            activation_calls += 1
            if activation_calls == 4:
                raise RuntimeError("simulated late activation failure")

        with self.assertRaisesRegex(RuntimeError, "late activation"):
            install_knowledge_release(
                self.data_dir,
                release_dir=release_dir,
                fault_injector=fault_injector,
            )

        self.assert_previous_release_still_queryable()
        self.assertFalse((self.data_dir / "corpus" / "kr-2026-07-07.1").exists())
        self.assertFalse((self.data_dir / "index" / "kr-2026-07-07.1").exists())

    def test_incompatible_or_invalid_release_is_rejected_before_active_state_changes(self):
        release_dir = self.make_newer_release()
        manifest_path = release_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["minimum_application_version"] = "99.0.0"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(KnowledgeReleaseError, "requires application"):
            install_knowledge_release(self.data_dir, release_dir=release_dir)

        self.assert_previous_release_still_queryable()
        self.assertFalse((self.data_dir / "corpus" / "kr-2026-07-07.1").exists())
        self.assertFalse((self.data_dir / "index" / "kr-2026-07-07.1").exists())

    async def test_activation_updates_future_provenance_without_rewriting_history(self):
        release_dir = self.make_newer_release()
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=FixtureAnswerGenerator(),
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)

        first = await client.post(
            "/ask",
            data={"question": "What Danish test do I need for permanent residence?"},
            headers={"Origin": "http://testserver"},
        )
        self.assertEqual(first.status_code, 200)
        first_id = self.conversation_id_from(
            first.text,
            "What Danish test do I need for permanent residence?",
        )

        install_knowledge_release(self.data_dir, release_dir=release_dir)

        second = await client.post(
            "/ask",
            data={"question": "Can Prøve i Dansk 2 support permanent residence?"},
            headers={"Origin": "http://testserver"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertIn("<dd>kr-2026-07-07.1</dd>", second.text)
        second_id = self.conversation_id_from(
            second.text,
            "Can Prøve i Dansk 2 support permanent residence?",
        )

        first_export = (await client.get(f"/conversations/{first_id}/export.json")).json()
        second_export = (await client.get(f"/conversations/{second_id}/export.json")).json()
        self.assertEqual(
            first_export["conversation"]["turns"][0]["corpus_version"],
            "kr-2026-07-06.1",
        )
        self.assertEqual(
            second_export["conversation"]["turns"][0]["corpus_version"],
            "kr-2026-07-07.1",
        )

    def conversation_id_from(self, html: str, title: str) -> str:
        match = re.search(rf'href="/conversations/([^"]+)">{re.escape(title)}', html)
        self.assertIsNotNone(match, html)
        return match.group(1)


if __name__ == "__main__":
    unittest.main()
