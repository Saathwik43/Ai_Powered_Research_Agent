import contextlib
import os
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import time

from ai import manuscript_generation
from ai.manuscript_generation import generate_section

# Sample papers for mocking search_all + filter
MOCK_PAPERS = [
    {
        "title": "Advances in Topic A Research",
        "authors": "Smith et al.",
        "year": "2024",
        "abstract": "A study on topic A methodology.",
        "url": "https://example.com/1",
        "source": "Semantic Scholar",
    },
    {
        "title": "Topic A: A Comprehensive Review",
        "authors": "Jones et al.",
        "year": "2023",
        "abstract": "Comprehensive review of topic A literature.",
        "url": "https://example.com/2",
        "source": "OpenAlex",
    },
    {
        "title": "Novel Approaches to Topic A",
        "authors": "Lee et al.",
        "year": "2024",
        "abstract": "Novel approaches in topic A domain.",
        "url": "https://example.com/3",
        "source": "arXiv",
    },
]


class TestManuscriptGeneration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Clear research cache before each test.
        manuscript_generation._research_cache.clear()

    @patch('ai.manuscript_generation.check_citation_grounding', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.extract_evidence_for_paper', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.search_all', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._filter_relevant_papers', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.generate_completion', new_callable=AsyncMock)
    async def test_groq_called_first(self, mock_gen, mock_filter, mock_search, mock_extract, mock_citation):
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_citation.return_value = {}
        """Verify generate_section calls generate_completion (which uses Groq first in auto)."""
        mock_search.return_value = MOCK_PAPERS
        mock_filter.return_value = MOCK_PAPERS
        mock_gen.return_value = "Groq Draft"

        result, flags = await generate_section("Topic A", "Introduction", "Context A")

        self.assertEqual(result, "Groq Draft")
        mock_gen.assert_called_once()
        # Verify provider_override is None (not "gemini") for non-lit_review sections
        call_kwargs = mock_gen.call_args
        self.assertIsNone(call_kwargs.kwargs.get('provider_override') or call_kwargs[1].get('provider_override'))

    @patch('ai.manuscript_generation.search_all', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._filter_relevant_papers', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.generate_completion', new_callable=AsyncMock)
    async def test_fallback_when_provider_raises(self, mock_gen, mock_filter, mock_search):
        """When generate_completion raises, we fall back to raising 503 verification_unavailable."""
        from fastapi import HTTPException
        mock_search.return_value = MOCK_PAPERS
        mock_filter.return_value = MOCK_PAPERS
        mock_gen.side_effect = Exception("All providers failed")

        with self.assertRaises(HTTPException) as context:
            await generate_section("Topic B", "Methodology", "Context B")

        self.assertEqual(context.exception.status_code, 503)
        self.assertTrue(context.exception.detail.get("verification_unavailable"))

    @patch('ai.manuscript_generation.check_citation_grounding', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.extract_evidence_for_paper', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.search_all', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._filter_relevant_papers', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.generate_completion', new_callable=AsyncMock)
    async def test_research_cache_hit_skips_search_filter_and_evidence(self, mock_gen, mock_filter, mock_search, mock_extract, mock_citation):
        """Cached research should skip search/filter/evidence while still generating fresh text."""
        cached_papers = [
            {
                **paper,
                "evidence": {"objective": "Cached objective"},
                "evidence_source": "cache",
            }
            for paper in MOCK_PAPERS
        ]
        mock_citation.return_value = {}
        mock_gen.return_value = "Draft from cached research"
        mock_search.return_value = MOCK_PAPERS
        mock_filter.return_value = MOCK_PAPERS

        topic, section, context = "Topic C", "Conclusion", "Context C"
        manuscript_generation._research_cache[topic.lower()] = (cached_papers, time.time())

        result, flags = await generate_section(topic, section, context)

        self.assertEqual(result, "Draft from cached research")
        mock_search.assert_not_called()
        mock_filter.assert_not_called()
        mock_extract.assert_not_called()
        mock_gen.assert_called_once()
        self.assertIn("references", flags)

    @patch('ai.manuscript_generation.check_citation_grounding', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.extract_evidence_for_paper', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.search_all', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._filter_relevant_papers', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.generate_completion', new_callable=AsyncMock)
    async def test_lit_review_uses_gemini_override(self, mock_gen, mock_filter, mock_search, mock_extract, mock_citation):
        """lit_review section should pass provider_override='gemini'."""
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_citation.return_value = {}
        mock_search.return_value = MOCK_PAPERS
        mock_filter.return_value = MOCK_PAPERS
        mock_gen.return_value = "Gemini Lit Review Draft"

        result, flags = await generate_section("Topic D", "lit_review", "Context D")

        self.assertEqual(result, "Gemini Lit Review Draft")
        mock_gen.assert_called_once()
        call_kwargs = mock_gen.call_args
        self.assertEqual(call_kwargs.kwargs.get('provider_override') or call_kwargs[1].get('provider_override'), "gemini")

    @patch('ai.manuscript_generation.check_citation_grounding', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.extract_evidence_for_paper', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.search_all', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._filter_relevant_papers', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.generate_completion', new_callable=AsyncMock)
    async def test_abstract_uses_groq_not_gemini_in_auto(self, mock_gen, mock_filter, mock_search, mock_extract, mock_citation):
        """
        REGRESSION TEST: The 'abstract' section (non-lit_review) must NOT use
        provider_override='gemini'. It should go through the standard auto
        cascade (Groq first), with provider_override=None.
        
        This catches the bug where Gemini was inserted first in the auto cascade,
        making every section try Gemini first.
        """
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_citation.return_value = {}
        mock_search.return_value = MOCK_PAPERS
        mock_filter.return_value = MOCK_PAPERS
        mock_gen.return_value = "Abstract content via Groq"

        result, flags = await generate_section("Topic E", "abstract", "Context E")

        self.assertEqual(result, "Abstract content via Groq")
        mock_gen.assert_called_once()

        # Extract the actual call arguments
        call_args, call_kwargs = mock_gen.call_args

        # provider_override must be None for non-lit_review sections
        actual_override = call_kwargs.get('provider_override')
        self.assertIsNone(
            actual_override,
            f"REGRESSION: 'abstract' section should have provider_override=None, "
            f"got provider_override='{actual_override}'. "
            f"Gemini should NOT be in the auto cascade for non-lit_review sections."
        )

    @patch('ai.manuscript_generation.extract_evidence_for_paper', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.search_all', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._filter_relevant_papers', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.generate_completion', new_callable=AsyncMock)
    async def test_insufficient_papers_skips_ref_list(self, mock_gen, mock_filter, mock_search, mock_extract):
        """When fewer than 2 papers pass relevance filter, skip forced reference list."""
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_search.return_value = MOCK_PAPERS
        # Only 1 paper passes filter — below threshold
        mock_filter.return_value = [MOCK_PAPERS[0]]
        mock_gen.return_value = "Content without forced references"

        result, flags = await generate_section("Niche Topic", "introduction", "")

        self.assertEqual(result, "Content without forced references")
        # references_mapping should be empty since < 2 papers passed
        self.assertNotIn("references", flags)

    @patch('ai.manuscript_generation.check_citation_grounding', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.search_all', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._filter_relevant_papers', new_callable=AsyncMock)
    @patch('ai.manuscript_generation.generate_completion', new_callable=AsyncMock)
    async def test_filter_called_with_papers(self, mock_gen, mock_filter, mock_search, mock_citation):
        """Verify _filter_relevant_papers is called when search returns papers."""
        mock_search.return_value = MOCK_PAPERS
        mock_filter.return_value = MOCK_PAPERS
        mock_gen.return_value = "Draft"
        mock_citation.return_value = {}

        await generate_section("Topic F", "methodology", "Context F")

        mock_filter.assert_called_once_with("Topic F", MOCK_PAPERS)


class TestProviderCascadeOrder(unittest.IsolatedAsyncioTestCase):
    """
    Auto-mode cascade order.

    generate_completion() builds its provider list at call time from the
    environment: OpenAI -> Gemini -> Groq -> Cerebras -> Mistral -> HuggingFace,
    each included only when its key is configured (Groq and HuggingFace are
    always in the list). These tests set that environment explicitly.

    Leaving availability to the ambient environment made the outcome depend on
    which keys the developer happened to have exported, and made a "cascade"
    test capable of issuing a real API call to an unpatched provider. It also
    produced a pair of tests asserting opposite things about Gemini, one of
    which had to fail on every run.
    """

    ONLY_GROQ = {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "CEREBRAS_API_KEY": "", "MISTRAL_API_KEY": ""}
    GEMINI_AND_GROQ = {**ONLY_GROQ, "GEMINI_API_KEY": "test-key"}

    @patch('ai.llm_provider.LLM_PROVIDER', 'auto')
    @patch('ai.llm_provider._generate_groq', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_huggingface', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_gemini', new_callable=AsyncMock)
    async def test_gemini_is_tried_before_groq_when_configured(self, mock_gemini, mock_hf, mock_groq):
        """Quality-first ordering: a configured Gemini short-circuits the rest."""
        from ai.llm_provider import generate_completion
        mock_gemini.return_value = ("Gemini result", 100)

        with patch.dict(os.environ, self.GEMINI_AND_GROQ):
            result = await generate_completion("system", "user", max_tokens=100)

        self.assertEqual(result, "Gemini result")
        mock_gemini.assert_called()
        mock_groq.assert_not_called()
        mock_hf.assert_not_called()

    @patch('ai.llm_provider.LLM_PROVIDER', 'auto')
    @patch('ai.llm_provider._generate_groq', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_huggingface', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_gemini', new_callable=AsyncMock)
    async def test_unconfigured_providers_are_skipped(self, mock_gemini, mock_hf, mock_groq):
        """No GEMINI_API_KEY means Gemini is never in the list at all."""
        from ai.llm_provider import generate_completion
        mock_groq.return_value = ("Groq result", 100)

        with patch.dict(os.environ, self.ONLY_GROQ):
            result = await generate_completion("system", "user", max_tokens=100)

        self.assertEqual(result, "Groq result")
        mock_groq.assert_called()
        mock_gemini.assert_not_called()

    @patch('ai.llm_provider.LLM_PROVIDER', 'auto')
    @patch('ai.llm_provider._generate_groq', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_huggingface', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_gemini', new_callable=AsyncMock)
    async def test_failure_falls_through_to_the_next_provider(self, mock_gemini, mock_hf, mock_groq):
        from ai.llm_provider import generate_completion
        mock_gemini.side_effect = RuntimeError("Gemini is down")
        mock_groq.return_value = ("Groq result", 100)

        with patch.dict(os.environ, self.GEMINI_AND_GROQ):
            result = await generate_completion("system", "user", max_tokens=100)

        self.assertEqual(result, "Groq result")
        mock_gemini.assert_called()
        mock_groq.assert_called()

    @patch('ai.llm_provider.LLM_PROVIDER', 'auto')
    @patch('ai.llm_provider._generate_groq', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_huggingface', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_gemini', new_callable=AsyncMock)
    async def test_all_providers_failing_raises(self, mock_gemini, mock_hf, mock_groq):
        from ai.llm_provider import generate_completion
        mock_gemini.side_effect = RuntimeError("down")
        mock_groq.side_effect = RuntimeError("down")
        mock_hf.side_effect = RuntimeError("down")

        with patch.dict(os.environ, self.GEMINI_AND_GROQ):
            with self.assertRaises(RuntimeError):
                await generate_completion("system", "user", max_tokens=100)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, length):
        return self._docs


