"""Retriever cache (#79): one hybrid retriever per active release + embedding model."""

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from danish_rag import snapshot_clock
from danish_rag.knowledge_release import (
    install_knowledge_release,
    install_minimal_knowledge_release,
)
from danish_rag.retrieval import HybridRetriever
from danish_rag.retriever_cache import RetrieverCache
from tests import test_snapshot_mode as sm


class BuildCounter:
    """Counts HybridRetriever.from_data_dir calls and keeps what they built."""

    def __init__(self) -> None:
        self.built: list[HybridRetriever] = []

    def __enter__(self):
        real = HybridRetriever.from_data_dir.__func__

        def counting(cls, *args, **kwargs):
            retriever = real(cls, *args, **kwargs)
            self.built.append(retriever)
            return retriever

        self._patch = patch.object(HybridRetriever, "from_data_dir", classmethod(counting))
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


class RetrieverCacheAppTests(sm.AppTestCase):
    def setUp(self) -> None:
        super().setUp()
        install_minimal_knowledge_release(
            self.data_dir, embedding_provider=self.provider, clock=sm.wall_clock
        )

    def install_september(self) -> None:
        install_knowledge_release(
            self.data_dir,
            release_dir=sm.SEPTEMBER_RELEASE,
            embedding_provider=self.provider,
            clock=sm.wall_clock,
        )

    async def test_repeated_questions_on_one_release_build_the_retriever_once(self):
        client = self.make_client()
        with BuildCounter() as counter:
            for _ in range(3):
                self.assert_cited_answer(await self.ask(client), "kr-2026-07-06.1")
        self.assertEqual(len(counter.built), 1)

    async def test_successful_install_switches_answers_to_the_new_release(self):
        client = self.make_client()
        with BuildCounter() as counter:
            await self.ask(client)
            self.install_september()
            response = await self.ask(client)
            await self.ask(client)
        self.assertEqual(len(counter.built), 2)
        self.assertEqual(
            counter.built[-1].manifest["knowledge_release_id"], "kr-2026-09-05.1"
        )
        self.assertEqual(response.status_code, 200)

    async def test_embedding_reindex_switches_to_the_new_index(self):
        client = self.make_client()
        with BuildCounter() as counter:
            await self.ask(client)
            install_minimal_knowledge_release(
                self.data_dir,
                embedding_model="embeddinggemma:latest",
                embedding_provider=self.provider,
                clock=sm.wall_clock,
            )
            self.assertEqual((await self.ask(client)).status_code, 200)
            await self.ask(client)
        self.assertEqual(len(counter.built), 2)
        self.assertEqual(
            [r.dense_index["metadata"]["embedding_model"] for r in counter.built],
            ["embeddinggemma", "embeddinggemma:latest"],
        )

    async def test_failed_install_keeps_answering_from_the_prior_release(self):
        client = self.make_client()

        def fail(phase: str) -> None:
            if phase == "activation":
                raise RuntimeError("injected")

        with BuildCounter() as counter:
            await self.ask(client)
            with self.assertRaises(RuntimeError):
                install_knowledge_release(
                    self.data_dir,
                    release_dir=sm.SEPTEMBER_RELEASE,
                    embedding_provider=self.provider,
                    fault_injector=fail,
                    clock=sm.wall_clock,
                )
            self.assert_cited_answer(await self.ask(client), "kr-2026-07-06.1")
        self.assertEqual(len(counter.built), 1)

    async def test_failed_reindex_keeps_answering_from_the_prior_index(self):
        client = self.make_client()
        with BuildCounter() as counter:
            await self.ask(client)
            with self.assertRaises(Exception):
                install_minimal_knowledge_release(
                    self.data_dir,
                    embedding_model="unreviewed-local-embedder",
                    embedding_provider=self.provider,
                    clock=sm.wall_clock,
                )
            self.assertEqual((await self.ask(client)).status_code, 200)
        self.assertEqual(len(counter.built), 1)

    async def outcome(self, client):
        response = await self.ask(client)
        return response.status_code, response.text

    async def test_eligibility_lapse_after_the_build_matches_a_fresh_build(self):
        # Eligibility changing over time (a freshness window lapsing) must give the
        # same outcome as building the retriever from scratch at that time.
        from danish_rag import retrieval

        real = retrieval._is_release_eligible
        with patch.object(
            snapshot_clock, "BUNDLED_CATALOG_DIR", Path(self.tempdir.name) / "none"
        ), patch.object(
            retrieval,
            "_is_release_eligible",
            lambda document, at: real(document, at) and at < sm.LATE_WALL_CLOCK,
        ):
            cached_client = self.make_client()
            self.assertEqual((await self.ask(cached_client)).status_code, 200)
            with sm.late_wall_clock():
                cached = await self.outcome(cached_client)
                fresh = await self.outcome(self.make_client())
        self.assertNotEqual(fresh[0], 200)
        self.assertEqual(cached[0], fresh[0])
        self.assertIn("re-index required", cached[1])

    async def test_embedding_model_drift_matches_a_fresh_build(self):
        client = self.make_client()
        self.assertEqual((await self.ask(client)).status_code, 200)
        self.provider._model_identity = {
            **self.provider._model_identity,
            "digest": "sha256:swapped-model",
        }
        cached = await self.outcome(client)
        fresh = await self.outcome(self.make_client())
        self.assertEqual(fresh[0], 503)
        self.assertEqual(cached[0], fresh[0])
        self.assertIn("re-index required", cached[1])

    async def test_failed_build_does_not_replace_the_cached_retriever(self):
        client = self.make_client()
        with BuildCounter() as counter:
            await self.ask(client)
            self.install_september()
            with patch.object(
                HybridRetriever,
                "from_data_dir",
                classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(OSError("boom"))),
            ):
                self.assertEqual((await self.ask(client)).status_code, 503)
        self.assertEqual(len(counter.built), 1)

    async def test_cached_retriever_reads_the_request_clock_on_every_call(self):
        # Non-bundled release: wall-clock freshness, evaluated per request, never at
        # the time of the request that built the retriever.
        self.install_september()
        client = self.make_client()
        with patch.object(
            snapshot_clock, "BUNDLED_CATALOG_DIR", Path(self.tempdir.name) / "none"
        ), BuildCounter() as counter:
            fresh = await self.ask(client)
            with sm.late_wall_clock():
                blocked = await self.ask(client)
            again = await self.ask(client)
        self.assertEqual(len(counter.built), 1)
        self.assertEqual(fresh.status_code, 200)
        self.assertNotIn("Fresh Tomato Score: High", blocked.text)
        self.assertIn("Fresh Tomato Score: High", again.text)
        self.assertEqual(self.generator.clock_seen, [sm.FIXED_TIME, sm.FIXED_TIME])

    async def test_cached_retriever_under_a_bundled_snapshot_evaluates_at_the_snapshot(self):
        client = self.make_client()
        with BuildCounter() as counter, sm.late_wall_clock():
            first = await self.ask(client)
            second = await self.ask(client)
        self.assertEqual(len(counter.built), 1)
        self.assertIn("Fresh Tomato Score: High", first.text)
        self.assertIn("Fresh Tomato Score: High", second.text)
        self.assertEqual(self.generator.clock_seen, [sm.JULY_SNAPSHOT] * 2)

    async def test_request_never_gets_a_cached_retriever_for_another_release(self):
        client = self.make_client()
        with BuildCounter():
            await self.ask(client)
            self.install_september()
            with sm.late_wall_clock():
                response = await self.ask(client)
        self.assert_cited_answer(response, "kr-2026-09-05.1")
        self.assertEqual(self.generator.clock_seen[-1], sm.SEPTEMBER_SNAPSHOT)


