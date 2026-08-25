"""
The durable cache tier — 1.2 (search memory survives a deploy) and 1.3
(embeddings stored by paper identity).

Every assertion here is about the thing that actually costs money or time on a
cold worker: whether the second worker re-runs a fan-out, re-embeds a paper it
has already embedded, or re-bills an LLM relevance verdict. The shared store is
exercised through a fake Mongo collection rather than mocked out, so the
encoding, expiry check and bulk paths are the ones that run in production.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core import shared_store


# ─── A Mongo collection thin enough to reason about ────────────────────────────

class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    async def to_list(self, length=None):
        return list(self._docs[:length] if length is not None else self._docs)


class _FakeCollection:
    def __init__(self):
        self.docs: dict = {}
        self.writes = 0

    def _matches(self, doc, query) -> bool:
        for field, expected in query.items():
            value = doc.get(field)
            if isinstance(expected, dict) and "$in" in expected:
                if value not in expected["$in"]:
                    return False
            elif value != expected:
                return False
        return True

    async def find_one(self, query, projection=None):
        for doc in self.docs.values():
            if self._matches(doc, query):
                return dict(doc)
        return None

    def find(self, query, projection=None):
        return _FakeCursor([dict(d) for d in self.docs.values() if self._matches(d, query)])

    async def update_one(self, filt, update, upsert=False):
        self.writes += 1
        doc_id = filt["_id"]
        doc = self.docs.setdefault(doc_id, {"_id": doc_id}) if upsert else self.docs.get(doc_id)
        if doc is None:
            return None
        doc.update(update.get("$set") or {})
        return None

    async def bulk_write(self, operations, ordered=False):
        for op in operations:
            await self.update_one(op._filter, op._doc, upsert=op._upsert)
        return None

    async def delete_one(self, filt):
        self.docs.pop(filt.get("_id"), None)
        return None


class _FakeDb:
    def __init__(self):
        self.collections: dict = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeCollection())


class SharedStoreCase(unittest.IsolatedAsyncioTestCase):
    """Base: a live shared store backed by the fake db, torn down after."""

    def setUp(self):
        self.db = _FakeDb()
        self._patch = patch.object(shared_store, "_collection", lambda name: self.db[name])
        self._patch.start()
        shared_store.set_enabled(True)

    def tearDown(self):
        self._patch.stop()
        shared_store.set_enabled(False)


# ─── The store itself ──────────────────────────────────────────────────────────

class TestSharedStore(SharedStoreCase):
    async def test_round_trip(self):
        await shared_store.set("ns", "k", {"a": [1, 2, 3]}, 60)
        self.assertEqual(await shared_store.get("ns", "k"), {"a": [1, 2, 3]})

    async def test_expired_entry_reads_as_a_miss(self):
        """Mongo's TTL sweep runs about once a minute, so an expired document is
        still readable. Trusting the sweep would serve stale results."""
        await shared_store.set("ns", "k", {"a": 1}, 60)
        for doc in self.db["cache_entries"].docs.values():
            doc["exp"] = doc["exp"].replace(year=2000)
        self.assertIsNone(await shared_store.get("ns", "k"))

    async def test_get_many_is_one_round_trip(self):
        await shared_store.set_many("ns", {"a": 1, "b": 2, "c": 3}, 60)
        got = await shared_store.get_many("ns", ["a", "c", "missing"])
        self.assertEqual(got, {"a": 1, "c": 3})

    async def test_keys_that_exceed_the_id_ceiling_still_work(self):
        long_key = "q" * 900
        await shared_store.set("ns", long_key, "value", 60)
        self.assertEqual(await shared_store.get("ns", long_key), "value")
        stored_id = next(iter(self.db["cache_entries"].docs))
        self.assertLess(len(stored_id), 100)

    async def test_paper_dict_with_a_dotted_key_survives(self):
        """Values are JSON, not raw BSON: Mongo rejects a field name with a '.'
        in it, and paper metadata is not ours to sanitise."""
        value = {"papers": [{"doi.original": "10.1/x", "title": "T"}]}
        await shared_store.set("ns", "k", value, 60)
        self.assertEqual(await shared_store.get("ns", "k"), value)

    async def test_oversized_value_is_dropped_not_raised(self):
        huge = {"papers": ["x" * 1024 for _ in range(6000)]}
        await shared_store.set("ns", "big", huge, 60)
        self.assertIsNone(await shared_store.get("ns", "big"))

    async def test_scan_tag_returns_the_bucket(self):
        await shared_store.set("ns", "k1", {"n": 1}, 60, tag="bucket")
        await shared_store.set("ns", "k2", {"n": 2}, 60, tag="bucket")
        await shared_store.set("ns", "k3", {"n": 3}, 60, tag="other")
        values = await shared_store.scan_tag("ns", "bucket")
        self.assertEqual(sorted(v["n"] for v in values), [1, 2])

    async def test_disabled_store_is_a_silent_miss(self):
        await shared_store.set("ns", "k", "v", 60)
        shared_store.set_enabled(False)
        self.assertIsNone(await shared_store.get("ns", "k"))
        await shared_store.set("ns", "k2", "v", 60)  # must not raise

    async def test_repeated_failures_trip_the_breaker(self):
        """A cache read must never cost more than the work it skips. After the
        threshold the store is skipped outright rather than timing out again."""
        class _Broken:
            async def find_one(self, *a, **k):
                raise RuntimeError("mongo down")

        with patch.object(shared_store, "_collection", lambda name: _Broken()):
            for _ in range(shared_store._FAILURE_THRESHOLD):
                self.assertIsNone(await shared_store.get("ns", "k"))
            self.assertFalse(shared_store.enabled())
        shared_store.set_enabled(True)
        self.assertTrue(shared_store.enabled())

    async def test_embeddings_are_stored_as_vectors_not_text(self):
        key = shared_store.embedding_key("doi:10.1/x", "model-1", "RETRIEVAL_DOCUMENT")
        await shared_store.put_embeddings({key: [0.1, 0.2, 0.3]})
        self.assertEqual(await shared_store.get_embeddings([key]), {key: [0.1, 0.2, 0.3]})
        self.assertIsInstance(self.db["paper_embeddings"].docs[key]["e"], list)

    async def test_embedding_key_separates_models(self):
        a = shared_store.embedding_key("doi:10.1/x", "model-1", "RETRIEVAL_DOCUMENT")
        b = shared_store.embedding_key("doi:10.1/x", "model-2", "RETRIEVAL_DOCUMENT")
        await shared_store.put_embeddings({a: [1.0]})
        self.assertEqual(await shared_store.get_embeddings([b]), {})


# ─── 1.3: embeddings by paper identity ─────────────────────────────────────────

class TestEmbeddingPersistence(SharedStoreCase):
    def setUp(self):
        super().setUp()
        import integrations.paper_search as ps
        self.ps = ps
        ps._embedding_cache.clear()

    async def test_second_worker_does_not_re_embed_the_same_paper(self):
        papers = [{"title": "Attention is all you need", "abstract": "A", "doi": "10.1/att"}]
        batch = AsyncMock(return_value=[[0.5, 0.5]])
        with patch("ai.llm_provider.get_embeddings_batch", batch):
            first = await self.ps._embed_papers_cached(papers)
        self.assertEqual(first, [[0.5, 0.5]])
        self.assertEqual(batch.await_count, 1)

        # A restart clears the in-process cache but not the store.
        self.ps._embedding_cache.clear()
        with patch("ai.llm_provider.get_embeddings_batch", batch):
            second = await self.ps._embed_papers_cached(papers)
        self.assertEqual(second, [[0.5, 0.5]])
        self.assertEqual(batch.await_count, 1, "paper was embedded a second time")

    async def test_identity_not_abstract_is_the_key(self):
        """The published record and the preprint carry different abstracts and
        the same DOI. That is one paper, and one embedding."""
        preprint = {"title": "Attention", "abstract": "preprint text", "doi": "10.1/att"}
        published = {"title": "Attention", "abstract": "camera ready text", "doi": "10.1/att"}
        batch = AsyncMock(return_value=[[0.5, 0.5]])
        with patch("ai.llm_provider.get_embeddings_batch", batch):
            await self.ps._embed_papers_cached([preprint])
            self.ps._embedding_cache.clear()
            got = await self.ps._embed_papers_cached([published])
        self.assertEqual(got, [[0.5, 0.5]])
        self.assertEqual(batch.await_count, 1)

    async def test_a_failed_embedding_is_never_persisted(self):
        """A provider outage must not write 'this paper has no embedding' into a
        store that has no expiry."""
        papers = [{"title": "Down", "abstract": "A", "doi": "10.1/down"}]
        with patch("ai.llm_provider.get_embeddings_batch", AsyncMock(return_value=[None])):
            self.assertEqual(await self.ps._embed_papers_cached(papers), [None])
        self.assertEqual(self.db["paper_embeddings"].docs, {})

    async def test_query_embedding_is_reused_across_a_restart(self):
        embed = AsyncMock(return_value=[0.1, 0.9])
        with patch("ai.llm_provider.get_embedding", embed):
            first = await self.ps._query_embedding("Machine Learning")
            self.ps._embedding_cache.clear()
            # Canonically the same query — different case and spacing.
            second = await self.ps._query_embedding("machine  learning")
        self.assertEqual(first, [0.1, 0.9])
        self.assertEqual(second, [0.1, 0.9])
        self.assertEqual(embed.await_count, 1)


# ─── 1.2: search memory survives a restart ─────────────────────────────────────

class TestSearchMemoryPersistence(SharedStoreCase):
    def setUp(self):
        super().setUp()
        import integrations.paper_search as ps
        from services import semantic_cache
        self.ps = ps
        self.semantic_cache = semantic_cache
        ps._cache.clear()
        ps._embedding_cache.clear()
        ps._inflight.clear()
        semantic_cache.clear()

    def _papers(self, n=3):
        return [{"title": f"Paper {i}", "abstract": "a", "doi": f"10.1/{i}",
                 "source": "arXiv", "year": "2024", "citations": n - i} for i in range(n)]

    def _stub_every_source(self, arxiv_papers):
        """Patch the nine fan-out integrations. The real _execute_search then
        runs — which is the point: the durable write lives next to the
        in-memory one, and a test that stubs _execute_search would not see it."""
        empty = AsyncMock(return_value=[])
        patches = [
            patch.object(self.ps, name, empty)
            for name in ("s2_search", "openalex_search", "crossref_search", "pubmed_search",
                         "springer_search", "europepmc_search", "doaj_search")
        ]
        patches.append(patch.object(self.ps, "arxiv_search", AsyncMock(return_value=arxiv_papers)))
        patches.append(patch.object(self.ps, "search_github_knowledge", lambda *a, **k: []))
        return patches

    async def test_fan_out_is_not_repeated_after_a_restart(self):
        papers = self._papers()
        patches = self._stub_every_source(papers)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        with patch("integrations.unpaywall.enrich_papers_with_oa", AsyncMock(side_effect=lambda x: x)):
            got, meta = await self.ps.search_all_with_meta(
                "graph neural networks", semantic_rerank=False
            )
        self.assertEqual(meta.cache, "miss")
        self.assertEqual(len(got), 3)

        # Simulate the redeploy: every in-process cache is gone.
        self.ps._cache.clear()
        self.ps._inflight.clear()
        self.semantic_cache.clear()

        with patch.object(self.ps, "_execute_search", AsyncMock()) as executed:
            got, meta = await self.ps.search_all_with_meta(
                "graph neural networks", semantic_rerank=False
            )

        executed.assert_not_called()
        self.assertEqual(meta.cache, "exact")
        self.assertEqual(len(got), 3)
        # Per-database yield survives too, as records rather than raw dicts.
        names = [s["name"] for s in meta.sources_as_dicts()]
        self.assertIn("arXiv", names)

    async def test_semantic_bucket_is_rehydrated_after_a_restart(self):
        stored = self._papers(4)
        await self.semantic_cache.store_shared(
            "15_True", "stored-key", [1.0, 0.0], "machine learning in healthcare", stored,
            sources=(self.ps.SourceOutcome(name="PubMed", status="ok", count=4),),
        )
        self.semantic_cache.clear()  # the restart

        hit = await self.semantic_cache.lookup_shared("15_True", "other-key", [1.0, 0.0])
        self.assertIsNotNone(hit)
        self.assertEqual(hit.matched_query, "machine learning in healthcare")
        self.assertEqual(len(hit.papers), 4)
        self.assertEqual(hit.sources[0].name, "PubMed")

    async def test_hydration_is_not_repeated_per_lookup(self):
        await self.semantic_cache.store_shared("b", "k", [1.0, 0.0], "q", self._papers(1))
        self.semantic_cache.clear()
        scans = 0
        real_scan = shared_store.scan_tag

        async def counting_scan(*args, **kwargs):
            nonlocal scans
            scans += 1
            return await real_scan(*args, **kwargs)

        with patch.object(shared_store, "scan_tag", counting_scan):
            for _ in range(5):
                await self.semantic_cache.lookup_shared("b", "other", [0.0, 1.0])
        self.assertEqual(scans, 1)


# ─── 1.2: relevance verdicts ───────────────────────────────────────────────────

class TestRelevancePersistence(SharedStoreCase):
    def setUp(self):
        super().setUp()
        from ai import relevance
        self.relevance = relevance
        relevance._relevance_cache.clear()
        relevance._failure_cache.clear()

    async def test_verdicts_are_not_re_billed_after_a_restart(self):
        papers = [
            {"title": "Relevant work", "abstract": "yes", "doi": "10.1/a"},
            {"title": "Off topic", "abstract": "no", "doi": "10.1/b"},
        ]
        classify = AsyncMock(return_value=[True, False])
        with patch.object(self.relevance, "_classify_batch", classify):
            kept = await self.relevance._filter_relevant_papers("topic", papers)
        self.assertEqual([p["doi"] for p in kept], ["10.1/a"])
        self.assertEqual(classify.await_count, 1)

        self.relevance._relevance_cache.clear()  # the restart
        with patch.object(self.relevance, "_classify_batch", classify):
            kept = await self.relevance._filter_relevant_papers("Topic", papers)
        self.assertEqual([p["doi"] for p in kept], ["10.1/a"])
        self.assertEqual(classify.await_count, 1, "verdicts were re-classified")


if __name__ == "__main__":
    unittest.main()
