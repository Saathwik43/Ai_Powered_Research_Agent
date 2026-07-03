import asyncio
import json
import logging
import sys
from dotenv import load_dotenv
load_dotenv()

from integrations.paper_search import search_all
from ai.relevance import _filter_relevant_papers
from ai.manuscript_generation import generate_section

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def safe_print(*args, **kwargs):
    """Print with fallback for Windows cp1252 encoding issues."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        print(text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"), **kwargs)

# Cross-domain keywords that should NOT appear for the physics topic
IRRELEVANT_KEYWORDS = [
    "gravitational wave", "ligo", "particle physics", "dark matter",
    "higgs boson", "black hole", "string theory", "cosmolog",
    "neutrino", "quark", "hadron", "collider",
]


def _is_irrelevant(title: str) -> bool:
    """Check if a title contains cross-domain irrelevant keywords."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in IRRELEVANT_KEYWORDS)


async def test_original_qec():
    """Original test: Quantum Error Correction lit_review via Gemini override."""
    topic = "Quantum Error Correction"
    section = "lit_review"
    context = ""

    safe_print("=" * 70)
    safe_print(f"TEST 1: generate_section for topic: '{topic}', section: '{section}'")
    safe_print("=" * 70)
    content, flags = await generate_section(topic, section, context)

    safe_print("\n--- Output Content (first 500 chars) ---")
    safe_print(content[:500])

    safe_print("\n--- Flags ---")
    flags_summary = {k: v for k, v in flags.items() if k != "references"}
    safe_print(json.dumps(flags_summary, indent=2))

    ref_count = len(flags.get("references", {}))
    safe_print(f"\n[RESULT] References included: {ref_count}")
    return ref_count


async def test_physics_topic():
    """
    Test with the exact physics topic from evaluation feedback.
    Confirms cross-domain irrelevant papers (gravitational wave, particle physics)
    are excluded from the reference list post-filter.
    """
    topic = "ferroelectric nematic liquid crystal electroviscosity"

    safe_print("\n" + "=" * 70)
    safe_print(f"TEST 2: Physics topic relevance filter")
    safe_print(f"Topic: '{topic}'")
    safe_print("=" * 70)

    # Step 1: Get raw papers (before filter)
    safe_print("\n--- BEFORE filter (raw search_all results) ---")
    raw_papers = await search_all(topic, limit=8)
    safe_print(f"Total papers from search_all: {len(raw_papers)}")
    for i, p in enumerate(raw_papers, 1):
        title = p.get("title", "?")
        source = p.get("source", "?")
        irrelevant_flag = " *** IRRELEVANT ***" if _is_irrelevant(title) else ""
        safe_print(f"  [{i}] [{source}] {title}{irrelevant_flag}")

    # Step 2: Run relevance filter
    safe_print("\n--- AFTER filter (_filter_relevant_papers) ---")
    filtered_papers = await _filter_relevant_papers(topic, raw_papers)
    safe_print(f"Papers after filter: {len(filtered_papers)}")
    for i, p in enumerate(filtered_papers, 1):
        title = p.get("title", "?")
        source = p.get("source", "?")
        safe_print(f"  [{i}] [{source}] {title}")

    # Step 3: Check that irrelevant papers were excluded
    filtered_titles = [p.get("title", "").lower() for p in filtered_papers]
    irrelevant_in_filtered = [
        t for t in filtered_titles if _is_irrelevant(t)
    ]

    safe_print("\n--- VALIDATION ---")
    if irrelevant_in_filtered:
        safe_print(f"[FAIL] {len(irrelevant_in_filtered)} irrelevant paper(s) still present:")
        for t in irrelevant_in_filtered:
            safe_print(f"  - {t}")
    else:
        safe_print("[PASS] No cross-domain irrelevant papers in filtered results")

    removed_count = len(raw_papers) - len(filtered_papers)
    safe_print(f"[INFO] Removed {removed_count} papers via relevance filter")
    safe_print(f"[INFO] Remaining: {len(filtered_papers)} papers (threshold for ref list: >= 2)")

    return len(raw_papers), len(filtered_papers), len(irrelevant_in_filtered)


async def main():
    safe_print("Running test_gemini_retrieval.py\n")

    # Test 1: Original QEC test
    ref_count = await test_original_qec()

    # Test 2: Physics topic with relevance filter validation
    raw_count, filtered_count, irrelevant_remaining = await test_physics_topic()

    # Summary
    safe_print("\n" + "=" * 70)
    safe_print("SUMMARY")
    safe_print("=" * 70)
    safe_print(f"Test 1 (QEC lit_review): {ref_count} references included")
    safe_print(f"Test 2 (Physics filter): {raw_count} raw -> {filtered_count} after filter, "
          f"{irrelevant_remaining} irrelevant remaining")
    if irrelevant_remaining == 0:
        safe_print("\nAll tests passed - irrelevant papers correctly filtered out")
    else:
        safe_print(f"\n{irrelevant_remaining} irrelevant paper(s) leaked through filter")


if __name__ == "__main__":
    asyncio.run(main())