class RetrieverCacheUnitTests(unittest.TestCase):
    def test_concurrent_requests_for_one_key_build_once(self):
        cache = RetrieverCache()
        builds = []

        def build():
            builds.append(1)
            time.sleep(0.05)
            return "retriever"

        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    cache.get_or_build("k", build, lambda r: "k")
                )
            )
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(builds), 1)
        self.assertEqual(results, ["retriever"] * 8)

    def test_hits_do_not_wait_for_a_slow_build_of_another_key(self):
        cache = RetrieverCache()
        cache.get_or_build("a", lambda: "old", lambda r: "a")
        started, release = threading.Event(), threading.Event()

        def slow():
            started.set()
            release.wait(5)
            return "new"

        builder = threading.Thread(
            target=lambda: cache.get_or_build("b", slow, lambda r: "b")
        )
        builder.start()
        self.assertTrue(started.wait(5))
        hit = []
        waiter = threading.Thread(
            target=lambda: hit.append(cache.get_or_build("a", lambda: "x", lambda r: "a"))
        )
        waiter.start()
        waiter.join(1)
        finished = not waiter.is_alive()
        release.set()
        builder.join()
        waiter.join()
        self.assertTrue(finished)
        self.assertEqual(hit, ["old"])

    def test_failed_build_leaves_the_entry_and_is_not_cached(self):
        cache = RetrieverCache()
        cache.get_or_build("a", lambda: "old", lambda r: "a")

        def boom():
            raise OSError("x")

        with self.assertRaises(OSError):
            cache.get_or_build("b", boom, lambda r: "b")
        self.assertEqual(cache.get_or_build("a", lambda: "rebuilt", lambda r: "a"), "old")

    def test_entry_is_stored_under_the_key_of_what_was_actually_built(self):
        cache = RetrieverCache()
        self.assertEqual(cache.get_or_build("stale", lambda: "new", lambda r: "actual"), "new")
        self.assertEqual(cache.get_or_build("actual", lambda: "other", lambda r: "actual"), "new")


if __name__ == "__main__":
    unittest.main()
