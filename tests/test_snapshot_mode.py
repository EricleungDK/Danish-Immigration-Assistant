"""Snapshot mode (#67): the running app evaluates freshness at the release snapshot time."""

import json
import shutil
import sqlite3
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


def at(instant: datetime):
    """A resolved snapshot state at `instant` (no release involved)."""

    from danish_rag.snapshot_clock import Snapshot, SnapshotState

    return SnapshotState(Snapshot("kr-test", instant))


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

        with late_wall_clock(), snapshot_clock(at(JULY_SNAPSHOT)):
            self.assertEqual(now_utc(), JULY_SNAPSHOT)
            self.assertTrue(
                assess_source_freshness(evidence_due("2026-10-06T12:00:00Z")).answer_eligible
            )

    def test_snapshot_time_is_not_an_exemption_from_the_due_date(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with snapshot_clock(at(datetime(2026, 10, 7, tzinfo=timezone.utc))):
            self.assertFalse(
                assess_source_freshness(evidence_due("2026-10-06T12:00:00Z")).answer_eligible
            )

    def test_scope_restores_the_underlying_clock_on_exit(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with late_wall_clock():
            with snapshot_clock(at(JULY_SNAPSHOT)):
                pass
            self.assertEqual(now_utc(), LATE_WALL_CLOCK)
        self.assertEqual(now_utc(), fixture_clock.FIXTURE_EVALUATION_TIME_UTC)

    def test_scope_is_isolated_per_thread(self):
        from danish_rag.snapshot_clock import snapshot_clock

        seen = []
        with late_wall_clock(), snapshot_clock(at(JULY_SNAPSHOT)):
            worker = threading.Thread(target=lambda: seen.append(now_utc()))
            worker.start()
            worker.join()
            self.assertEqual(now_utc(), JULY_SNAPSHOT)
        self.assertEqual(seen, [LATE_WALL_CLOCK])

    def test_scopes_nest_and_the_inner_scope_wins(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with snapshot_clock(at(JULY_SNAPSHOT)):
            with snapshot_clock(at(SEPTEMBER_SNAPSHOT)):
                self.assertEqual(now_utc(), SEPTEMBER_SNAPSHOT)
            self.assertEqual(now_utc(), JULY_SNAPSHOT)

    def test_scope_reads_the_shared_state_at_each_call_without_io(self):
        from danish_rag.snapshot_clock import Snapshot, snapshot_clock

        state = at(JULY_SNAPSHOT)
        with snapshot_clock(state):
            self.assertEqual(now_utc(), JULY_SNAPSHOT)
            state.snapshot = Snapshot("kr-test", SEPTEMBER_SNAPSHOT)
            self.assertEqual(now_utc(), SEPTEMBER_SNAPSHOT)

    def test_state_without_a_snapshot_leaves_the_underlying_clock(self):
        from danish_rag.snapshot_clock import SnapshotState, snapshot_clock

        unavailable = SnapshotState()
        unavailable.unavailable = True
        for state in (SnapshotState(), unavailable):
            with late_wall_clock(), snapshot_clock(state):
                self.assertEqual(now_utc(), LATE_WALL_CLOCK)

    def test_now_without_tz_mirrors_datetime_now_local_naive_time(self):
        import os
        import time

        from danish_rag.snapshot_clock import snapshot_clock

        previous = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        try:
            with snapshot_clock(at(JULY_SNAPSHOT)):
                naive = source_freshness.datetime.now()
            self.assertIsNone(naive.tzinfo)
            self.assertEqual(naive, datetime(2026, 7, 6, 9, 0))  # UTC+9
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

    def test_a_subclass_of_the_wrapper_is_wrapped_again(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with snapshot_clock(at(JULY_SNAPSHOT)):
            installed = source_freshness.datetime
        replaced = type("Replaced", (installed,), {})  # inherits the wrapper's marker
        previous = source_freshness.datetime
        source_freshness.datetime = replaced
        try:
            with snapshot_clock(at(SEPTEMBER_SNAPSHOT)):
                self.assertEqual(now_utc(), SEPTEMBER_SNAPSHOT)
        finally:
            source_freshness.datetime = previous

    def test_clock_works_over_the_real_datetime_and_after_the_pin_is_removed(self):
        from danish_rag.snapshot_clock import snapshot_clock

        fixture_clock.unpin_freshness_clock()
        try:
            with snapshot_clock(at(JULY_SNAPSHOT)):
                self.assertEqual(now_utc(), JULY_SNAPSHOT)
            self.assertIsNot(source_freshness.datetime, None)
            self.assertAlmostEqual(
                now_utc().timestamp(), datetime.now(timezone.utc).timestamp(), delta=60
            )
        finally:
            fixture_clock.pin_freshness_clock()

    def test_wrapped_datetime_still_recognises_real_datetimes_and_parses(self):
        from danish_rag.snapshot_clock import snapshot_clock

        with snapshot_clock(at(JULY_SNAPSHOT)):
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

        with late_wall_clock(), snapshot_clock(at(JULY_SNAPSHOT)):
            self.assertEqual(
                evidence_integrity.utc_now_seconds(), "2027-01-01T00:00:00Z"
            )
            self.assertEqual(grounded._utc_now(), "2027-01-01T00:00:00Z")


class SnapshotResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.data_dir = self.root / "data"
        self.provider = DeterministicEmbeddingProviderFixture()

    @staticmethod
    def manifest(release_dir: Path) -> dict:
        return json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))

    def test_bundled_releases_resolve_to_their_manifest_created_at(self):
        from danish_rag.snapshot_clock import bundled_snapshot

        july = bundled_snapshot(self.manifest(BUNDLED_MINIMAL_RELEASE))
        september = bundled_snapshot(self.manifest(SEPTEMBER_RELEASE))
        self.assertEqual((july.release_id, july.time, july.date), ("kr-2026-07-06.1", JULY_SNAPSHOT, "2026-07-06"))
        self.assertEqual((september.time, september.date), (SEPTEMBER_SNAPSHOT, "2026-09-05"))
        self.assertEqual(
            september.metadata(),
            {"release_id": "kr-2026-09-05.1", "snapshot_date": "2026-09-05", "kept_current": False},
        )

    def test_only_manifests_identical_to_a_bundled_one_are_snapshots(self):
        from danish_rag.snapshot_clock import bundled_snapshot

        genuine = self.manifest(SEPTEMBER_RELEASE)
        altered_time = {**genuine, "created_at_utc": "2026-01-01T00:00:00Z"}
        altered_hash = json.loads(json.dumps(genuine))
        altered_hash["integrity"]["manifest_sha256"] = "0" * 64
        unknown = {**genuine, "knowledge_release_id": "kr-2030-01-01.1"}
        traversal = {**genuine, "knowledge_release_id": "../kr-2026-09-05.1"}
        for name, manifest in {
            "altered created_at": altered_time,
            "altered integrity": altered_hash,
            "unknown id": unknown,
            "path traversal": traversal,
            "not a manifest": {},
        }.items():
            with self.subTest(name):
                self.assertIsNone(bundled_snapshot(manifest))

    def test_a_missing_bundled_catalogue_means_no_snapshot(self):
        from danish_rag import snapshot_clock

        with patch.object(snapshot_clock, "BUNDLED_CATALOG_DIR", self.root / "none"):
            self.assertIsNone(snapshot_clock.bundled_snapshot(self.manifest(SEPTEMBER_RELEASE)))

    def test_state_starts_unresolved_so_the_wall_clock_applies(self):
        from danish_rag.snapshot_clock import SnapshotState, snapshot_clock

        state = SnapshotState()
        self.assertEqual((state.snapshot, state.unavailable, state.time), (None, False, None))
        with late_wall_clock(), snapshot_clock(state):
            self.assertEqual(now_utc(), LATE_WALL_CLOCK)

    def test_mark_unavailable_logs_a_warning_and_drops_any_snapshot(self):
        from danish_rag.snapshot_clock import SnapshotState

        state = SnapshotState()
        state.adopt(self.manifest(BUNDLED_MINIMAL_RELEASE))
        with self.assertLogs("danish_rag.snapshot_clock", "WARNING") as logs:
            state.mark_unavailable(OSError("index unreadable"))
        self.assertIn("index unreadable", "\n".join(logs.output))
        self.assertEqual((state.snapshot, state.unavailable), (None, True))
        self.assertIsNone(state.metadata())
        self.assertEqual(state.status, "unavailable")

    def test_status_names_the_three_states(self):
        from danish_rag.snapshot_clock import SnapshotState

        state = SnapshotState()
        self.assertEqual(state.status, "none")
        state.adopt(self.manifest(BUNDLED_MINIMAL_RELEASE))
        self.assertEqual(state.status, "snapshot")

    def test_snapshot_module_does_no_release_io_of_its_own(self):
        from danish_rag import snapshot_clock

        self.assertFalse(hasattr(snapshot_clock, "load_active_release"))
        self.assertFalse(hasattr(snapshot_clock, "SnapshotResolver"))
        self.assertFalse(hasattr(snapshot_clock.SnapshotState, "resolve_active"))

    def test_bundled_manifest_digests_are_memoized_until_the_file_changes(self):
        from danish_rag import snapshot_clock

        catalog = self.root / "catalog"
        shutil.copytree(SEPTEMBER_RELEASE, catalog / "kr-2026-09-05.1")
        manifest = self.manifest(SEPTEMBER_RELEASE)
        reads = []
        real = Path.read_text
        counting = lambda path, *a, **k: (reads.append(path.name), real(path, *a, **k))[1]
        with patch.object(snapshot_clock, "BUNDLED_CATALOG_DIR", catalog), patch.object(
            Path, "read_text", counting
        ):
            self.assertIsNotNone(snapshot_clock.bundled_snapshot(manifest))
            first = reads.count("manifest.json")
            for _ in range(5):
                self.assertIsNotNone(snapshot_clock.bundled_snapshot(manifest))
            self.assertEqual(reads.count("manifest.json"), first)  # dict lookups only
            # The bundled file changing invalidates the memo.
            path = catalog / "kr-2026-09-05.1" / "manifest.json"
            path.write_text(path.read_text(encoding="utf-8").replace("{", '{"probe": 1,', 1), encoding="utf-8")
            self.assertIsNone(snapshot_clock.bundled_snapshot(manifest))

    def test_manifest_identity_is_strict_about_json_types(self):
        from danish_rag import snapshot_clock

        catalog = self.root / "catalog"
        shutil.copytree(SEPTEMBER_RELEASE, catalog / "kr-2026-09-05.1")
        path = catalog / "kr-2026-09-05.1" / "manifest.json"
        genuine = {**self.manifest(SEPTEMBER_RELEASE), "probe": 1}
        path.write_text(json.dumps(genuine), encoding="utf-8")
        with patch.object(snapshot_clock, "BUNDLED_CATALOG_DIR", catalog):
            self.assertIsNotNone(snapshot_clock.bundled_snapshot(genuine))
            for name, probe in {"float": 1.0, "bool": True, "nan": float("nan"), "string": "1"}.items():
                with self.subTest(name):
                    self.assertIsNone(snapshot_clock.bundled_snapshot({**genuine, "probe": probe}))

    def test_adopt_replaces_a_previous_resolution(self):
        from danish_rag.snapshot_clock import SnapshotState

        state = SnapshotState()
        state.adopt(self.manifest(BUNDLED_MINIMAL_RELEASE))
        self.assertEqual(state.time, JULY_SNAPSHOT)
        state.adopt(self.manifest(SEPTEMBER_RELEASE))
        self.assertEqual(state.time, SEPTEMBER_SNAPSHOT)
        state.adopt({"knowledge_release_id": "kr-2030-01-01.1"})
        self.assertEqual((state.snapshot, state.unavailable), (None, False))

    def test_a_load_failure_is_sticky_for_the_rest_of_the_request(self):
        from danish_rag.snapshot_clock import SnapshotState

        state = SnapshotState()
        with self.assertLogs("danish_rag.snapshot_clock", "WARNING"):
            state.mark_unavailable(OSError("boom"))
        state.adopt(self.manifest(BUNDLED_MINIMAL_RELEASE))
        self.assertEqual((state.snapshot, state.status), (None, "unavailable"))

    def test_offsetless_or_malformed_snapshot_times_are_rejected(self):
        from danish_rag.snapshot_clock import parse_snapshot_time

        self.assertEqual(parse_snapshot_time("2026-07-06T00:00:00Z"), JULY_SNAPSHOT)
        for created in (None, "2026-07-06T00:00:00", "not a date", 5):
            with self.subTest(created=created), self.assertRaises(ValueError):
                parse_snapshot_time(created)


QUESTION = "What Danish test do I need for permanent residence?"
RELEASES = {"kr-2026-07-06.1": BUNDLED_MINIMAL_RELEASE, "kr-2026-09-05.1": SEPTEMBER_RELEASE}


class FirstEvidenceAnswerGenerator:
    def __init__(self) -> None:
        self.clock_seen: list[datetime] = []

    def generate(self, *, evidence, **_):
        self.clock_seen.append(now_utc())  # the freshness clock while answering
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
        self.generator = FirstEvidenceAnswerGenerator()
        app = create_app(
            config_path=self.config_path,
            data_dir=self.data_dir,
            answer_generator=self.generator,
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

    async def test_unavailable_release_does_not_claim_a_snapshot_basis(self):
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        (self.data_dir / "active-release.json").write_text("{not json", encoding="utf-8")
        client = self.make_client()
        with self.assertLogs("danish_rag.snapshot_clock", "WARNING"):
            html = (await client.get("/")).text
        self.assertIn("Snapshot date unavailable", html)
        self.assertIn("judged at today's date", html)
        self.assertIn("not legal advice", html)
        self.assertNotIn("Knowledge snapshot from", html)
        self.assertNotIn("as of the snapshot date", html)

    async def test_non_bundled_release_keeps_wall_clock_freshness_and_no_label(self):
        from danish_rag import snapshot_clock

        install_knowledge_release(
            self.data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )
        client = self.make_client()
        with patch.object(snapshot_clock, "BUNDLED_CATALOG_DIR", Path(self.tempdir.name) / "none"):
            page = (await client.get("/")).text
            self.assertNotIn("Knowledge snapshot from", page)
            self.assertNotIn("Snapshot date unavailable", page)
            self.assertIn("not legal advice", page)
            status = (await client.get("/status")).json()
            self.assertEqual((status["snapshot_status"], status["knowledge_snapshot"]), ("none", None))
            # Real-time hard block still fires: past the due date, no sources.
            with late_wall_clock():
                blocked = await self.ask(client)
            self.assertNotIn("Fresh Tomato Score: High", blocked.text)
            self.assertNotIn("as of the snapshot date", blocked.text)
            self.assertEqual(self.generator.clock_seen, [])
            # Before the due date the same release is served at the wall clock.
            fresh = await self.ask(client)
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(self.generator.clock_seen, [fixture_clock.FIXTURE_EVALUATION_TIME_UTC])
        self.assertNotIn("as of the snapshot date", fresh.text)

    async def test_first_request_on_a_fresh_install_shows_the_installed_release_snapshot(self):
        client = self.make_client(initial_release_dir=SEPTEMBER_RELEASE)
        self.assertIn("Knowledge snapshot from 2026-09-05", (await client.get("/")).text)

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


class SnapshotConsistencyTests(AppTestCase):
    """The snapshot comes only from the verified release the request actually loaded."""

    async def test_snapshot_follows_the_release_the_retriever_loaded(self):
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        client = self.make_client()
        real = HybridRetriever.from_data_dir.__func__

        def switching(cls, *args, **kwargs):
            # A concurrent install activates the September release after the request began.
            install_knowledge_release(
                self.data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
            )
            return real(cls, *args, **kwargs)

        with late_wall_clock(), patch.object(
            HybridRetriever, "from_data_dir", classmethod(switching)
        ):
            response = await self.ask(client)
        self.assert_cited_answer(response, "kr-2026-09-05.1")
        self.assertEqual(self.generator.clock_seen, [SEPTEMBER_SNAPSHOT])
        self.assertEqual(self.stored_snapshot()["release_id"], "kr-2026-09-05.1")

    def stored_snapshot(self):
        store = ConversationStore(self.data_dir / "conversations.sqlite3")
        return store.get_conversation(store.list_conversations()[0]["id"])["answer"][
            "knowledge_snapshot"
        ]

    async def test_snapshot_mode_adds_no_release_verification_to_a_request(self):
        from danish_rag import knowledge_release, snapshot_clock

        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        client = self.make_client()
        calls = []
        real_load = knowledge_release.load_active_release
        real_verify = knowledge_release.verify_knowledge_release

        def counting(real, name):
            return lambda *a, **k: (calls.append(name), real(*a, **k))[1]

        with patch.object(
            knowledge_release, "load_active_release", counting(real_load, "load")
        ), patch.object(
            snapshot_clock, "load_active_release", counting(real_load, "load"), create=True
        ), patch.object(knowledge_release, "verify_knowledge_release", counting(real_verify, "verify")):
            # Routes that touch no release do no release I/O at all.
            for path in ("/static/app.css", "/vendor/htmx.min.js", "/conversations/export.json"):
                await client.get(path)
            self.assertEqual(calls, [])
            # /status does exactly the verification its own work needs (no extra read).
            knowledge_release.ensure_minimal_knowledge_release(
                self.data_dir, embedding_provider=self.provider
            )
            knowledge_release.active_corpus_summary(self.data_dir)
            expected = list(calls)
            calls.clear()
            await client.get("/status")
        self.assertEqual(calls, expected)

    async def test_corrupt_release_artifacts_leave_no_snapshot_claim(self):
        corruptions = {
            "documents": lambda d: (d / "corpus/kr-2026-07-06.1/documents.json").write_text("[]", encoding="utf-8"),
            "index metadata": lambda d: (d / "index/kr-2026-07-06.1/index-metadata.json").write_text("{not json", encoding="utf-8"),
        }
        for name, corrupt in corruptions.items():
            with self.subTest(name):
                self.data_dir = Path(self.tempdir.name) / f"corrupt-{name.replace(' ', '-')}"
                install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
                client = self.make_client()
                self.assertIn("Knowledge snapshot from 2026-07-06", (await client.get("/")).text)
                corrupt(self.data_dir)  # the active-release pointer file is untouched
                with self.assertLogs("danish_rag.snapshot_clock", "WARNING"):
                    page = (await client.get("/")).text
                self.assertIn("Snapshot date unavailable", page)
                self.assertNotIn("Knowledge snapshot from", page)
                with self.assertLogs("danish_rag.snapshot_clock", "WARNING"):
                    status = (await client.get("/status")).json()
                self.assertEqual(status["snapshot_status"], "unavailable")
                self.assertIsNone(status["knowledge_snapshot"])
                self.assertTrue(status["corpus_error"])
                with late_wall_clock(), self.assertLogs("danish_rag.snapshot_clock", "WARNING"):
                    response = await self.ask(client)
                self.assertNotIn("Fresh Tomato Score: High", response.text)
                self.assertEqual(self.generator.clock_seen, [])

    async def test_a_failed_retriever_load_marks_the_request_unavailable(self):
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        client = self.make_client()

        def failing(cls, *args, **kwargs):
            raise OSError("dense index unreadable")

        with late_wall_clock(), patch.object(
            HybridRetriever, "from_data_dir", classmethod(failing)
        ), self.assertLogs("danish_rag.snapshot_clock", "WARNING"):
            response = await self.ask(client)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Snapshot date unavailable", response.text)
        self.assertNotIn("Knowledge snapshot from", response.text)

    async def test_turns_carry_their_own_snapshot_and_old_turns_carry_none(self):
        install_minimal_knowledge_release(self.data_dir, embedding_provider=self.provider)
        client = self.make_client()
        await self.ask(client)
        store = ConversationStore(self.data_dir / "conversations.sqlite3")
        conversation_id = store.list_conversations()[0]["id"]
        install_knowledge_release(
            self.data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )

        page = (await client.get(f"/conversations/{conversation_id}")).text
        self.assertIn("Knowledge snapshot from 2026-09-05", page)  # current release
        self.assertIn("as of the snapshot date (2026-07-06), not today", page)  # the turn's
        self.assertNotIn("(2026-09-05), not today", page)

        with sqlite3.connect(self.data_dir / "conversations.sqlite3") as connection:
            for table in ("conversations", "conversation_turns"):
                connection.execute(
                    f"UPDATE {table} SET answer_json = json_remove(answer_json, '$.knowledge_snapshot')"
                )
        page = (await client.get(f"/conversations/{conversation_id}")).text
        self.assertIn("Fresh Tomato Score:", page)
        self.assertNotIn("not today", page)

    async def test_status_and_exports_carry_explicit_snapshot_metadata(self):
        install_knowledge_release(
            self.data_dir, release_dir=SEPTEMBER_RELEASE, embedding_provider=self.provider
        )
        client = self.make_client()
        expected = {"release_id": "kr-2026-09-05.1", "snapshot_date": "2026-09-05", "kept_current": False}
        status = (await client.get("/status")).json()
        self.assertEqual((status["snapshot_status"], status["knowledge_snapshot"]), ("snapshot", expected))
        await self.ask(client)
        store = ConversationStore(self.data_dir / "conversations.sqlite3")
        conversation_id = store.list_conversations()[0]["id"]
        one = (await client.get(f"/conversations/{conversation_id}/export.json")).json()
        every = (await client.get("/conversations/export.json")).json()
        turn = one["conversation"]["turns"][0]
        self.assertEqual(turn["answer"]["knowledge_snapshot"], expected)
        self.assertEqual(
            every["conversations"][0]["turns"][0]["answer"]["knowledge_snapshot"], expected
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

    def make_catalog(self, **releases: Path) -> Path:
        catalog = self.root / "catalog"
        for name, source in releases.items():
            shutil.copytree(source, catalog / name.replace("_", "."), dirs_exist_ok=False)
        return catalog

    def tamper(self, release_dir: Path) -> None:
        documents = release_dir / "corpus" / "documents.json"
        documents.write_text(documents.read_text(encoding="utf-8") + " ", encoding="utf-8")

    def test_newest_is_chosen_by_numeric_release_id_among_verified_releases(self):
        from danish_rag.knowledge_release import newest_bundled_release_dir

        catalog = self.make_catalog(
            **{"kr-2026-07-06_1": BUNDLED_MINIMAL_RELEASE, "kr-2026-09-05_2": SEPTEMBER_RELEASE,
               "kr-2026-09-05_10": SEPTEMBER_RELEASE}
        )
        (catalog / "notes").mkdir()
        (catalog / "kr-2027-01-01.1").write_text("not a directory", encoding="utf-8")
        self.assertEqual(newest_bundled_release_dir(catalog), catalog / "kr-2026-09-05.10")

    def test_a_broken_newer_entry_falls_back_to_the_newest_release_that_verifies(self):
        from danish_rag.knowledge_release import newest_bundled_release_dir

        catalog = self.make_catalog(
            **{"kr-2026-07-06_1": BUNDLED_MINIMAL_RELEASE, "kr-2026-09-05_1": SEPTEMBER_RELEASE,
               "kr-2026-10-01_1": SEPTEMBER_RELEASE}
        )
        self.tamper(catalog / "kr-2026-10-01.1")
        self.assertEqual(newest_bundled_release_dir(catalog), catalog / "kr-2026-09-05.1")

    def test_skipped_releases_are_logged_with_the_reason(self):
        from danish_rag.knowledge_release import newest_bundled_release_dir

        catalog = self.make_catalog(
            **{"kr-2026-09-05_1": SEPTEMBER_RELEASE, "kr-2026-10-01_1": SEPTEMBER_RELEASE}
        )
        self.tamper(catalog / "kr-2026-10-01.1")
        with self.assertLogs("danish_rag.knowledge_release", "WARNING") as logs:
            newest_bundled_release_dir(catalog)
        text = "\n".join(logs.output)
        self.assertIn("kr-2026-10-01.1", text)
        self.assertNotIn("kr-2026-09-05.1", text)

    def test_unexpected_errors_are_not_swallowed(self):
        from danish_rag import knowledge_release

        catalog = self.make_catalog(**{"kr-2026-09-05_1": SEPTEMBER_RELEASE})
        with patch.object(knowledge_release, "verify_knowledge_release", side_effect=TypeError("bug")):
            with self.assertRaises(TypeError):
                knowledge_release.newest_bundled_release_dir(catalog)

    def test_incompatible_releases_are_skipped(self):
        from danish_rag.knowledge_release import newest_bundled_release_dir

        catalog = self.make_catalog(**{"kr-2026-09-05_1": SEPTEMBER_RELEASE})
        self.assertEqual(
            newest_bundled_release_dir(catalog, application_version="0.0.1"),
            BUNDLED_MINIMAL_RELEASE,
        )

    def test_no_usable_bundled_release_falls_back_to_the_fixture_release(self):
        from danish_rag.knowledge_release import newest_bundled_release_dir

        (self.root / "empty").mkdir()
        broken = self.make_catalog(**{"kr-2026-09-05_1": SEPTEMBER_RELEASE})
        self.tamper(broken / "kr-2026-09-05.1")
        for name, catalog in {
            "missing": self.root / "missing", "empty": self.root / "empty", "broken": broken,
        }.items():
            with self.subTest(name):
                self.assertEqual(newest_bundled_release_dir(catalog), BUNDLED_MINIMAL_RELEASE)

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

    def test_production_app_resolves_the_newest_bundled_release_lazily(self):
        from danish_rag import local_app
        from danish_rag.knowledge_release import newest_bundled_release_dir

        self.assertIs(local_app.app.state.initial_release_dir, newest_bundled_release_dir)
        self.assertEqual(
            newest_bundled_release_dir(trust_root_path=None), SEPTEMBER_RELEASE
        )

    def test_initial_release_is_resolved_only_when_a_fresh_install_needs_it(self):
        calls = []

        def resolve(**kwargs):
            calls.append(kwargs)
            return SEPTEMBER_RELEASE

        data_dir = self.root / "data"
        for _ in range(2):
            installed = ensure_minimal_knowledge_release(
                data_dir, release_dir=resolve, embedding_provider=self.provider
            )
        self.assertEqual(installed["manifest"]["knowledge_release_id"], "kr-2026-09-05.1")
        self.assertEqual(len(calls), 1)

    def test_documented_launch_command_uses_the_newest_bundled_release(self):
        docs = Path(__file__).resolve().parents[1] / "docs" / "release-qualification.md"
        commands = [
            line for line in docs.read_text(encoding="utf-8").splitlines() if "create_app(" in line
        ]
        self.assertTrue(commands)
        for line in commands:
            self.assertIn("initial_release_dir=newest_bundled_release_dir", line)

    def test_library_default_stays_the_fixture_release(self):
        # Tests and tools that call create_app() directly keep the July fixture.
        app = create_app(data_dir=self.root / "data", config_path=self.root / "c.json")
        self.assertEqual(app.state.initial_release_dir, BUNDLED_MINIMAL_RELEASE)


def release_snapshot_date(release_dir: Path) -> str:
    return json.loads((release_dir / "manifest.json").read_text())["created_at_utc"][:10]


if __name__ == "__main__":
    unittest.main()
