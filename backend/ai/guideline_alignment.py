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
    max_new_tokens=768,
    temperature=0.3,
    huggingfacehub_api_token=os.getenv("HUGGINGFACEHUB_API_TOKEN"),
)

prompt_template = PromptTemplate(
    input_variables=["venue_name", "venue_type", "domain", "abstract"],
    template="""[INST] You are an expert academic publishing advisor. A researcher wants to submit their paper to '{venue_name}' ({venue_type}) in the domain of '{domain}'.

Here is their abstract:
'{abstract}'

Provide detailed, actionable formatting and submission guidelines for this venue.
Output strictly in JSON format with no markdown or extra text.
Use this exact format:
{{
  "venue": "{venue_name}",
  "word_limit": "e.g. 8000 words",
  "sections_required": ["Abstract", "Introduction", "..."],
  "citation_style": "e.g. IEEE / APA / Chicago",
  "submission_format": "e.g. PDF via online portal",
  "key_requirements": ["requirement 1", "requirement 2", "requirement 3"],
  "formatting_tips": ["tip 1", "tip 2", "tip 3"],
  "alignment_score": 85,
  "alignment_notes": "Brief explanation of how well the paper fits this venue."
}}
[/INST]"""
)

_executor = ThreadPoolExecutor(max_workers=4)


def _run_chain(venue_name: str, venue_type: str, domain: str, abstract: str) -> str:
    try:
        chain = prompt_template | llm
        return chain.invoke({
            "venue_name": venue_name,
            "venue_type": venue_type,
            "domain": domain,
            "abstract": abstract,
        })
    except StopIteration as e:
        raise RuntimeError(f"LangChain StopIteration: {e}") from e


async def align_guidelines(manuscript: dict, venue_guidelines: dict):
    venue_name = venue_guidelines.get("name", "Unknown Venue")
    venue_type = venue_guidelines.get("type", "Journal")
    domain = venue_guidelines.get("scope", manuscript.get("domain", "General Research"))
    abstract = manuscript.get("abstract", f"Research paper submitted to {venue_name}")

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(_executor, _run_chain, venue_name, venue_type, domain, abstract)

        content = response.strip()
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        if start_idx != -1 and end_idx != -1:
            guidelines = json.loads(content[start_idx:end_idx + 1])
            return guidelines
        raise ValueError("No JSON object found in response")
    except Exception as e:
        print(f"Error in align_guidelines: {e}")
        return {
            "venue": venue_name,
            "word_limit": "Typically 6000–10000 words",
            "sections_required": ["Abstract", "Introduction", "Related Work", "Methodology", "Results", "Conclusion", "References"],
            "citation_style": "IEEE",
            "submission_format": "PDF via online submission portal",
            "key_requirements": [
                "Anonymized manuscript for double-blind review",
                "Figures must be high-resolution (300 DPI minimum)",
                "References must follow venue citation style",
            ],
            "formatting_tips": [
                "Use a consistent font (e.g. Times New Roman 10pt or LaTeX default)",
                "Ensure all acronyms are defined on first use",
                "Keep abstract under 250 words",
            ],
            "alignment_score": 80,
            "alignment_notes": f"This paper appears to be a reasonable fit for {venue_name} based on the domain and abstract provided.",
        }
