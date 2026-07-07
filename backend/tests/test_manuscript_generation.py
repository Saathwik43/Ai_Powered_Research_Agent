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
        # Clear cache before each test
        manuscript_generation._cache.clear()

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
    async def test_cache_hit_skips_network(self, mock_gen, mock_filter, mock_search, mock_extract, mock_citation):
        """Cached results should skip network calls."""
        mock_extract.return_value = ({"objective": "Mock objective"}, "llm-fallback")
        mock_citation.return_value = {}
        mock_gen.return_value = "Fallback if missed"
        mock_search.return_value = MOCK_PAPERS
        mock_filter.return_value = MOCK_PAPERS

        topic, section, context = "Topic C", "Conclusion", "Context C"
        # Build context the same way generate_section would
        ref_text = "\n\nNumbered Reference List:\n"
        for idx, p in enumerate(MOCK_PAPERS, 1):
            ref_text += f"[{idx}] {p['authors']} ({p['year']}). {p['title']}. Objective: Mock objective. {p.get('doi', p.get('url', ''))}\n"
        full_context = context + ref_text

        cache_key = hash(topic + section + full_context)

        # Pre-populate cache
        manuscript_generation._cache[cache_key] = {
            'content': "Cached Draft",
            'time': time.time()
        }

        result, flags = await generate_section(topic, section, context)

        self.assertEqual(result, "Cached Draft", f"Cache missed! Expected Cached Draft, got {result}")
        mock_gen.assert_not_called()

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
    """Test the auto cascade ordering in llm_provider.py directly."""

    @patch('ai.llm_provider.LLM_PROVIDER', 'auto')
    @patch('ai.llm_provider._generate_groq', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_openrouter', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_huggingface', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_gemini', new_callable=AsyncMock)
    async def test_auto_cascade_excludes_gemini(self, mock_gemini, mock_hf, mock_or, mock_groq):
        """In auto mode, Gemini should NOT be tried. Only Groq → OpenRouter → HuggingFace."""
        from ai.llm_provider import generate_completion
        mock_groq.return_value = "Groq result"

        result = await generate_completion("system", "user", max_tokens=100)

        self.assertEqual(result, "Groq result")
        mock_groq.assert_called()
        mock_gemini.assert_not_called()

    @patch('ai.llm_provider.LLM_PROVIDER', 'auto')
    @patch('ai.llm_provider._generate_groq', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_openrouter', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_huggingface', new_callable=AsyncMock)
    @patch('ai.llm_provider._generate_gemini', new_callable=AsyncMock)
    async def test_auto_cascade_order_groq_first(self, mock_gemini, mock_hf, mock_or, mock_groq):
        """
        REGRESSION: In auto mode, Groq must be first. If Groq succeeds,
        OpenRouter and HuggingFace should not be called.
        """
        from ai.llm_provider import generate_completion
        mock_groq.return_value = "Groq result"

        result = await generate_completion("system", "user", max_tokens=100)

        self.assertEqual(result, "Groq result")
        mock_groq.assert_called()
        mock_or.assert_not_called()
        mock_hf.assert_not_called()
        mock_gemini.assert_not_called()


if __name__ == '__main__':
    unittest.main()
