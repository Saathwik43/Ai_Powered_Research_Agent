import os
import asyncio
import requests
from dotenv import load_dotenv

load_dotenv()

S2_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
S2_BASE_URL = "https://api.semanticscholar.org/graph/v1"

class SemanticScholarClient:
    def __init__(self):
        self.api_key = S2_API_KEY
        self.headers = {"x-api-key": self.api_key} if self.api_key else {}
        self.lock = asyncio.Lock()

    async def search_papers(self, query: str):
        """
        Queries the Semantic Scholar API to search for research papers.
        Applies a rate-limit of 1 request per second.
        """
        url = f"{S2_BASE_URL}/paper/search?query={query}&fields=title,abstract,authors,venue,citationCount,referenceCount"
        
        async with self.lock:
            # Enforce 1 request per second rate limit
            await asyncio.sleep(1)
            try:
                response = requests.get(url, headers=self.headers)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                return {"error": str(e)}

client = SemanticScholarClient()

async def search_papers(query: str):
    return await client.search_papers(query)
