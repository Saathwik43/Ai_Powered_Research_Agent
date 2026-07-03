"""
Shared paper relevance filtering used by both manuscript generation and
the /api/literature endpoint.

Fail-open behaviour note
------------------------
When the Groq classification call fails (rate limit, timeout, network error),
the paper is **included** by default.  This is intentional for manuscript
generation (better to surface a possibly-borderline paper than silently drop
context), but carries a known trade-off for the literature endpoint: under
heavy Groq load every unclassified paper leaks through, potentially returning
noisy results to the user.  The trade-off is documented here pending a
frontend 'low-confidence' flag or server-side fail-closed option.
"""

import logging
from ai.llm_provider import generate_completion

logger = logging.getLogger(__name__)

__all__ = ["_filter_relevant_papers"]


async def _filter_relevant_papers(topic: str, papers: list) -> list:
    """
    Filter *papers* by relevance to *topic*.

    Fast-path: if a paper already carries a `relevance_score` field
    (e.g. from Semantic Scholar), papers with score < 0.5 are dropped
    without an LLM call.

    LLM-path: for all other papers a single yes/no call is made to the
    configured provider (Groq in auto mode).  On failure the paper is
    **included** (fail-open).

    Parameters
    ----------
    topic : str
        The research topic the papers must be relevant to.
    papers : list[dict]
        Raw paper dicts as returned by `search_all()`.

    Returns
    -------
    list[dict]
        Subset of *papers* deemed relevant.
    """
    relevant = []
    for paper in papers:
        # Fast-path: check if provider already scored relevance
        if "relevance_score" in paper:
            if paper["relevance_score"] >= 0.5:
                relevant.append(paper)
            else:
                logger.info(
                    f"Filtered out low-relevance paper "
                    f"(score={paper['relevance_score']}): {paper.get('title', '')}"
                )
            continue

        # LLM-path: single-call relevance classification
        title = paper.get("title", "")
        abstract = (paper.get("abstract", "") or "")[:300]
        try:
            answer = await generate_completion(
                system_prompt="You are a research relevance classifier. Answer only 'yes' or 'no'.",
                user_prompt=(
                    f'Is the following paper relevant to the research topic "{topic}"?\n'
                    f'Paper title: "{title}"\n'
                    f'Paper abstract: "{abstract}"\n'
                    'Answer with exactly "yes" or "no".'
                ),
                max_tokens=5,
                temperature=0.0,
            )
            if answer.strip().lower().startswith("yes"):
                relevant.append(paper)
            else:
                logger.info(f"Filtered out irrelevant paper: {title}")
        except Exception as e:
            logger.warning(
                f"Relevance check failed for '{title}', including by default: {e}"
            )
            relevant.append(paper)  # fail-open: include if classification fails

    return relevant