class _FakeSources:
    """Stands in for db['sources'], returning per-user uploads."""

    def __init__(self, by_user):
        self._by_user = by_user

    def find(self, query, *args, **kwargs):
        return _FakeCursor(self._by_user.get(query.get("user_id"), []))


class TestResearchCacheIsolation(unittest.IsolatedAsyncioTestCase):
    """
    Regression: _prepare_generation used to append the caller's private uploaded
    sources directly onto the list object stored in _research_cache. Because that
    cache is keyed on topic alone and shared process-wide, this caused:

      1. user sources duplicated once per section generated on the topic,
      2. [N] markers shifting between sections, so the compiled References list
         no longer matched the citations in previously generated sections,
      3. one user's private document text appearing in the next user's prompt
         and reference list.

    The cached list must stay pure public literature; per-user material may only
    exist on a copy.
    """

    TOPIC = "convolutional neural networks"

    LITERATURE = [
        {"title": "paper0", "abstract": "abs0", "evidence": {"results": "r0"}},
        {"title": "paper1", "abstract": "abs1", "evidence": {"results": "r1"}},
        {"title": "paper2", "abstract": "abs2", "evidence": {"results": "r2"}},
    ]

    USER_SOURCES = {
        "userA": [{"filename": "A_private.pdf", "raw_text": "A confidential results"}],
        "userB": [{"filename": "B_private.pdf", "raw_text": "B confidential results"}],
    }

    def setUp(self):
        manuscript_generation._research_cache.clear()

    async def _prepare_as(self, user_id, section):
        from services import usage_tracker
        token = usage_tracker.current_user_id.set(user_id)
        try:
            result = await manuscript_generation._prepare_generation(
                self.TOPIC, section, "", "ieee"
            )
        finally:
            usage_tracker.current_user_id.reset(token)
        references_mapping, papers, err = result[2], result[4], result[5]
        self.assertIsNone(err, "topic guardrail unexpectedly rejected the test topic")
        return [p["title"] for p in papers], references_mapping

    def _pipeline(self):
        """Enter every upstream patch this class needs; returns an ExitStack."""
        stack = contextlib.ExitStack()
        for patcher in (
            patch.object(manuscript_generation, "search_all",
                         new=AsyncMock(return_value=[dict(p) for p in self.LITERATURE])),
            patch.object(manuscript_generation, "_filter_relevant_papers",
                         new=AsyncMock(side_effect=lambda topic, papers: papers)),
            patch.object(manuscript_generation, "extract_evidence_for_paper",
                         new=AsyncMock(return_value=({"results": "r"}, "llm-fallback"))),
            patch.object(manuscript_generation, "db",
                         {"sources": _FakeSources(self.USER_SOURCES)}),
        ):
            stack.enter_context(patcher)
        return stack

    async def test_user_sources_are_not_duplicated_across_sections(self):
        with self._pipeline():
            expected = ["paper0", "paper1", "paper2", "A_private.pdf"]
            for section in ("abstract", "methodology", "results"):
                titles, _ = await self._prepare_as("userA", section)
                self.assertEqual(titles, expected, f"section {section!r} drifted")

    async def test_citation_numbers_are_stable_across_sections(self):
        with self._pipeline():
            _, first = await self._prepare_as("userA", "abstract")
            baseline = {k: v["title"] for k, v in first.items()}
            self.assertEqual(baseline["4"], "A_private.pdf")

            for section in ("methodology", "results"):
                _, mapping = await self._prepare_as("userA", section)
                self.assertEqual(
                    {k: v["title"] for k, v in mapping.items()}, baseline,
                    f"[N] markers changed meaning in section {section!r}",
                )

    async def test_one_users_upload_never_reaches_another_user(self):
        with self._pipeline():
            await self._prepare_as("userA", "abstract")
            titles, _ = await self._prepare_as("userB", "abstract")

            self.assertNotIn("A_private.pdf", titles,
                             "user A's private upload leaked into user B's references")
            self.assertEqual(titles, ["paper0", "paper1", "paper2", "B_private.pdf"])

    async def test_cached_entry_holds_only_public_literature(self):
        with self._pipeline():
            await self._prepare_as("userA", "abstract")
            await self._prepare_as("userA", "methodology")

            cached, _fetched_at = manuscript_generation._research_cache[self.TOPIC]
            self.assertEqual([p["title"] for p in cached], ["paper0", "paper1", "paper2"])

    async def test_empty_search_is_negatively_cached(self):
        """An empty result must not re-run the whole pipeline on every retry."""
        search = AsyncMock(return_value=[])
        with patch.object(manuscript_generation, "search_all", new=search), \
             patch.object(manuscript_generation, "_filter_relevant_papers",
                          new=AsyncMock(side_effect=lambda topic, papers: papers)), \
             patch.object(manuscript_generation, "db",
                          {"sources": _FakeSources({})}):
            await self._prepare_as("userA", "abstract")
            await self._prepare_as("userA", "methodology")

        self.assertEqual(search.await_count, 1, "empty search result was not cached")


