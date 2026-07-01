import os
import json
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import HuggingFaceEndpoint
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

HF_MODEL = "mistralai/Mixtral-8x7B-Instruct-v0.1"

llm = HuggingFaceEndpoint(
    repo_id=HF_MODEL,
    task="text-generation",
    max_new_tokens=512,
    temperature=0.7,
    huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN"),
)

prompt_template = PromptTemplate(
    input_variables=["intent"],
    template="""[INST] You are an AI research assistant. A researcher is looking to explore the following domain/intent:
'{intent}'

CRITICAL INSTRUCTION: If the domain/intent '{intent}' is complete gibberish, a random string of characters, or doesn't correspond to a coherent, recognizable research subject, you MUST immediately output EXACTLY the following JSON and nothing else:
[{{ "error": "topic_unclear" }}]

Based on recent advancements, provide exactly 3 highly promising and trending research topics within this domain.
Output strictly in JSON format as a list of dictionaries with no other text, markdown, or explanation.
Example format:
[
  {{"id": 1, "title": "Topic Name", "impact": "High"}}
]
[/INST]"""
)

_executor = ThreadPoolExecutor(max_workers=4)


def _run_chain(intent: str) -> str:
    """Runs the LangChain chain synchronously inside a thread. Catches StopIteration here."""
    try:
        chain = prompt_template | llm
        return chain.invoke({"intent": intent})
    except StopIteration as e:
        raise RuntimeError(f"LangChain StopIteration: {e}") from e


def _fallback_topics(intent: str):
    return [
        {"id": 1, "title": f"Advancements in {intent}", "impact": "High"},
        {"id": 2, "title": f"Emerging Applications of {intent}", "impact": "High"},
        {"id": 3, "title": f"Challenges and Future Directions in {intent}", "impact": "Medium"},
    ]


async def discover_topics(intent: str):
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(_executor, _run_chain, intent)

        content = response.strip()
        start_idx = content.find('[')
        end_idx = content.rfind(']')
        if start_idx != -1 and end_idx != -1:
            topics = json.loads(content[start_idx:end_idx + 1])
            return topics
        if '{"error": "topic_unclear"}' in content:
            return [{"error": "topic_unclear"}]
        raise ValueError("No JSON array found in response")
    except Exception as e:
        logger.error(f"Error in discover_topics: {e}")
        if "StopIteration" in str(e):
            import re
            intent_alpha = re.sub(r'[^a-zA-Z]', '', intent)
            is_gibberish = not re.search(r'[aeiouyAEIOUY]', intent_alpha, re.IGNORECASE) or re.search(r'[bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ]{5,}', intent_alpha, re.IGNORECASE)
            if is_gibberish:
                return [{"error": "topic_unclear"}]
        return _fallback_topics(intent)
