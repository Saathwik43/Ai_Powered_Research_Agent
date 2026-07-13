import re
from collections import Counter
import logging

logger = logging.getLogger(__name__)

# Comprehensive stop words including common academic filler words
STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "cannot",
    "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how",
    "if", "in", "into", "is", "it", "its", "itself", "let", "me", "more", "most", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought", "our", "ours",
    "ourselves", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where",
    "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself",
    # Academic filler
    "paper", "papers", "study", "studies", "research", "results", "result", "analysis", "using", "used",
    "new", "method", "methods", "based", "approach", "proposed", "model", "models", "data", "also",
    "show", "shown", "shows", "two", "one", "can", "may", "however", "well", "first", "present",
    "presented", "use", "provide", "provides", "recent", "several", "different", "including", "include",
    "found", "demonstrate", "demonstrated", "important", "significant", "various", "many", "three",
    "high", "low", "effect", "effects", "figure", "table", "abstract", "introduction", "conclusion",
    "available", "number", "et", "al", "fig", "vol", "pp", "doi", "http", "https", "www", "org", "com",
}


def extract_top_topics(text: str, query: str = "", top_n: int = 3) -> list[dict]:
    """
    Extracts the top N topics from aggregated paper titles/abstracts
    using bigram and unigram frequency analysis. No AI/LLM required.
    """
    try:
        text = text.lower()
        query_words = set(re.findall(r'\b\w+\b', query.lower()))

        # Keep only alphanumeric and spaces
        cleaned = re.sub(r'[^a-z0-9\s]', ' ', text)
        words = cleaned.split()

        def _valid(w: str) -> bool:
            return len(w) > 2 and w not in STOP_WORDS and w not in query_words and not w.isdigit()

        # --- Bigrams (scored higher — they make better topic names) ---
        bigram_counts: Counter = Counter()
        for i in range(len(words) - 1):
            w1, w2 = words[i], words[i + 1]
            if _valid(w1) and _valid(w2):
                bigram_counts[f"{w1} {w2}"] += 1

        # --- Unigrams ---
        unigram_counts: Counter = Counter()
        for w in words:
            if _valid(w):
                unigram_counts[w] += 1

        # --- Scoring: bigrams get a 2.5× multiplier ---
        scores: dict[str, float] = {}
        for bg, count in bigram_counts.items():
            scores[bg] = count * 2.5

        for ug, count in unigram_counts.items():
            # Suppress unigrams that already appear inside a popular bigram
            part_of_bigram = any(ug in bg for bg in bigram_counts if bigram_counts[bg] > 1)
            scores[ug] = count * (0.5 if part_of_bigram else 1.0)

        # --- Pick top N, preferring bigrams for readability ---
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        topics = []
        for term, score in ranked:
            if len(topics) >= top_n:
                break
            topics.append({
                "id": len(topics) + 1,
                "title": term.title(),
                "impact": "High" if score > 5 else "Medium",
            })

        return topics

    except Exception as e:
        logger.error(f"Keyword extraction error: {e}")
        return []
