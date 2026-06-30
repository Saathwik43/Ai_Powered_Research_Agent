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
    temperature=0.7,
    huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN"),
)

prompt_template = PromptTemplate(
    input_variables=["intent"],
    template="""[INST] You are an AI research assistant. A researcher is looking to explore the following domain/intent:
'{intent}'

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
        raise ValueError("No JSON array found in response")
    except Exception as e:
        print(f"Error in discover_topics: {e}")
        return [
            {"id": 1, "title": f"Advancements in {intent}", "impact": "High"},
            {"id": 2, "title": f"Emerging Applications of {intent}", "impact": "High"},
            {"id": 3, "title": f"Challenges and Future Directions in {intent}", "impact": "Medium"},
        ]