class TestGeminiCachePlan(unittest.IsolatedAsyncioTestCase):
    """
    Regression: the Gemini cache key was md5(f"{topic}:{provider}:{model}") --
    it did not include the context it was caching, while the cached path
    deliberately omitted context from the user prompt. So whichever section ran
    first froze the context for every later section on that topic: if lit_review
    ran first its gap-analysis block leaked into the Abstract, and if a section
    with fewer than two papers ran first, later sections silently lost their
    reference list while still being told to cite [N] markers.
    """

    TOPIC = "convolutional neural networks"

    def _key(self, context, model=None, topic=None):
        return manuscript_generation._gemini_cache_plan(
            topic or self.TOPIC, context, "sys", model
        )["cache_key"]

    def test_key_changes_with_context(self):
        self.assertNotEqual(self._key("reference list A"), self._key("reference list B"))

    def test_key_is_stable_for_identical_context(self):
        self.assertEqual(self._key("reference list A"), self._key("reference list A"))

    def test_key_changes_with_model_and_topic(self):
        base = self._key("ctx")
        self.assertNotEqual(base, self._key("ctx", model="gemini-pro-latest"))
        self.assertNotEqual(base, self._key("ctx", topic="graph neural networks"))

    def test_plan_wraps_context_in_delimiters(self):
        plan = manuscript_generation._gemini_cache_plan(self.TOPIC, "REFS", "sys", None)
        self.assertIn("<context>", plan["shared_context"])
        self.assertIn("REFS", plan["shared_context"])
        self.assertEqual(plan["system_instruction"], "sys")

    async def test_no_context_means_no_plan(self):
        """Nothing to cache when there is no literature context."""
        manuscript_generation._research_cache.clear()
        with patch.object(manuscript_generation, "search_all", new=AsyncMock(return_value=[])),              patch.object(manuscript_generation, "_filter_relevant_papers",
                          new=AsyncMock(side_effect=lambda t, p: p)),              patch.object(manuscript_generation, "db", {"sources": _FakeSources({})}):
            prep = await manuscript_generation._prepare_generation(
                self.TOPIC, "abstract", "", "ieee"
            )
        self.assertIsNone(prep.cache_plan)
        self.assertIsNone(prep.cached_content)

    async def test_prompt_keeps_context_when_nothing_is_cached(self):
        """Dropping context without a cache to hold it would lose the references."""
        manuscript_generation._research_cache.clear()
        with patch.object(manuscript_generation, "search_all", new=AsyncMock(return_value=[])),              patch.object(manuscript_generation, "_filter_relevant_papers",
                          new=AsyncMock(side_effect=lambda t, p: p)),              patch.object(manuscript_generation, "db", {"sources": _FakeSources({})}):
            prep = await manuscript_generation._prepare_generation(
                self.TOPIC, "abstract", "UNIQUE-CONTEXT-MARKER", "ieee"
            )
        self.assertIsNone(prep.cached_content)
        self.assertIn("UNIQUE-CONTEXT-MARKER", prep.user_prompt)
        self.assertNotIn("UNIQUE-CONTEXT-MARKER", prep.user_prompt_cached)


