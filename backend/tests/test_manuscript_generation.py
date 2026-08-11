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


if __name__ == '__main__':
    unittest.main()
