import json
import logging
from langchain_core.prompts import PromptTemplate
from ai.llm_provider import generate_completion

logger = logging.getLogger(__name__)

prompt_template = PromptTemplate(
    input_variables=["abstract", "domain"],
    template="""[INST] You are an AI publication advisor. A researcher has written a paper in the domain of '{domain}'.
Here is the abstract of their paper:
'{abstract}'

CRITICAL INSTRUCTION: If the abstract or domain '{domain}' is complete gibberish, a random string of characters, a nonsensical combination of unrelated everyday words, or doesn't correspond to a coherent, recognizable research subject, you MUST immediately output EXACTLY the following JSON and nothing else:
[{{ "error": "domain_unclear" }}]

Based on the domain and abstract, recommend exactly 3 suitable publication venues (journals or conferences).
Output strictly in JSON format as a list of dictionaries with no markdown or text.
Example format:
[
  {{"id": 1, "name": "Nature Machine Intelligence", "type": "Journal", "impact": "High", "scope": "AI and ML", "match": 95}}
]
[/INST]"""
)

def _fallback_venues(abstract: str, domain: str):
    import re
    combined = f"{abstract} {domain}"
    intent_alpha = re.sub(r'[^a-zA-Z]', '', combined)
    if intent_alpha:
        is_gibberish = not re.search(r'[aeiouyAEIOUY]', intent_alpha, re.IGNORECASE) or re.search(r'[bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ]{5,}', intent_alpha, re.IGNORECASE)
        if is_gibberish:
            return None
            
    return [
        {"id": 1, "name": "IEEE Access", "type": "Journal", "impact": "Medium", "scope": "Multidisciplinary", "match": 85},
        {"id": 2, "name": "PLOS One", "type": "Journal", "impact": "Medium", "scope": "General Science", "match": 80},
        {"id": 3, "name": "Springer Nature", "type": "Journal", "impact": "High", "scope": "General Science", "match": 75},
    ]

async def recommend_venues(abstract: str, domain: str):
    try:
        abs_text = abstract.strip() if abstract.strip() else f"Research focused on {domain}"
        user_prompt = prompt_template.format(abstract=abs_text, domain=domain)
        response = await generate_completion(system_prompt="", user_prompt=user_prompt, max_tokens=512, temperature=0.3)

        content = response.strip()
        start_idx = content.find('[')
        end_idx = content.rfind(']')
        if start_idx != -1 and end_idx != -1:
            venues = json.loads(content[start_idx:end_idx + 1])
            if venues and isinstance(venues, list) and "error" in venues[0]:
                return {"data": [], "source": "ai", "coherence_check": "failed"}
            return {"data": venues, "source": "ai"}
            
        if '{"error": "domain_unclear"}' in content:
            return {"data": [], "source": "ai", "coherence_check": "failed"}
            
        raise ValueError("No JSON array found in response")
    except Exception as e:
        logger.error(f"Error in recommend_venues: {e}")
        fallback_data = _fallback_venues(abstract, domain)
        if fallback_data is None:
            return {"data": [], "source": "fallback", "coherence_check": "failed"}
        return {"data": fallback_data, "source": "fallback"}
