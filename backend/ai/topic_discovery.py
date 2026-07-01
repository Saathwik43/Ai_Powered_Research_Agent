import json
import logging
from langchain_core.prompts import PromptTemplate
from ai.llm_provider import generate_completion

logger = logging.getLogger(__name__)

prompt_template = PromptTemplate(
    input_variables=["intent"],
    template="""[INST] You are an AI research assistant. A researcher is looking to explore the following domain/intent:
'{intent}'

CRITICAL INSTRUCTION: If the domain/intent '{intent}' is complete gibberish, a random string of characters, a nonsensical combination of unrelated everyday words, or doesn't correspond to a coherent, recognizable research subject, you MUST immediately output EXACTLY the following JSON and nothing else:
[{{ "error": "topic_unclear" }}]

Based on recent advancements, provide exactly 3 highly promising and trending research topics within this domain.
Output strictly in JSON format as a list of dictionaries with no other text, markdown, or explanation.
Example format:
[
  {{"id": 1, "title": "Topic Name", "impact": "High"}}
]
[/INST]"""
)

def _fallback_topics(intent: str):
    import re
    intent_alpha = re.sub(r'[^a-zA-Z]', '', intent)
    is_gibberish = not re.search(r'[aeiouyAEIOUY]', intent_alpha, re.IGNORECASE) or re.search(r'[bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ]{5,}', intent_alpha, re.IGNORECASE)
    if is_gibberish:
        return None
    return [
        {"id": 1, "title": f"Advancements in {intent}", "impact": "High"},
        {"id": 2, "title": f"Emerging Applications of {intent}", "impact": "High"},
        {"id": 3, "title": f"Challenges and Future Directions in {intent}", "impact": "Medium"},
    ]


async def discover_topics(intent: str):
    try:
        user_prompt = prompt_template.format(intent=intent)
        response = await generate_completion(system_prompt="", user_prompt=user_prompt, max_tokens=512, temperature=0.7)

        content = response.strip()
        start_idx = content.find('[')
        end_idx = content.rfind(']')
        if start_idx != -1 and end_idx != -1:
            topics = json.loads(content[start_idx:end_idx + 1])
            if topics and isinstance(topics, list) and "error" in topics[0]:
                return {"data": [], "source": "ai", "coherence_check": "failed"}
            return {"data": topics, "source": "ai"}
            
        if '{"error": "topic_unclear"}' in content:
            return {"data": [], "source": "ai", "coherence_check": "failed"}
            
        raise ValueError("No JSON array found in response")
    except Exception as e:
        logger.error(f"Error in discover_topics: {e}")
        fallback_data = _fallback_topics(intent)
        if fallback_data is None:
            return {"data": [], "source": "fallback", "coherence_check": "failed"}
        return {"data": fallback_data, "source": "fallback"}
