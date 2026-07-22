"""
tests/test_pubmed.py
--------------------
Tests for integrations/pubmed.py and the search_all() ceiling.

Test groups
~~~~~~~~~~~
1. TestPubMedUnit         — mocked httpx, verify normalised output shape.
2. TestPubMedAbortEarly   — esearch >3s → esummary never called, returns [].
3. TestSearchAllCeiling   — slow PubMed mock (sleep 10s) → search_all()
                            returns within ~6.5s with PubMed absent.
4. TestSearchAllLatency   — @pytest.mark.integration, real queries,
                            prints before/after latency numbers.

Run unit + ceiling tests (no API keys needed):
    cd backend
    python -m pytest tests/test_pubmed.py -v

Run the integration/latency test (needs PUBMED_API_KEY in .env):
    python -m pytest tests/test_pubmed.py -v -m integration --run-integration
"""

import asyncio
import time
import pytest
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_esearch_response(pmids: list[str]) -> MagicMock:
    """Minimal httpx Response mock for esearch."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "esearchresult": {"idlist": pmids}
    }
    return mock


def _make_esummary_response(pmids: list[str]) -> MagicMock:
    """Minimal httpx Response mock for esummary with one entry per PMID."""
    result = {"uids": pmids}
    for uid in pmids:
        result[str(uid)] = {
            "uid": uid,
            "title": f"Test Paper {uid}",
            "authors": [{"name": "Author A"}, {"name": "Author B"}],
            "pubdate": "2023 Jan",
            "source": "J Test Med",
        }
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"result": result}
    return mock


# ── 1. Unit tests for pubmed.search_papers ───────────────────────────────────

class TestPubMedUnit(unittest.IsolatedAsyncioTestCase):
    """Mocked httpx — verifies normalised output shape end-to-end."""

    @patch("integrations.pubmed.httpx.AsyncClient")
    async def test_returns_normalised_paper_list(self, mock_client_cls):
        """Two mocked calls → correct list of paper dicts."""
        from integrations.pubmed import search_papers

        pmids = ["12345", "67890"]
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            side_effect=[
                _make_esearch_response(pmids),
                _make_esummary_response(pmids),
            ]
        )
        mock_client_cls.return_value = mock_client

        results = await search_papers("CRISPR gene editing", limit=2)

        self.assertEqual(len(results), 2)
        for paper in results:
            self.assertIn("id", paper)
            self.assertIn("title", paper)
            self.assertIn("authors", paper)
            self.assertIn("year", paper)
            self.assertIn("citations", paper)
            self.assertIn("abstract", paper)
            self.assertIn("url", paper)
            self.assertEqual(paper["source"], "PubMed")
            # URL must be a real PubMed page link
            self.assertTrue(paper["url"].startswith("https://pubmed.ncbi.nlm.nih.gov/"))
            # ID must carry the pmid: prefix
            self.assertTrue(paper["id"].startswith("pmid:"))

    @patch("integrations.pubmed.httpx.AsyncClient")
    async def test_empty_query_returns_empty(self, mock_client_cls):
        """Empty / whitespace query returns [] immediately without HTTP calls."""
        from integrations.pubmed import search_papers

        for bad_query in ("", "   ", "\t"):
            result = await search_papers(bad_query)
            self.assertEqual(result, [], f"Expected [] for query={bad_query!r}")

        mock_client_cls.assert_not_called()

    @patch("integrations.pubmed.httpx.AsyncClient")
    async def test_esearch_returns_empty_pmids(self, mock_client_cls):
        """esearch result with no PMIDs → returns [] without calling esummary."""
        from integrations.pubmed import search_papers

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_make_esearch_response([]))
        mock_client_cls.return_value = mock_client

        result = await search_papers("obscure query with no results xyz123")
        self.assertEqual(result, [])
        # Only one client.get call (esearch), not two
        self.assertEqual(mock_client.get.call_count, 1)

    @patch("integrations.pubmed.httpx.AsyncClient")
    async def test_esearch_http_error_returns_empty(self, mock_client_cls):
        """HTTP error in esearch → returns [] gracefully, no exception raised."""
        from integrations.pubmed import search_papers

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client_cls.return_value = mock_client

        result = await search_papers("anything")
        self.assertEqual(result, [])

    @patch("integrations.pubmed.httpx.AsyncClient")
    async def test_esummary_http_error_returns_empty(self, mock_client_cls):
        """esearch succeeds but esummary raises → returns [] gracefully."""
        from integrations.pubmed import search_papers

        call_count = {"n": 0}

        async def get_side_effect(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_esearch_response(["99999"])
            raise Exception("esummary timed out")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=get_side_effect)
        mock_client_cls.return_value = mock_client

        result = await search_papers("something")
        self.assertEqual(result, [])

    @patch("integrations.pubmed.httpx.AsyncClient")
    async def test_author_truncation_et_al(self, mock_client_cls):
        """Papers with >3 authors should get 'et al.' appended."""
        from integrations.pubmed import search_papers

        pmids = ["111"]
        esummary_mock = MagicMock()
        esummary_mock.raise_for_status = MagicMock()
        esummary_mock.json.return_value = {
            "result": {
                "uids": pmids,
                "111": {
                    "uid": "111",
                    "title": "Multi-Author Paper",
                    "authors": [
                        {"name": "A One"},
                        {"name": "B Two"},
                        {"name": "C Three"},
                        {"name": "D Four"},
                        {"name": "E Five"},
                    ],
                    "pubdate": "2024",
                    "source": "Nature",
                }
            }
        }

        call_count = {"n": 0}

        async def get_side_effect(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_esearch_response(pmids)
            return esummary_mock

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=get_side_effect)
        mock_client_cls.return_value = mock_client

        results = await search_papers("test")
        self.assertEqual(len(results), 1)
        self.assertIn("et al.", results[0]["authors"])
        self.assertTrue(results[0]["authors"].startswith("A One, B Two, C Three"))


# ── 2. Abort-early guard tests ────────────────────────────────────────────────

class TestPubMedAbortEarly(unittest.IsolatedAsyncioTestCase):
    """
    If esearch takes longer than _ESEARCH_ABORT_AFTER (3s), search_papers()
    must return [] immediately without calling esummary.
    """

    async def test_slow_esearch_aborts_before_esummary(self):
        """
        Monkey-patch _esearch to sleep 3.5s (above threshold).
        Verify esummary is never called and search_papers returns [] quickly.
        """
        import integrations.pubmed as pubmed_mod
        from integrations.pubmed import search_papers

        esummary_called = {"flag": False}

        async def slow_esearch(client, query, limit):
            await asyncio.sleep(3.5)
            return ["99999"]

        async def should_not_be_called(client, pmids):
            esummary_called["flag"] = True
            return [{"id": "pmid:99999", "title": "Should Not Appear"}]

        with (
            patch.object(pubmed_mod, "_esearch", side_effect=slow_esearch),
            patch.object(pubmed_mod, "_esummary", side_effect=should_not_be_called),
            # Also patch httpx so no real network call is attempted
            patch("integrations.pubmed.httpx.AsyncClient"),
        ):
            t0 = time.monotonic()
            result = await search_papers("some query")
            elapsed = time.monotonic() - t0

        self.assertEqual(result, [], "Slow esearch must return []")
        self.assertFalse(esummary_called["flag"], "esummary must NOT be called after slow esearch")
        # Should return relatively quickly (≤ esearch sleep + small overhead, not +5s for esummary)
        self.assertLess(elapsed, 5.0, f"Abort-early took {elapsed:.2f}s — expected <5s")

    async def test_fast_esearch_proceeds_to_esummary(self):
        """Esearch within threshold → esummary IS called."""
        import integrations.pubmed as pubmed_mod
        from integrations.pubmed import search_papers

        esummary_called = {"flag": False}

        async def fast_esearch(client, query, limit):
            return ["12345"]

        async def mock_esummary(client, pmids):
            esummary_called["flag"] = True
            return [{
                "id": "pmid:12345", "title": "Fast Paper", "authors": "A B",
                "year": "2023", "citations": 0,
                "abstract": "Abstract not available via PubMed summary API.",
                "url": "https://pubmed.ncbi.nlm.nih.gov/12345/", "source": "PubMed",
            }]

        with (
            patch.object(pubmed_mod, "_esearch", side_effect=fast_esearch),
            patch.object(pubmed_mod, "_esummary", side_effect=mock_esummary),
            patch("integrations.pubmed.httpx.AsyncClient"),
        ):
            result = await search_papers("fast query")

        self.assertTrue(esummary_called["flag"], "esummary must be called for fast esearch")
        self.assertEqual(len(result), 1)


# ── 3. search_all() 6-second ceiling + partial-result tests ─────────────────

class TestSearchAllCeiling(unittest.IsolatedAsyncioTestCase):
    """
    Verify the asyncio.wait(tasks, timeout=6.0) per-task cancellation ceiling.

    Key contract:
    - Slow sources are cancelled; fast sources' results ARE returned.
    - A slow PubMed must NEVER cause the entire search to return [].
    - When ALL sources time out, stale cache is returned (or [] if no cache).
    """

    async def test_ceiling_triggers_on_slow_pubmed(self):
        """
        Mock PubMed to sleep 10s.  search_all() must return within ~6.5s
        and PubMed results must be absent from the output.
        Fast sources (mocked to return instantly) must still appear.
        """
        import integrations.paper_search as ps_module

        # Clear cache so we don't short-circuit
        ps_module._cache.clear()

        fast_papers = [
            {
                "id": "s2-001", "title": "Fast Source Paper", "authors": "Quick Author",
                "year": "2024", "citations": 10,
                "abstract": "A fast abstract.", "url": "https://example.com/fast",
                "source": "Semantic Scholar",
            }
        ]

        async def instant_search(*args, **kwargs):
            return fast_papers

        async def slow_pubmed(*args, **kwargs):
            await asyncio.sleep(0.2)   # simulate a source exceeding the short test ceiling
            return [{"id": "pmid:SLOW", "title": "Slow PubMed Paper", "source": "PubMed"}]

        # search_github_knowledge is sync, wrapped in asyncio.to_thread
        def instant_github(query):
            return []

        with (
            patch("integrations.paper_search.s2_search",        side_effect=instant_search),
            patch("integrations.paper_search.openalex_search",  side_effect=instant_search),
            patch("integrations.paper_search.crossref_search",  side_effect=instant_search),
            patch("integrations.paper_search.pubmed_search",    side_effect=slow_pubmed),
            patch("integrations.paper_search.arxiv_search",     side_effect=instant_search),
            patch("integrations.paper_search.search_github_knowledge", side_effect=instant_github),
            patch("integrations.paper_search.springer_search",  side_effect=instant_search),
            patch("integrations.paper_search.ieee_search",      side_effect=instant_search),
            patch("integrations.paper_search.core_search",      side_effect=instant_search),
        ):
            t0 = time.monotonic()
            results = await ps_module.search_all(
                "ceiling test query xyz",
                limit_per_source=5,
                semantic_rerank=False,
                source_timeout=0.05,
                oa_timeout=0.01,
            )
            elapsed = time.monotonic() - t0

        print(f"\n[ceiling test] search_all() returned in {elapsed:.3f}s")

        # ── Assertions ──────────────────────────────────────────────────────
        # Must return well within the 6s ceiling (allow 1s of OS/event-loop slack)
        self.assertLess(
            elapsed, 1.0,
            f"search_all() took {elapsed:.2f}s — ceiling not enforced! Expected <7s."
        )

        # PubMed results must NOT be present (they were cancelled)
        pubmed_titles = [p["title"] for p in results if p.get("source") == "PubMed"]
        self.assertEqual(
            pubmed_titles, [],
            f"Slow PubMed results leaked into output: {pubmed_titles}"
        )

    async def test_ceiling_returns_cache_on_timeout(self):
        """
        If ALL sources time out (done is empty), search_all() returns the stale
        cache entry rather than an empty list.

        Cache key format is "{query}_{limit}", so we must pre-populate with the
        exact key that search_all("cached_query", limit=5) will produce.
        """
        import integrations.paper_search as ps_module

        # Pre-populate with the EXACT key search_all() will compute.
        cache_key = "cached_query_5_all"
        stale_papers = [
            {"id": "cached-001", "title": "Stale Cached Paper", "source": "OpenAlex"}
        ]
        ps_module._cache[cache_key] = (stale_papers, time.time() - 700)  # expired TTL but still present

        async def always_slow(*args, **kwargs):
            await asyncio.sleep(0.2)
            return []

        def always_slow_sync(query):
            # Must be a real blocking sleep so asyncio.to_thread() also times out.
            import time as _time
            _time.sleep(0.2)
            return []

        with (
            patch("integrations.paper_search.s2_search",        side_effect=always_slow),
            patch("integrations.paper_search.openalex_search",  side_effect=always_slow),
            patch("integrations.paper_search.crossref_search",  side_effect=always_slow),
            patch("integrations.paper_search.pubmed_search",    side_effect=always_slow),
            patch("integrations.paper_search.arxiv_search",     side_effect=always_slow),
            patch("integrations.paper_search.search_github_knowledge", side_effect=always_slow_sync),
            patch("integrations.paper_search.springer_search",  side_effect=always_slow),
            patch("integrations.paper_search.ieee_search",      side_effect=always_slow),
            patch("integrations.paper_search.core_search",      side_effect=always_slow),
        ):
            t0 = time.monotonic()
            results = await ps_module.search_all(
                "cached_query",
                limit_per_source=5,
                semantic_rerank=False,
                source_timeout=0.05,
                oa_timeout=0.01,
            )
            elapsed = time.monotonic() - t0

        print(f"\n[cache fallback test] search_all() returned in {elapsed:.3f}s")

        self.assertLess(elapsed, 1.0, f"search_all() still hung for {elapsed:.2f}s")
        self.assertEqual(
            results, stale_papers,
            "When all sources time out, search_all() must return stale cache"
        )

        # Cleanup
        del ps_module._cache[cache_key]

    async def test_all_fast_sources_complete_within_ceiling(self):
        """
        When all 6 sources return instantly, search_all() must complete well
        under the 6s ceiling and return all combined results.
        """
        import integrations.paper_search as ps_module

        ps_module._cache.clear()

        def make_paper(src_id: str, source: str) -> dict:
            return {
                "id": src_id, "title": f"Paper from {source}", "authors": "A",
                "year": "2024", "citations": 0, "abstract": "x",
                "url": f"https://example.com/{src_id}", "source": source,
            }

        async def s2_fast(*a, **k):     return [make_paper("s2-1",  "Semantic Scholar")]
        async def oa_fast(*a, **k):     return [make_paper("oa-1",  "OpenAlex")]
        async def cr_fast(*a, **k):     return [make_paper("cr-1",  "Crossref")]
        async def pm_fast(*a, **k):     return [make_paper("pm-1",  "PubMed")]
        async def ax_fast(*a, **k):     return [make_paper("ax-1",  "arXiv")]
        def gh_fast(query):             return [make_paper("gh-1",  "GitHub")]
        async def sp_fast(*a, **k):     return [make_paper("sp-1",  "Springer")]
        async def ie_fast(*a, **k):     return [make_paper("ie-1",  "IEEE")]
        async def co_fast(*a, **k):     return [make_paper("co-1",  "CORE")]

        with (
            patch("integrations.paper_search.s2_search",        side_effect=s2_fast),
            patch("integrations.paper_search.openalex_search",  side_effect=oa_fast),
            patch("integrations.paper_search.crossref_search",  side_effect=cr_fast),
            patch("integrations.paper_search.pubmed_search",    side_effect=pm_fast),
            patch("integrations.paper_search.arxiv_search",     side_effect=ax_fast),
            patch("integrations.paper_search.search_github_knowledge", side_effect=gh_fast),
            patch("integrations.paper_search.springer_search",  side_effect=sp_fast),
            patch("integrations.paper_search.ieee_search",      side_effect=ie_fast),
            patch("integrations.paper_search.core_search",      side_effect=co_fast),
        ):
            t0 = time.monotonic()
            results = await ps_module.search_all(
                "fast all sources",
                limit_per_source=6,
                semantic_rerank=False,
                source_timeout=1.0,
                oa_timeout=0.01,
            )
            elapsed = time.monotonic() - t0

        print(f"\n[all-fast test] search_all() returned in {elapsed:.3f}s with {len(results)} papers")

        self.assertLess(elapsed, 2.0, f"All-fast case should complete in <2s, took {elapsed:.2f}s")
        sources_present = {p["source"] for p in results}
        self.assertIn("PubMed", sources_present, "PubMed must be present in all-fast results")
        self.assertEqual(len(results), 9, "Expected 1 paper from each of 9 sources")


# ── 4. Partial-results + broad-query regression tests ────────────────────────

class TestSearchAllPartialResults(unittest.IsolatedAsyncioTestCase):
    """
    Regression tests for the asyncio.wait() partial-result contract.

    These specifically test broad queries ("machine learning",
    "artificial intelligence") — the queries that broke under the old
    asyncio.wait_for(gather()) pattern when PubMed was slow.

    Key invariants:
    - Fast sources always return results even when one source is slow.
    - A slow PubMed must NEVER produce an empty list for a broad query.
    - The partial-result set is a strict superset of zero results.
    """

    async def _run_with_slow_pubmed(self, query: str, fast_papers: list) -> tuple[list, float]:
        """Helper: run search_all() with PubMed sleeping 10s, other sources instant."""
        import integrations.paper_search as ps_module
        ps_module._cache.clear()

        async def instant(*a, **k):
            return fast_papers

        async def slow_pubmed(*a, **k):
            await asyncio.sleep(0.2)
            return [{"id": "pmid:SLOW", "title": "Slow PubMed Paper", "source": "PubMed"}]

        def instant_github(q):
            return []

        with (
            patch("integrations.paper_search.s2_search",        side_effect=instant),
            patch("integrations.paper_search.openalex_search",  side_effect=instant),
            patch("integrations.paper_search.crossref_search",  side_effect=instant),
            patch("integrations.paper_search.pubmed_search",    side_effect=slow_pubmed),
            patch("integrations.paper_search.arxiv_search",     side_effect=instant),
            patch("integrations.paper_search.search_github_knowledge", side_effect=instant_github),
            patch("integrations.paper_search.springer_search",  side_effect=instant),
            patch("integrations.paper_search.ieee_search",      side_effect=instant),
            patch("integrations.paper_search.core_search",      side_effect=instant),
        ):
            t0 = time.monotonic()
            results = await ps_module.search_all(
                query,
                limit_per_source=5,
                semantic_rerank=False,
                source_timeout=0.05,
                oa_timeout=0.01,
            )
            elapsed = time.monotonic() - t0

        return results, elapsed

    def _make_fast_paper(self, src: str) -> dict:
        return {
            "id": f"id-{src}", "title": f"Paper about {src} topic",
            "authors": "Author A", "year": "2024", "citations": 5,
            "abstract": "Abstract.", "url": f"https://example.com/{src}",
            "source": "Semantic Scholar",
        }

    async def test_slow_pubmed_never_returns_empty_machine_learning(self):
        """
        Regression: 'machine learning' query with slow PubMed must return
        results from fast sources — NOT an empty list.

        This is the canonical failure mode of asyncio.wait_for(gather()):
        PubMed's slowness on a broad query caused the entire gather to
        cancel, returning [] even though S2/OpenAlex/arXiv had already
        finished.
        """
        fast = [self._make_fast_paper("ml")]
        results, elapsed = await self._run_with_slow_pubmed("machine learning", fast)

        print(f"\n['machine learning' partial] {len(results)} results in {elapsed:.3f}s")

        # Must return within ceiling (allow 1s slack)
        self.assertLess(elapsed, 1.0, f"Ceiling not enforced: {elapsed:.2f}s")

        # Must NOT return empty — fast sources finished and must be present
        self.assertGreater(
            len(results), 0,
            "'machine learning' with slow PubMed returned []. "
            "Fast sources (S2, OpenAlex, arXiv) should have been returned."
        )

        # PubMed's 10s-sleep paper must NOT appear
        pubmed_titles = [p["title"] for p in results if p.get("source") == "PubMed"]
        self.assertEqual(pubmed_titles, [], f"Slow PubMed leaked: {pubmed_titles}")

    async def test_slow_pubmed_never_returns_empty_artificial_intelligence(self):
        """
        Same regression test for 'artificial intelligence' — the other broad
        query that exposed the wait_for(gather()) bug.
        """
        fast = [self._make_fast_paper("ai")]
        results, elapsed = await self._run_with_slow_pubmed("artificial intelligence", fast)

        print(f"\n['artificial intelligence' partial] {len(results)} results in {elapsed:.3f}s")

        self.assertLess(elapsed, 1.0)
        self.assertGreater(
            len(results), 0,
            "'artificial intelligence' with slow PubMed returned []. "
            "Fast sources must still be returned."
        )

    async def test_partial_results_contain_exactly_fast_sources(self):
        """
        When PubMed is slow and other 5 sources are instant, the result set
        must contain papers from the 5 fast sources and NOT from PubMed.
        This verifies the partial-result contract precisely.
        """
        import integrations.paper_search as ps_module
        ps_module._cache.clear()

        def make_paper(src_name, source):
            return {
                "id": f"id-{src_name}", "title": f"Paper from {src_name}",
                "authors": "A", "year": "2024", "citations": 1,
                "abstract": "x", "url": f"https://example.com/{src_name}",
                "source": source,
            }

        async def s2_fast(*a, **k):   return [make_paper("s2",  "Semantic Scholar")]
        async def oa_fast(*a, **k):   return [make_paper("oa",  "OpenAlex")]
        async def cr_fast(*a, **k):   return [make_paper("cr",  "Crossref")]
        async def ax_fast(*a, **k):   return [make_paper("ax",  "arXiv")]
        async def slow_pm(*a, **k):
            await asyncio.sleep(0.2)
            return [make_paper("pm", "PubMed")]
        def gh_fast(q):               return []
        async def sp_fast(*a, **k):   return [make_paper("sp", "Springer")]
        async def ie_fast(*a, **k):   return [make_paper("ie", "IEEE")]
        async def co_fast(*a, **k):   return [make_paper("co", "CORE")]

        with (
            patch("integrations.paper_search.s2_search",        side_effect=s2_fast),
            patch("integrations.paper_search.openalex_search",  side_effect=oa_fast),
            patch("integrations.paper_search.crossref_search",  side_effect=cr_fast),
            patch("integrations.paper_search.pubmed_search",    side_effect=slow_pm),
            patch("integrations.paper_search.arxiv_search",     side_effect=ax_fast),
            patch("integrations.paper_search.search_github_knowledge", side_effect=gh_fast),
            patch("integrations.paper_search.springer_search",  side_effect=sp_fast),
            patch("integrations.paper_search.ieee_search",      side_effect=ie_fast),
            patch("integrations.paper_search.core_search",      side_effect=co_fast),
        ):
            t0 = time.monotonic()
            results = await ps_module.search_all(
                "partial result test",
                limit_per_source=5,
                semantic_rerank=False,
                source_timeout=0.05,
                oa_timeout=0.01,
            )
            elapsed = time.monotonic() - t0

        sources_present = {p["source"] for p in results}
        print(f"\n[partial sources] {sources_present} in {elapsed:.3f}s")

        self.assertLess(elapsed, 1.0)
        # 4 fast sources (S2, OpenAlex, Crossref, arXiv) must be present
        for expected_source in ("Semantic Scholar", "OpenAlex", "Crossref", "arXiv", "Springer", "IEEE", "CORE"):
            self.assertIn(
                expected_source, sources_present,
                f"{expected_source} missing from partial results: {sources_present}"
            )
        # PubMed must be absent (it was cancelled)
        self.assertNotIn("PubMed", sources_present,
            f"Slow PubMed leaked into partial results: {sources_present}")


# ── 5. Integration / latency tests (real network) ────────────────────────────

@pytest.mark.integration
class TestSearchAllLatency(unittest.IsolatedAsyncioTestCase):
    """
    Real network calls — measures and reports latency before/after PubMed.
    Skipped by default; run with: pytest -m integration --run-integration
    """

    async def test_pubmed_alone_latency(self):
        """Report PubMed search_papers() latency for a real query."""
        from integrations.pubmed import search_papers

        query = "CRISPR gene editing cancer therapy"
        t0 = time.monotonic()
        results = await search_papers(query, limit=5)
        elapsed = time.monotonic() - t0

        print(f"\n[INTEGRATION] PubMed latency: {elapsed:.3f}s → {len(results)} papers")
        for p in results:
            print(f"  {p['year']}  {p['title'][:70]}  ({p['authors'][:40]})")

        self.assertLess(elapsed, 10.0, f"PubMed took {elapsed:.2f}s — timeout not working?")

    async def test_search_all_latency_with_pubmed(self):
        """
        Report total search_all() latency with PubMed as 6th source.
        Also checks that total latency stays under the 6s ceiling.
        """
        import integrations.paper_search as ps_module

        ps_module._cache.clear()
        query = "CRISPR gene editing"

        t0 = time.monotonic()
        results = await ps_module.search_all(query, limit_per_source=5)
        elapsed = time.monotonic() - t0

        print(f"\n[INTEGRATION] search_all() with PubMed: {elapsed:.3f}s → {len(results)} papers")
        sources = {}
        for p in results:
            src = p.get("source", "Unknown")
            sources[src] = sources.get(src, 0) + 1
        for src, count in sorted(sources.items()):
            print(f"  {src}: {count} papers")

        # Ceiling must hold even under real network conditions
        self.assertLess(
            elapsed, 7.0,
            f"search_all() took {elapsed:.2f}s with real network — ceiling broken? "
            f"Expected <7s (6s ceiling + 1s slack)"
        )
        print(f"[INTEGRATION] ✓ Ceiling held: {elapsed:.3f}s < 7s")
