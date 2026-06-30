import os
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import HuggingFaceEndpoint
from dotenv import load_dotenv

load_dotenv()

llm = HuggingFaceEndpoint(
    repo_id="mistralai/Mixtral-8x7B-Instruct-v0.1",
    task="text-generation",
    max_new_tokens=512,
    temperature=0.3,
    huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN"),
)

prompt_template = PromptTemplate(
    input_variables=["abstract", "domain"],
    template="""[INST] You are an AI publication advisor. A researcher has written a paper in the domain of '{domain}'.
Here is the abstract of their paper:
'{abstract}'

Based on the domain and abstract, recommend exactly 3 suitable publication venues (journals or conferences).
Output strictly in JSON format as a list of dictionaries with no markdown or text.
Example format:
[
  {{"id": 1, "name": "Nature Machine Intelligence", "type": "Journal", "impact": "High", "scope": "AI and ML", "match": 95}}
]
[/INST]"""
)

_executor = ThreadPoolExecutor(max_workers=4)


def _run_chain(abstract: str, domain: str) -> str:
    try:
        chain = prompt_template | llm
        return chain.invoke({"abstract": abstract, "domain": domain})
    except StopIteration as e:
        raise RuntimeError(f"LangChain StopIteration: {e}") from e


async def recommend_venues(abstract: str, domain: str):
    try:
        abs_text = abstract.strip() if abstract.strip() else f"Research focused on {domain}"
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(_executor, _run_chain, abs_text, domain)

        content = response.strip()
        start_idx = content.find('[')
        end_idx = content.rfind(']')
        if start_idx != -1 and end_idx != -1:
            venues = json.loads(content[start_idx:end_idx + 1])
            return venues
        raise ValueError("No JSON array found in response")
    except Exception as e:
        print(f"Error in recommend_venues: {e}")
        return [
            {"id": 1, "name": "IEEE Access", "type": "Journal", "impact": "Medium", "scope": "Multidisciplinary", "match": 85},
            {"id": 2, "name": "PLOS One", "type": "Journal", "impact": "Medium", "scope": "General Science", "match": 80},
            {"id": 3, "name": "Springer Nature", "type": "Journal", "impact": "High", "scope": "General Science", "match": 75},
        ]