class _FakeRefCollection:
    """Minimal stand-in for db['manuscript_references']."""

    def __init__(self):
        self.docs = {}

    def find(self, query, *args, **kwargs):
        return _FakeCursor([])

    async def find_one(self, query, *args, **kwargs):
        return self.docs.get((query.get("user_id"), query.get("topic")))

    async def update_one(self, query, update, upsert=False):
        self.docs[(query["user_id"], query["topic"])] = update["$set"]


class TestEditSectionGrounding(unittest.IsolatedAsyncioTestCase):
    """
    Regression: edit_section called _prepare_generation purely to obtain
    references_mapping, discarding the other six return values including the
    prompt it had just built. On a cold research cache that meant search_all
    across 11 sources, two batched classifier calls, up to 15 evidence
    extractions and -- for lit_review -- a full analyze_gaps run, all to answer
    "make paragraph two shorter".
    """

    TOPIC = "convolutional neural networks"
    LONG_ABSTRACT = "A long abstract sentence describing the study. " * 40

    def setUp(self):
        manuscript_generation._research_cache.clear()
        self.refs = _FakeRefCollection()
        self.counters = {"search": 0, "filter": 0, "evidence": 0}

    def _pipeline_spies(self):
        async def search(*a, **k):
            self.counters["search"] += 1
            return [
                {"title": f"paper{i}", "abstract": self.LONG_ABSTRACT,
                 "authors": "A. Author", "year": "2024"}
                for i in range(15)
            ]

        async def filt(topic, papers):
            self.counters["filter"] += 1
            return papers

        async def evidence(paper):
            self.counters["evidence"] += 1
            return ({"results": "Accuracy 92%.", "method": "A CNN."}, "llm-fallback")

        stack = contextlib.ExitStack()
        for patcher in (
            patch.object(manuscript_generation, "search_all", new=search),
            patch.object(manuscript_generation, "_filter_relevant_papers", new=filt),
            patch.object(manuscript_generation, "extract_evidence_for_paper", new=evidence),
            patch.object(manuscript_generation, "db",
                         {"sources": _FakeSources({}), "manuscript_references": self.refs}),
        ):
            stack.enter_context(patcher)
        return stack

    async def _generate_then_edit(self, section="abstract"):
        from services import usage_tracker
        token = usage_tracker.current_user_id.set("userA")
        try:
            with self._pipeline_spies():
                with patch.object(manuscript_generation, "generate_completion",
                                  new=AsyncMock(return_value="draft")),                      patch.object(manuscript_generation, "check_citation_grounding",
                                  new=AsyncMock(return_value={})):
                    await manuscript_generation.generate_section(self.TOPIC, section, "ctx")

                # Cold research cache: a restart, or past the 1h TTL.
                manuscript_generation._research_cache.clear()
                for key in self.counters:
                    self.counters[key] = 0

                captured = {}

                async def capture(system_prompt, user_prompt, **kwargs):
                    captured["prompt"] = user_prompt
                    return "revised text"

                with patch.object(manuscript_generation, "generate_completion", new=capture):
                    await manuscript_generation.edit_section(
                        self.TOPIC, section, "Some paragraph.", "Make it shorter."
                    )
                return captured["prompt"]
        finally:
            usage_tracker.current_user_id.reset(token)

    async def test_edit_makes_no_upstream_calls(self):
        await self._generate_then_edit()
        self.assertEqual(
            self.counters, {"search": 0, "filter": 0, "evidence": 0},
            "editing re-ran the research pipeline",
        )

    async def test_edit_is_still_grounded_in_the_reference_set(self):
        prompt = await self._generate_then_edit()
        sources = prompt.split("<sources>")[1].split("</sources>")[0]
        self.assertIn("[1]", sources)
        self.assertIn("paper0", sources)
        self.assertIn("results: Accuracy 92%.", sources)

    async def test_lit_review_edit_does_not_rerun_gap_analysis(self):
        with patch("ai.gap_analysis.analyze_gaps", new_callable=AsyncMock) as gaps:
            gaps.return_value = {"status": "insufficient_literature", "paper_count": 0}
            await self._generate_then_edit(section="lit_review")
            generation_calls = gaps.await_count
        self.assertEqual(generation_calls, 1, "gap analysis ran for the edit as well")

    async def test_source_context_is_bounded(self):
        prompt = await self._generate_then_edit()
        sources = prompt.split("<sources>")[1].split("</sources>")[0]
        self.assertLessEqual(len(sources), manuscript_generation._SOURCE_CONTEXT_CHARS + 100)
        self.assertLess(len(sources), 15 * len(self.LONG_ABSTRACT))

    async def test_evidence_fields_render_by_name_not_as_a_dict_repr(self):
        """`v.get('abstract') or v.get('evidence')` used to interpolate a dict."""
        prompt = await self._generate_then_edit()
        sources = prompt.split("<sources>")[1].split("</sources>")[0]
        self.assertNotIn("{'", sources)
        self.assertNotIn("'results':", sources)

    def test_renderer_prefers_evidence_over_abstract(self):
        rendered = manuscript_generation._render_source_context([{
            "index": "1", "title": "T", "authors": "A", "year": "2024",
            "evidence": {"results": "R"}, "abstract": "SHOULD-NOT-APPEAR",
        }])
        self.assertIn("results: R", rendered)
        self.assertNotIn("SHOULD-NOT-APPEAR", rendered)

    async def test_provider_failure_raises_instead_of_writing_a_note(self):
        """
        It used to return current_content + "_(Note: AI revision providers
        failed...)_" as the content, which the diff view showed as an ordinary
        addition -- so Accept wrote that note into the manuscript.
        """
        from fastapi import HTTPException
        from services import usage_tracker
        token = usage_tracker.current_user_id.set("userA")
        try:
            with patch.object(manuscript_generation, "db",
                              {"sources": _FakeSources({}), "manuscript_references": self.refs}),                  patch.object(manuscript_generation, "generate_completion",
                              new=AsyncMock(side_effect=RuntimeError("all providers down"))):
                with self.assertRaises(HTTPException) as ctx:
                    await manuscript_generation.edit_section(
                        self.TOPIC, "abstract", "Original paragraph.", "Shorten it."
                    )
        finally:
            usage_tracker.current_user_id.reset(token)

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertNotIn("providers failed", str(ctx.exception.detail))


