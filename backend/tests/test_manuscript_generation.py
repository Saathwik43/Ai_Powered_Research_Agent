import unittest
from unittest.mock import patch, AsyncMock
import time

from ai import manuscript_generation
from ai.manuscript_generation import generate_section

class TestManuscriptGeneration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Clear cache before each test
        manuscript_generation._cache.clear()
        
    @patch('ai.manuscript_generation._generate_groq', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._generate_openrouter', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._generate_huggingface', new_callable=AsyncMock)
    async def test_groq_called_first(self, mock_hf, mock_or, mock_groq):
        # Setup MANUSCRIPT_PROVIDER
        manuscript_generation.MANUSCRIPT_PROVIDER = "auto"
        mock_groq.return_value = "Groq Draft"
        
        result = await generate_section("Topic A", "Introduction", "Context A")
        
        self.assertEqual(result, "Groq Draft")
        mock_groq.assert_called_once_with("Topic A", "Introduction", "Context A")
        mock_or.assert_not_called()
        mock_hf.assert_not_called()

    @patch('ai.manuscript_generation.asyncio.sleep', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._generate_groq', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._generate_openrouter', new_callable=AsyncMock)
    @patch('ai.manuscript_generation._generate_huggingface', new_callable=AsyncMock)
    async def test_fallback_when_provider_raises(self, mock_hf, mock_or, mock_groq, mock_sleep):
        manuscript_generation.MANUSCRIPT_PROVIDER = "auto"
        # Make Groq fail twice
        mock_groq.side_effect = Exception("Groq failed")
        mock_or.return_value = "OpenRouter Draft"
        
        result = await generate_section("Topic B", "Methodology", "Context B")
        
        self.assertEqual(result, "OpenRouter Draft")
        self.assertEqual(mock_groq.call_count, 2)
        mock_or.assert_called_once_with("Topic B", "Methodology", "Context B")
        mock_hf.assert_not_called()

    @patch('ai.manuscript_generation._generate_groq', new_callable=AsyncMock)
    async def test_cache_hit_skips_network(self, mock_groq):
        manuscript_generation.MANUSCRIPT_PROVIDER = "auto"
        
        topic, section, context = "Topic C", "Conclusion", "Context C"
        cache_key = hash(topic + section + context)
        
        # Pre-populate cache
        manuscript_generation._cache[cache_key] = {
            'content': "Cached Draft",
            'time': time.time()
        }
        
        result = await generate_section(topic, section, context)
        
        self.assertEqual(result, "Cached Draft")
        mock_groq.assert_not_called()

if __name__ == '__main__':
    unittest.main()
