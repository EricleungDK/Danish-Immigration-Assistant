"""Snapshot mode (#67): the running app evaluates freshness at the release snapshot time."""

import json
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import danish_rag.source_freshness as source_freshness
from danish_rag.source_freshness import assess_source_freshness
import httpx

from danish_rag.conversation_store import ConversationStore
from danish_rag.knowledge_release import (
    BUNDLED_MINIMAL_RELEASE,
    DEFAULT_RELEASE_CATALOG_DIR,
    KnowledgeReleaseError,
    ensure_minimal_knowledge_release,
    install_knowledge_release,
    install_minimal_knowledge_release,
    verify_knowledge_release,
)
from danish_rag.local_app import create_app
from danish_rag.provider_setup import ProviderConfiguration, save_provider_configuration
from danish_rag.retrieval import HybridRetriever
from tests import fixture_clock
from tests.embedding_provider_fixture import DeterministicEmbeddingProviderFixture

SEPTEMBER_RELEASE = DEFAULT_RELEASE_CATALOG_DIR / "kr-2026-09-05.1"

# After every bundled release's review due dates (2026-10-06 and 2026-10-26).
LATE_WALL_CLOCK = datetime(2027, 1, 1, tzinfo=timezone.utc)
JULY_SNAPSHOT = datetime(2026, 7, 6, tzinfo=timezone.utc)
SEPTEMBER_SNAPSHOT = datetime(2026, 9, 5, 15, 12, tzinfo=timezone.utc)


def late_wall_clock():
    """Force the (test-pinned) wall clock past the due dates; restored on exit."""

    return patch.object(fixture_clock, "FIXTURE_EVALUATION_TIME_UTC", LATE_WALL_CLOCK)


def evidence_due(due: str) -> dict[str, object]:
    return {
        "review_state": "approved-current",
        "source_health": "healthy",
        "approval_state": "approved",
        "fresh_tomato_inputs": {"source_health": "current", "next_review_due_utc": due},
    }


def now_utc() -> datetime:
    return source_freshness.datetime.now(timezone.utc)