class TestEditVerification(unittest.IsolatedAsyncioTestCase):
    """
    Regression (audit Tier 4): revision was the only write path into the
    manuscript with no verification. edit_section ran neither _citation_flags
    nor validate_numerical_claims, so a revision could introduce a hallucinated
    citation or an invented number unchecked -- while the UI kept showing the
    *previous* generation's verdict, reading as verified about text that no
    longer existed.
    """

    TOPIC = "convolutional neural networks"
    SNAPSHOT = [{
        "index": "1", "title": "Paper One", "authors": "A. Author", "year": "2024",
        "evidence": {"results": "Accuracy improved by 12.4%."}, "abstract": "",
    }]

    def setUp(self):
        manuscript_generation._research_cache.clear()
        self.refs = _FakeRefCollection()
        self.refs.docs[("userA", self.TOPIC)] = {"references": self.SNAPSHOT}

    async def _edit(self, revised, section="results", instructions="Make it bolder.",
                    grounding=None, current="Old text.", capture=None, **target):
        from services import usage_tracker
        token = usage_tracker.current_user_id.set("userA")
        try:
            async def fake_generate(system_prompt, user_prompt, **kwargs):
                if capture is not None:
                    capture.update(prompt=user_prompt, **kwargs)
                return revised

            async def fake_grounding(content, mapping):
                return grounding if grounding is not None else {}

            with patch.object(manuscript_generation, "db",
                              {"sources": _FakeSources({}), "manuscript_references": self.refs}),                  patch.object(manuscript_generation, "generate_completion", new=fake_generate),                  patch.object(manuscript_generation, "check_citation_grounding", new=fake_grounding):
                return await manuscript_generation.edit_section(
                    self.TOPIC, section, current, instructions, "ieee", **target
                )
        finally:
            usage_tracker.current_user_id.reset(token)

    async def test_returns_content_and_flags(self):
        content, flags = await self._edit("Revised text reaching 12.4% accuracy [1].")
        self.assertEqual(content, "Revised text reaching 12.4% accuracy [1].")
        self.assertIsInstance(flags, dict)

    async def test_invented_number_in_a_revision_is_flagged(self):
        _content, flags = await self._edit("Our method reached 99.9% accuracy [1].")
        self.assertIn("99.9%", flags["unverified_numbers"])

    async def test_number_supported_by_the_snapshot_is_not_flagged(self):
        _content, flags = await self._edit("Accuracy improved by 12.4% [1].")
        self.assertEqual(flags["unverified_numbers"], [])

    async def test_grounding_verdict_is_surfaced(self):
        verdict = {"citation_map": [
            {"sentence": "It always outperforms every baseline [1].",
             "cites": ["1"], "status": "partial", "note": "overgeneralises"}
        ]}
        _content, flags = await self._edit(
            "It always outperforms every baseline [1].", grounding=verdict
        )
        self.assertEqual([c["status"] for c in flags["citation_map"]], ["partial"])

    async def test_truncated_revision_is_flagged(self):
        budget = manuscript_generation._edit_token_budget("x" * 400)
        # Fills the budget and stops mid-sentence.
        revised = ("word " * (budget * 4 // 5)).strip() + " and then the argument continues"
        _content, flags = await self._edit(revised, current="x" * 400)
        self.assertTrue(flags.get("truncated"))

    async def test_complete_revision_is_not_flagged_as_truncated(self):
        _content, flags = await self._edit("A short, complete revision [1].")
        self.assertNotIn("truncated", flags)

    async def test_nonsense_instructions_are_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            await self._edit("anything", instructions="   ")
        self.assertEqual(ctx.exception.status_code, 400)

    # -- diagram health --------------------------------------------------
    # A revision re-emits the whole section, so the ```mermaid block travels
    # through the model as ordinary text. Nothing used to check what came back.

    CHART = (
        "```mermaid\nxychart-beta\n"
        '    x-axis ["Baseline", "Proposed"]\n'
        "    bar [65.4, 92.8]\n```"
    )

    async def test_intact_diagram_raises_no_flag(self):
        _content, flags = await self._edit(
            f"Prose [1].\n\n{self.CHART}", current=f"Old prose.\n\n{self.CHART}"
        )
        self.assertNotIn("diagram_errors", flags)
        self.assertNotIn("diagrams_dropped", flags)

    async def test_revision_that_breaks_the_diagram_is_flagged(self):
        broken = (
            "```mermaid\nxychart-beta\n"
            '    x-axis ["Baseline", "Proposed"]\n'
            "    bar [65.4, 92.8%]\n```"
        )
        _content, flags = await self._edit(
            f"Prose [1].\n\n{broken}", current=f"Old prose.\n\n{self.CHART}"
        )
        self.assertIn("non-numeric", flags["diagram_errors"][0]["error"])

    async def test_revision_that_silently_drops_the_diagram_is_flagged(self):
        _content, flags = await self._edit(
            "Prose [1], and the chart is gone.", current=f"Old prose.\n\n{self.CHART}"
        )
        self.assertEqual(flags["diagrams_dropped"], 1)

    async def test_prose_only_section_gets_no_diagram_flags(self):
        _content, flags = await self._edit("A short, complete revision [1].")
        self.assertNotIn("diagram_errors", flags)
        self.assertNotIn("diagrams_dropped", flags)


class TestScopedEdit(unittest.IsolatedAsyncioTestCase):
    """
    Regression (audit Tier 5, E7): a revision re-emits the whole section, so
    "fix the chart" came back with untouched paragraphs subtly reworded at
    temperature 0.45. Prompt rules make that less likely; only splicing makes it
    impossible. Text outside the target span must be *copied*, never regenerated.
    """

    TOPIC = "convolutional neural networks"
    SECTION = (
        "Opening paragraph grounded in prior work [1].\n\n"
        "```mermaid\nxychart-beta\n    x-axis [\"A\", \"B\"]\n    bar [1, 2]\n```\n\n"
        "Closing paragraph about the projected outcome [1]."
    )
    CHART = 'xychart-beta\n    x-axis ["A", "B"]\n    bar [1, 2]'

    def setUp(self):
        manuscript_generation._research_cache.clear()
        self.refs = _FakeRefCollection()
        self.refs.docs[("userA", self.TOPIC)] = {"references": TestEditVerification.SNAPSHOT}

    async def _edit(self, revised, current=None, capture=None, **target):
        from services import usage_tracker
        token = usage_tracker.current_user_id.set("userA")
        try:
            async def fake_generate(system_prompt, user_prompt, **kwargs):
                if capture is not None:
                    capture.update(prompt=user_prompt, **kwargs)
                return revised

            async def fake_grounding(content, mapping):
                return {}

            with patch.object(manuscript_generation, "db",
                              {"sources": _FakeSources({}), "manuscript_references": self.refs}),                  patch.object(manuscript_generation, "generate_completion", new=fake_generate),                  patch.object(manuscript_generation, "check_citation_grounding", new=fake_grounding):
                return await manuscript_generation.edit_section(
                    self.TOPIC, "results",
                    self.SECTION if current is None else current,
                    "Fix the chart.", "ieee", **target
                )
        finally:
            usage_tracker.current_user_id.reset(token)

    def _chart_target(self):
        start = self.SECTION.index(self.CHART)
        return {"target_text": self.CHART, "target_start": start,
                "target_end": start + len(self.CHART), "target_kind": "diagram"}

    async def test_prose_outside_the_span_is_byte_identical(self):
        # The model returns *only* a new chart; the paragraphs never pass
        # through it, so they cannot drift even if the model misbehaves.
        content, _flags = await self._edit(
            'xychart-beta\n    x-axis ["A", "B"]\n    bar [7, 9]', **self._chart_target()
        )
        self.assertIn("Opening paragraph grounded in prior work [1].", content)
        self.assertIn("Closing paragraph about the projected outcome [1].", content)
        self.assertIn("bar [7, 9]", content)
        self.assertNotIn("bar [1, 2]", content)

    async def test_the_fences_survive_because_they_sit_outside_the_span(self):
        content, flags = await self._edit(
            'xychart-beta\n    x-axis ["A", "B"]\n    bar [7, 9]', **self._chart_target()
        )
        self.assertEqual(content.count("```"), 2)
        self.assertNotIn("diagram_errors", flags)

    async def test_a_fenced_reply_is_not_nested_inside_the_real_fence(self):
        content, flags = await self._edit(
            '```mermaid\nxychart-beta\n    x-axis ["A", "B"]\n    bar [7, 9]\n```',
            **self._chart_target()
        )
        self.assertEqual(content.count("```"), 2)
        self.assertNotIn("diagram_errors", flags)

    async def test_a_selection_target_rewrites_only_that_sentence(self):
        target = "Opening paragraph grounded in prior work [1]."
        content, _flags = await self._edit(
            "Revised opening grounded in prior work [1].",
            target_text=target, target_start=0, target_end=len(target),
            target_kind="selection",
        )
        self.assertTrue(content.startswith("Revised opening"))
        self.assertIn(self.CHART, content)
        self.assertIn("Closing paragraph about the projected outcome [1].", content)

    async def test_the_model_is_told_to_return_only_the_replacement(self):
        capture = {}
        await self._edit("xychart-beta\n    bar [7, 9]", capture=capture, **self._chart_target())
        self.assertIn("⟦EDIT⟧", capture["prompt"])
        self.assertIn("output ONLY the replacement for <target>", capture["prompt"])
        self.assertIn("no ``` fences", capture["prompt"])

    async def test_the_budget_tracks_the_span_not_the_section(self):
        capture = {}
        long_section = "x" * 12000 + "\n\n" + self.CHART
        start = long_section.index(self.CHART)
        await self._edit(
            "xychart-beta\n    bar [7, 9]", current=long_section, capture=capture,
            target_text=self.CHART, target_start=start, target_end=start + len(self.CHART),
            target_kind="diagram",
        )
        self.assertEqual(capture["max_tokens"], manuscript_generation._EDIT_MIN_TOKENS)

    async def test_a_stale_target_falls_back_to_the_whole_section_and_says_so(self):
        content, flags = await self._edit(
            "A whole new section [1].", target_text="text that was deleted",
            target_start=0, target_end=21, target_kind="selection",
        )
        self.assertTrue(flags["target_unresolved"])
        self.assertEqual(content, "A whole new section [1].")

    async def test_a_reply_that_echoes_the_section_is_flagged_as_an_overrun(self):
        _content, flags = await self._edit(self.SECTION * 3, **self._chart_target())
        self.assertTrue(flags["target_overrun"])

    async def test_no_target_behaves_exactly_as_before(self):
        content, flags = await self._edit("A whole new section [1].")
        self.assertEqual(content, "A whole new section [1].")
        self.assertNotIn("target_unresolved", flags)
        self.assertNotIn("target_overrun", flags)

    async def test_flags_are_computed_on_the_spliced_section(self):
        # Verification runs on the full section the user is about to accept, not
        # on the fragment the model returned -- so an invented number inside a
        # scoped replacement is still caught.
        target = "Opening paragraph grounded in prior work [1]."
        _content, flags = await self._edit(
            "Our method reached 99.9% accuracy [1].",
            target_text=target, target_start=0, target_end=len(target),
            target_kind="selection",
        )
        self.assertIn("99.9%", flags["unverified_numbers"])

    async def test_a_broken_replacement_chart_is_still_caught(self):
        _content, flags = await self._edit(
            'xychart-beta\n    x-axis ["A", "B"]\n    bar [1, two]', **self._chart_target()
        )
        self.assertIn("non-numeric", flags["diagram_errors"][0]["error"])


class TestEditPromptFraming(unittest.TestCase):
    """
    Regression: _METHOD_RESULTS_FRAMING was local to _prompt(), so a revision
    carried none of it. A revise pass could turn "it is expected that..." into
    "we observed that...", undoing the generator's fabrication guard.
    """

    def _prompt_for(self, section):
        return manuscript_generation._edit_prompt_fn(
            "a topic", section, "current content", "shorten it", "sources"
        )

    def test_methodology_edit_keeps_the_proposed_framing(self):
        self.assertIn("PROPOSED methodology", self._prompt_for("methodology"))

    def test_results_edit_keeps_the_projected_framing(self):
        self.assertIn("PROJECTED/EXPECTED", self._prompt_for("results"))

    def test_other_sections_get_no_framing_block(self):
        prompt = self._prompt_for("abstract")
        self.assertNotIn("PROPOSED methodology", prompt)
        self.assertNotIn("PROJECTED/EXPECTED", prompt)

    def test_generation_and_edit_share_one_definition(self):
        gen = manuscript_generation._prompt("a topic", "results", "", "ieee")
        edit = self._prompt_for("results")
        framing = manuscript_generation._METHOD_RESULTS_FRAMING["results"]
        self.assertIn(framing, gen)
        self.assertIn(framing, edit)

    def test_braces_in_user_input_do_not_break_the_template(self):
        prompt = manuscript_generation._edit_prompt_fn(
            "t", "results", "content with {braces}", "use {placeholders}", "src {x}"
        )
        self.assertIn("{braces}", prompt)
        self.assertIn("{placeholders}", prompt)

    def test_edit_is_scoped_to_what_was_asked(self):
        """
        Without this rule the model rewrites the whole section at temperature
        0.45 -- "fix the chart" came back with every paragraph reworded.
        """
        prompt = self._prompt_for("results")
        self.assertIn("Change ONLY what the instructions require", prompt)
        self.assertIn("character for character", prompt)

    def test_edit_prompt_states_the_mermaid_contract(self):
        prompt = self._prompt_for("results")
        self.assertIn("```mermaid", prompt)
        self.assertIn("Never drop a diagram unless the instructions ask you to", prompt)

    def test_scope_rule_does_not_displace_the_existing_rules(self):
        prompt = self._prompt_for("results")
        for rule in (
            "Stay grounded in <sources>",
            "DO NOT include a title or heading",
            "Preserve the epistemic framing",
            "Keep every [N] citation marker",
            "Output ONLY the revised text",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, prompt)


class TestEditTokenBudget(unittest.TestCase):
    """
    Regression: max_tokens was a flat 1200 on edits while lit_review generates
    at 2000, so revising a long section truncated it mid-sentence -- and the
    diff view offered the truncation for acceptance without warning.
    """

    def test_budget_grows_with_the_section(self):
        short = manuscript_generation._edit_token_budget("x" * 2000)
        long = manuscript_generation._edit_token_budget("x" * 12000)
        self.assertGreater(long, short)

    def test_budget_never_drops_below_the_old_floor(self):
        self.assertGreaterEqual(manuscript_generation._edit_token_budget(""), 1200)
        self.assertGreaterEqual(manuscript_generation._edit_token_budget("x" * 10), 1200)

    def test_budget_is_capped(self):
        self.assertLessEqual(
            manuscript_generation._edit_token_budget("x" * 500000),
            manuscript_generation._EDIT_MAX_TOKENS,
        )

    def test_a_long_section_gets_more_than_it_needs_to_echo_itself(self):
        """The revision must be able to be at least as long as the original."""
        content = "x" * 12000
        self.assertGreater(
            manuscript_generation._edit_token_budget(content), len(content) / 4
        )


if __name__ == '__main__':
    unittest.main()
