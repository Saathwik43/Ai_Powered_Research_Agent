import re
import math
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


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text)
    return cleaned.split()


def _valid(w: str) -> bool:
    return len(w) > 2 and w not in STOP_WORDS and not w.isdigit()


def _ngrams_for_doc(words: list[str]) -> tuple[Counter, Counter]:
    """Return (bigram_counts, unigram_counts) for a single document's word list."""
    bigrams: Counter = Counter()
    unigrams: Counter = Counter()
    for i, w in enumerate(words):
        if _valid(w):
            unigrams[w] += 1
        if i < len(words) - 1:
            w1, w2 = words[i], words[i + 1]
            if _valid(w1) and _valid(w2):
                bigrams[f"{w1} {w2}"] += 1
    return bigrams, unigrams


def extract_top_topics(docs: list[str], query: str = "", top_n: int = 3) -> list[dict]:
    """
    Extracts the top N topics using TF-IDF across the individual papers
    (not one merged blob). A term that shows up in every paper (e.g.
    "machine learning") is domain-boilerplate and gets penalized by IDF
    automatically. A term concentrated in a few papers but repeated there
    is query-specific and scores high. No AI/LLM required, no hardcoded
    buzzword list, no need to ban the query's own words.

    docs: list of per-paper text blobs (title + abstract), one per paper.
    """
    try:
        docs = [d for d in (docs or []) if d and d.strip()]
        n_docs = len(docs)
        if n_docs == 0:
            return []

        total_bigram_counts: Counter = Counter()
        total_unigram_counts: Counter = Counter()
        bigram_doc_freq: Counter = Counter()
        unigram_doc_freq: Counter = Counter()

        for doc in docs:
            words = _tokenize(doc)
            bigrams, unigrams = _ngrams_for_doc(words)
            total_bigram_counts.update(bigrams)
            total_unigram_counts.update(unigrams)
            for term in bigrams:
                bigram_doc_freq[term] += 1
            for term in unigrams:
                unigram_doc_freq[term] += 1

        def _tfidf(term_freq: int, doc_freq: int) -> float:
            idf = math.log((n_docs + 1) / (doc_freq + 1)) + 1  # smoothed, always positive
            return term_freq * idf

        # max_df cutoff: a term sitting in >50% of the papers is corpus-wide
        # boilerplate (e.g. "machine learning" across an ML-adjacent paper
        # set), not a distinguishing topic — drop it outright rather than
        # just downweight it. Skip the cutoff when the sample is too small
        # (<4 papers) to trust the ratio.
        MAX_DF_RATIO = 0.5
        apply_cutoff = n_docs >= 4

        def _too_common(doc_freq: int) -> bool:
            return apply_cutoff and (doc_freq / n_docs) >= MAX_DF_RATIO

        scores: dict[str, float] = {}
        for bg, count in total_bigram_counts.items():
            if _too_common(bigram_doc_freq[bg]):
                continue
            scores[bg] = _tfidf(count, bigram_doc_freq[bg]) * 2.5  # bigrams read better as topic names

        for ug, count in total_unigram_counts.items():
            if _too_common(unigram_doc_freq[ug]):
                continue
            part_of_bigram = any(ug in bg for bg in total_bigram_counts if total_bigram_counts[bg] > 1)
            scores[ug] = _tfidf(count, unigram_doc_freq[ug]) * (0.5 if part_of_bigram else 1.0)

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