class SnapshotClockTests(unittest.TestCase):
    def test_wall_clock_after_the_due_date_blocks_without_a_snapshot_scope(self):
        with late_wall_clock():
            self.assertFalse(
                assess_source_freshness(evidence_due("2026-10-06T12:00:00Z")).answer_eligible
            )

    def test_scope_evaluates_freshness_at_the_snapshot_time(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with late_wall_clock(), snapshot_clock(lambda: JULY_SNAPSHOT):
            self.assertEqual(now_utc(), JULY_SNAPSHOT)
            self.assertTrue(
                assess_source_freshness(evidence_due("2026-10-06T12:00:00Z")).answer_eligible
            )

    def test_snapshot_time_is_not_an_exemption_from_the_due_date(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with snapshot_clock(lambda: datetime(2026, 10, 7, tzinfo=timezone.utc)):
            self.assertFalse(
                assess_source_freshness(evidence_due("2026-10-06T12:00:00Z")).answer_eligible
            )

    def test_scope_restores_the_underlying_clock_on_exit(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with late_wall_clock():
            with snapshot_clock(lambda: JULY_SNAPSHOT):
                pass
            self.assertEqual(now_utc(), LATE_WALL_CLOCK)
        self.assertEqual(now_utc(), fixture_clock.FIXTURE_EVALUATION_TIME_UTC)

    def test_scope_is_isolated_per_thread(self):
        from danish_rag.snapshot_clock import snapshot_clock

        seen = []
        with late_wall_clock(), snapshot_clock(lambda: JULY_SNAPSHOT):
            worker = threading.Thread(target=lambda: seen.append(now_utc()))
            worker.start()
            worker.join()
            self.assertEqual(now_utc(), JULY_SNAPSHOT)
        self.assertEqual(seen, [LATE_WALL_CLOCK])

    def test_scopes_nest_and_the_inner_scope_wins(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with snapshot_clock(lambda: JULY_SNAPSHOT):
            with snapshot_clock(lambda: SEPTEMBER_SNAPSHOT):
                self.assertEqual(now_utc(), SEPTEMBER_SNAPSHOT)
            self.assertEqual(now_utc(), JULY_SNAPSHOT)

    def test_snapshot_is_resolved_lazily_once_per_scope(self):
        from danish_rag.snapshot_clock import snapshot_clock

        calls = []

        def resolve():
            calls.append(1)
            return JULY_SNAPSHOT

        with snapshot_clock(resolve):
            self.assertEqual(calls, [])
            now_utc()
            now_utc()
        self.assertEqual(calls, [1])

    def test_unresolvable_snapshot_falls_back_to_the_underlying_clock(self):
        from danish_rag.snapshot_clock import snapshot_clock

        def broken():
            raise OSError("active release unreadable")

        with late_wall_clock(), snapshot_clock(broken):
            self.assertEqual(now_utc(), LATE_WALL_CLOCK)
        with late_wall_clock(), snapshot_clock(lambda: None):
            self.assertEqual(now_utc(), LATE_WALL_CLOCK)

    def test_clock_works_over_the_real_datetime_and_after_the_pin_is_removed(self):
        from danish_rag.snapshot_clock import snapshot_clock

        fixture_clock.unpin_freshness_clock()
        try:
            with snapshot_clock(lambda: JULY_SNAPSHOT):
                self.assertEqual(now_utc(), JULY_SNAPSHOT)
            self.assertIsNot(source_freshness.datetime, None)
            self.assertAlmostEqual(
                now_utc().timestamp(), datetime.now(timezone.utc).timestamp(), delta=60
            )
        finally:
            fixture_clock.pin_freshness_clock()

    def test_wrapped_datetime_still_recognises_real_datetimes_and_parses(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with snapshot_clock(lambda: JULY_SNAPSHOT):
            self.assertIsInstance(datetime(2026, 1, 1, tzinfo=timezone.utc), source_freshness.datetime)
            self.assertTrue(
                assess_source_freshness(
                    evidence_due("2026-10-06T12:00:00Z"),
                    evaluated_at_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
                ).answer_eligible
            )
            self.assertFalse(
                assess_source_freshness(
                    evidence_due("2026-10-06T12:00:00Z"), evaluated_at_utc="2026-10-07T00:00:00Z"
                ).answer_eligible
            )

    def test_other_freshness_clocks_are_left_to_the_evaluation_pipeline(self):
        # Evidence/qualification code reads these; snapshot mode is app-only.
        import danish_rag.evidence_integrity as evidence_integrity
        import danish_rag.grounded_flexibility_evaluation as grounded

        from danish_rag.snapshot_clock import snapshot_clock

        with late_wall_clock(), snapshot_clock(lambda: JULY_SNAPSHOT):
            self.assertEqual(
                evidence_integrity.utc_now_seconds(), "2027-01-01T00:00:00Z"
            )
            self.assertEqual(grounded._utc_now(), "2027-01-01T00:00:00Z")


class SnapshotTimeResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_dir = Path(self.tempdir.name) / "data"
        self.provider = DeterministicEmbeddingProviderFixture()

    def test_active_snapshot_time_follows_the_active_release(self):
        from danish_rag.snapshot_clock import active_snapshot_time

        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        self.assertEqual(active_snapshot_time(self.data_dir), JULY_SNAPSHOT)
        install_knowledge_release(
            self.data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )
        self.assertEqual(active_snapshot_time(self.data_dir), SEPTEMBER_SNAPSHOT)

    def test_snapshot_time_is_the_manifest_created_at(self):
        from danish_rag.snapshot_clock import active_snapshot_time

        install_knowledge_release(
            self.data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )
        self.assertEqual(active_snapshot_time(self.data_dir), SEPTEMBER_SNAPSHOT)

    def test_no_active_release_gives_no_snapshot(self):
        from danish_rag.snapshot_clock import active_snapshot_time

        self.assertIsNone(active_snapshot_time(self.data_dir))

    def test_offsetless_or_malformed_snapshot_times_are_rejected(self):
        from danish_rag.snapshot_clock import parse_snapshot_time

        self.assertEqual(parse_snapshot_time("2026-07-06T00:00:00Z"), JULY_SNAPSHOT)
        for created in (None, "2026-07-06T00:00:00", "not a date", 5):
            with self.subTest(created=created), self.assertRaises(ValueError):
                parse_snapshot_time(created)

    def test_unreadable_active_release_gives_no_snapshot(self):
        from danish_rag.snapshot_clock import active_snapshot_time

        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        (self.data_dir / "active-release.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(active_snapshot_time(self.data_dir))


QUESTION = "What Danish test do I need for permanent residence?"
RELEASES = {"kr-2026-07-06.1": BUNDLED_MINIMAL_RELEASE, "kr-2026-09-05.1": SEPTEMBER_RELEASE}


class FirstEvidenceAnswerGenerator:
    def generate(self, *, evidence, **_):
        return {
            "summary": "Reviewed official evidence found.",
            "sections": [
                {
                    "kind": "official_fact",
                    "text": evidence[0]["content"],
                    "citation_ids": [evidence[0]["citation_id"]],
                }
            ],
        }


class AppTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.config_path = root / "config" / "provider-config.json"
        self.data_dir = root / "data"
        self.provider = DeterministicEmbeddingProviderFixture()
        save_provider_configuration(
            self.config_path,
            ProviderConfiguration(
                provider_id="openai_compatible",
                endpoint="http://127.0.0.1:1234",
                model="fixture-model",
                provider_version="fixture-provider",
                model_identity={"id": "fixture-model"},
                capabilities=["generation"],
                validated_at_utc="2026-07-06T12:00:00+00:00",
            ),
        )

    def make_client(self, **app_kwargs) -> httpx.AsyncClient:
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=FirstEvidenceAnswerGenerator(),
            embedding_provider=self.provider,
            **app_kwargs,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def ask(self, client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            "/ask", data={"question": QUESTION}, headers={"Origin": "http://testserver"}
        )

    def assert_cited_answer(self, response: httpx.Response, release_id: str) -> None:
        self.assertEqual(response.status_code, 200, response.text[:400])
        self.assertIn("Fresh Tomato Score: High", response.text)
        store = ConversationStore(self.data_dir / "conversations.sqlite3")
        record = store.get_conversation(store.list_conversations()[0]["id"])
        self.assertTrue(record["answer"]["citations"])
        self.assertEqual(
            record["corpus_identity"],
            json.loads((RELEASES[release_id] / "manifest.json").read_text())["corpus_id"],
        )


class SnapshotAppTests(AppTestCase):
    async def test_wall_clock_after_the_due_dates_blocks_retrieval_outside_the_app(self):
        for release_id, release_dir in RELEASES.items():
            with self.subTest(release=release_id):
                data_dir = Path(self.tempdir.name) / f"control-{release_id}"
                install_knowledge_release(
                    data_dir, release_dir=release_dir, embedding_provider=self.provider
                )
                retriever = HybridRetriever.from_data_dir(data_dir, embedding_provider=self.provider)
                self.assertTrue(retriever.retrieve(QUESTION))
                with late_wall_clock():
                    self.assertEqual(retriever.retrieve(QUESTION), [])

    async def test_installed_release_still_cites_its_sources_after_the_due_dates(self):
        for release_id, release_dir in RELEASES.items():
            with self.subTest(release=release_id):
                self.data_dir = Path(self.tempdir.name) / f"installed-{release_id}"
                install_knowledge_release(
                    self.data_dir, release_dir=release_dir, embedding_provider=self.provider
                )
                client = self.make_client()
                with late_wall_clock():
                    response = await self.ask(client)
                self.assert_cited_answer(response, release_id)

    async def test_pin_governs_outside_requests_and_the_snapshot_clock_inside(self):
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        client = self.make_client()
        with late_wall_clock():
            response = await self.ask(client)
            self.assertEqual(now_utc(), LATE_WALL_CLOCK)
        self.assert_cited_answer(response, "kr-2026-07-06.1")
        self.assertEqual(now_utc(), fixture_clock.FIXTURE_EVALUATION_TIME_UTC)

    async def test_snapshot_clock_does_not_leak_between_requests_or_apps(self):
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        client = self.make_client()
        with late_wall_clock():
            await client.get("/")
            self.assertEqual(now_utc(), LATE_WALL_CLOCK)
            self.assertFalse(
                assess_source_freshness(evidence_due("2026-10-06T12:00:00Z")).answer_eligible
            )

    async def test_request_follows_the_active_release_after_it_changes(self):
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        client = self.make_client()
        first = (await client.get("/status")).json()["corpus"]["knowledge_release_id"]
        install_knowledge_release(
            self.data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )
        second = (await client.get("/status")).json()["corpus"]["knowledge_release_id"]
        self.assertEqual((first, second), ("kr-2026-07-06.1", "kr-2026-09-05.1"))
        with late_wall_clock():
            response = await self.ask(client)
        self.assert_cited_answer(response, "kr-2026-09-05.1")

    async def test_fresh_install_inside_the_app_indexes_at_the_release_snapshot(self):
        for release_id, release_dir in RELEASES.items():
            with self.subTest(release=release_id):
                self.data_dir = Path(self.tempdir.name) / f"fresh-{release_id}"
                client = self.make_client(initial_release_dir=release_dir)
                with late_wall_clock():
                    response = await self.ask(client)
                self.assert_cited_answer(response, release_id)

    async def test_installing_after_the_due_dates_does_not_drop_sources(self):
        # Eligibility is judged at retrieval time (documents carry no freshness inputs
        # when indexed), so install paths need no snapshot scope: a worker-thread install
        # or an install on a late wall clock still serves its sources in the app.
        for release_id, release_dir in RELEASES.items():
            with self.subTest(release=release_id):
                self.data_dir = Path(self.tempdir.name) / f"late-install-{release_id}"
                with late_wall_clock():
                    install_knowledge_release(
                        self.data_dir, release_dir=release_dir, embedding_provider=self.provider
                    )
                    response = await self.ask(self.make_client())
                self.assert_cited_answer(response, release_id)


class SnapshotLabelTests(AppTestCase):
    async def test_every_page_states_the_snapshot_date_and_that_it_is_not_current(self):
        for release_id, release_dir in RELEASES.items():
            with self.subTest(release=release_id):
                self.data_dir = Path(self.tempdir.name) / f"label-{release_id}"
                install_knowledge_release(
                    self.data_dir, release_dir=release_dir, embedding_provider=self.provider
                )
                client = self.make_client()
                date = release_snapshot_date(release_dir)
                html = (await client.get("/")).text
                self.assertIn(f"Knowledge snapshot from {date}", html)
                self.assertIn("Not kept current", html)
                self.assertIn("not legal advice", html)

    async def test_label_survives_an_unavailable_corpus_without_inventing_a_date(self):
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        (self.data_dir / "active-release.json").write_text("{not json", encoding="utf-8")
        html = (await self.make_client().get("/")).text
        self.assertIn("Knowledge is a snapshot", html)
        self.assertIn("Not kept current", html)
        self.assertNotIn("Knowledge snapshot from", html)

    async def test_fresh_tomato_wording_is_scoped_to_the_snapshot_not_today(self):
        install_knowledge_release(
            self.data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )
        client = self.make_client()
        with late_wall_clock():
            page = await self.ask(client)
            fragment = await client.post(
                "/ask",
                data={"question": QUESTION},
                headers={"Origin": "http://testserver", "HX-Request": "true"},
            )
        note = "Freshness is judged as of the snapshot date (2026-09-05), not today."
        # Answer trust list, drawer trust reasons, drawer citation score.
        self.assertGreaterEqual(page.text.count(note), 3)
        self.assertGreaterEqual(fragment.text.count(note), 3)
        self.assertEqual(
            page.text.count(note), page.text.count("Fresh Tomato Score:"), page.text[:200]
        )


class FreshInstallDefaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.provider = DeterministicEmbeddingProviderFixture()

    def test_newest_bundled_release_is_the_september_snapshot(self):
        from danish_rag.knowledge_release import newest_bundled_release_dir

        self.assertEqual(newest_bundled_release_dir(), SEPTEMBER_RELEASE)
        # Same signature and trust-root verification as any installed release.
        verified = verify_knowledge_release(newest_bundled_release_dir())
        self.assertEqual(verified["manifest"]["knowledge_release_id"], "kr-2026-09-05.1")

    def test_newest_is_chosen_by_numeric_release_id_and_ignores_other_entries(self):
        from danish_rag.knowledge_release import newest_bundled_release_dir

        catalog = self.root / "catalog"
        for name in ("kr-2026-09-05.2", "kr-2026-09-05.10", "kr-2026-08-31.99", "notes"):
            (catalog / name).mkdir(parents=True)
        (catalog / "kr-2027-01-01.1").write_text("not a directory", encoding="utf-8")
        self.assertEqual(newest_bundled_release_dir(catalog), catalog / "kr-2026-09-05.10")

    def test_empty_catalog_has_no_newest_release(self):
        from danish_rag.knowledge_release import newest_bundled_release_dir

        (self.root / "empty").mkdir()
        with self.assertRaises(KnowledgeReleaseError):
            newest_bundled_release_dir(self.root / "empty")

    def test_ensure_installs_the_requested_initial_release_on_a_fresh_data_dir(self):
        installed = ensure_minimal_knowledge_release(
            self.root / "data", release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )
        self.assertEqual(installed["manifest"]["knowledge_release_id"], "kr-2026-09-05.1")

    def test_ensure_keeps_the_fixture_default_and_the_active_release(self):
        data_dir = self.root / "data"
        installed = ensure_minimal_knowledge_release(data_dir, embedding_provider=self.provider)
        self.assertEqual(installed["manifest"]["knowledge_release_id"], "kr-2026-07-06.1")
        again = ensure_minimal_knowledge_release(
            data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )
        self.assertEqual(again["manifest"]["knowledge_release_id"], "kr-2026-07-06.1")

    def test_a_tampered_initial_release_is_refused_not_installed(self):
        tampered = self.root / "tampered" / "kr-2026-09-05.1"
        shutil.copytree(SEPTEMBER_RELEASE, tampered)
        documents = tampered / "corpus" / "documents.json"
        documents.write_text(documents.read_text(encoding="utf-8") + " ", encoding="utf-8")
        data_dir = self.root / "data"
        with self.assertRaises(KnowledgeReleaseError):
            ensure_minimal_knowledge_release(
                data_dir, release_dir=tampered, embedding_provider=self.provider
            )
        self.assertFalse((data_dir / "active-release.json").exists())

    def test_production_app_starts_fresh_installs_from_the_newest_bundled_release(self):
        from danish_rag import local_app

        self.assertEqual(local_app.app.state.initial_release_dir, SEPTEMBER_RELEASE)

    def test_library_default_stays_the_fixture_release(self):
        # Tests and tools that call create_app() directly keep the July fixture.
        app = create_app(data_dir=self.root / "data", config_path=self.root / "c.json")
        self.assertEqual(app.state.initial_release_dir, BUNDLED_MINIMAL_RELEASE)


def release_snapshot_date(release_dir: Path) -> str:
    return json.loads((release_dir / "manifest.json").read_text())["created_at_utc"][:10]


if __name__ == "__main__":
    unittest.main()
