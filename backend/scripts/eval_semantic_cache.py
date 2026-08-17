"""
Threshold evaluation for the semantic result cache.

The thresholds in services/semantic_cache.py are a starting guess. This script
measures them: it embeds a fixed list of labelled query pairs and reports, for
a sweep of candidate thresholds, how many pairs would be served from cache
correctly and how many would be served *wrongly*.

    cd backend && python -m scripts.eval_semantic_cache

Requires GEMINI_API_KEY — it makes real embedding calls (one per distinct
query, roughly 60 of them).

Reading the output
------------------
A **false hit** is the failure that matters: two queries a researcher would
consider different, served from one another's cache. Optimise for zero false
hits first, then for the highest true-hit rate that keeps it there.

Add pairs whenever a bad substitution is reported in the product, then re-run.
The list is the regression suite for this cache.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.llm_provider import get_embeddings_batch  # noqa: E402
from services.semantic_cache import _cosine  # noqa: E402

# Pairs a researcher would consider THE SAME search. Serving one from the
# other's cache is correct.
SAME = [
    ("machine learning in healthcare", "ML in healthcare"),
    ("CNN classification", "convolutional neural network classification"),
    ("LLM safety", "large language model safety"),
    ("NLP transformers", "natural language processing transformers"),
    ("graph neural networks", "GNN architectures"),
    ("federated learning privacy", "privacy-preserving federated learning"),
    ("self-driving car perception", "autonomous vehicle perception"),
    ("protein structure prediction", "predicting protein structures"),
    ("quantum error correction", "error correction in quantum computing"),
    ("transformer attention mechanisms", "attention mechanisms in transformers"),
    ("drug discovery using AI", "AI for drug discovery"),
    ("climate model uncertainty", "uncertainty in climate models"),
]

# Pairs that are genuinely DIFFERENT searches. Serving one from the other is a
# false hit — the failure this cache must not produce.
DIFFERENT = [
    ("quantum computing", "quantum cryptography"),
    ("machine learning", "deep learning"),
    ("CNN classification", "RNN classification"),
    ("supervised learning", "unsupervised learning"),
    ("protein folding", "protein synthesis"),
    ("solar cell efficiency", "solar system formation"),
    ("network intrusion detection", "social network analysis"),
    ("gene editing ethics", "gene expression analysis"),
    ("reinforcement learning robotics", "supervised learning robotics"),
    ("LLM safety", "LLM inference speed"),
    ("battery degradation", "battery manufacturing cost"),
    ("transformer attention", "electrical transformer design"),
]

CANDIDATE_THRESHOLDS = [0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98]


async def _embed_all(queries: list[str]) -> dict[str, list]:
    unique = sorted(set(queries))
    vectors = await get_embeddings_batch(unique, task_type="RETRIEVAL_QUERY")
    return {q: v for q, v in zip(unique, vectors) if v}


async def main() -> int:
    queries = [q for pair in SAME + DIFFERENT for q in pair]
    print(f"Embedding {len(set(queries))} distinct queries...")
    vectors = await _embed_all(queries)

    missing = [q for q in set(queries) if q not in vectors]
    if missing:
        print(f"\nERROR: no embedding returned for {len(missing)} queries. Is GEMINI_API_KEY set?")
        for q in missing[:5]:
            print(f"  - {q}")
        return 1

    same_scores = [(a, b, _cosine(vectors[a], vectors[b])) for a, b in SAME]
    diff_scores = [(a, b, _cosine(vectors[a], vectors[b])) for a, b in DIFFERENT]

    print("\n── Same-meaning pairs (want HIGH similarity) ─────────────────────")
    for a, b, s in sorted(same_scores, key=lambda x: x[2]):
        print(f"  {s:.4f}  {a!r} ~ {b!r}")

    print("\n── Different-meaning pairs (want LOW similarity) ─────────────────")
    for a, b, s in sorted(diff_scores, key=lambda x: -x[2]):
        print(f"  {s:.4f}  {a!r} vs {b!r}")

    print("\n── Threshold sweep ──────────────────────────────────────────────")
    print(f"  {'thresh':>7}  {'true hits':>10}  {'FALSE HITS':>11}  {'missed':>7}")
    best = None
    for threshold in CANDIDATE_THRESHOLDS:
        true_hits = sum(1 for _, _, s in same_scores if s >= threshold)
        false_hits = sum(1 for _, _, s in diff_scores if s >= threshold)
        missed = len(same_scores) - true_hits
        flag = "  <-- false hits" if false_hits else ""
        print(f"  {threshold:>7.2f}  {true_hits:>4}/{len(same_scores):<5}  {false_hits:>5}/{len(diff_scores):<5}  {missed:>7}{flag}")
        if false_hits == 0 and (best is None or true_hits > best[1]):
            best = (threshold, true_hits)

    print()
    if best:
        print(f"Lowest threshold with zero false hits: {best[0]:.2f} ({best[1]}/{len(same_scores)} same-meaning pairs served)")
        print("Set VERBATIM_THRESHOLD at or above this. RERANK_THRESHOLD may sit")
        print("lower, since that tier re-ranks against the typed query and only")
        print("reuses the candidate pool.")
    else:
        print("No candidate threshold avoids false hits. Do not widen the cache;")
        print("investigate the offending pairs above first.")
    return 0


if __name__ == "__main__":
    # The report is box-drawing characters; a Windows console defaults to
    # cp1252 and the run dies at the first print — after paying for every
    # embedding.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main()))
