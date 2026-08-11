import re
import math
from collections import Counter
import logging

logger = logging.getLogger(__name__)

# Pure grammar / closed-class words. Used for query tokenization and for
# deciding whether a bigram edge is just glue ("of the").
GRAMMAR_STOPS = {
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
}

# Academic filler for *unigram* topics only. Keep domain nouns like "data",
# "model", "method", "analysis" out of standalone topic lists, but still allow
# them inside bigrams ("data science", "machine learning") — see _ngrams_for_doc.
STOP_WORDS = GRAMMAR_STOPS | {
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
    """True when *w* may stand alone as a unigram topic."""
    return len(w) > 2 and w not in STOP_WORDS and not w.isdigit()


def _is_grammar(w: str) -> bool:
    return len(w) <= 2 or w in GRAMMAR_STOPS or w.isdigit()


def _ngrams_for_doc(words: list[str]) -> tuple[Counter, Counter]:
    """Return (bigram_counts, unigram_counts) for a single document's word list."""
    bigrams: Counter = Counter()
    unigrams: Counter = Counter()
    for i, w in enumerate(words):
        if _valid(w):
            unigrams[w] += 1
        if i < len(words) - 1:
            w1, w2 = words[i], words[i + 1]
            # Skip pure grammar glue ("of the"). Keep mixed pairs so
            # academic-filler+content still yields "data science".
            if _is_grammar(w1) or _is_grammar(w2):
                continue
            if _valid(w1) or _valid(w2):
                bigrams[f"{w1} {w2}"] += 1
    return bigrams, unigrams


def _stemish(w: str) -> str:
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _query_tokens(query: str) -> set[str]:
    """
    Content stems from the user query.

    Uses grammar stops only — never academic filler — so "data science"
    keeps both tokens. Dropping "data" used to collapse the query to
    {"science"} and rank "Science Education" as related.
    """
    return {
        _stemish(w)
        for w in _tokenize(query)
        if len(w) > 2 and w not in GRAMMAR_STOPS and not w.isdigit()
    }


def _term_relatedness(term: str, q_stems: set[str]) -> float:
    """
    Prefer topic phrases that share meaning with the user's query.

    Multi-token queries weight by coverage so a single shared generic stem
    ("science" from "data science") cannot outrank fuller matches.
    """
    if not q_stems:
        return 1.0
    t_stems = {_stemish(w) for w in term.split() if not _is_grammar(w)}
    if not t_stems:
        return 0.25
    shared = t_stems & q_stems
    if shared:
        coverage = len(shared) / len(q_stems)
        # Square coverage: half-match on a 2-stem query scores ~0.5 and is
        # filtered out below; full match keeps a strong boost.
        return (1.0 + 0.9 * len(shared)) * (coverage ** 2)
    # Soft prefix match for near-stems the simple strip missed
    for qt in q_stems:
        for tt in t_stems:
            if len(qt) >= 4 and len(tt) >= 4 and (qt.startswith(tt) or tt.startswith(qt)):
                return 0.85
    return 0.22


def extract_top_topics(docs: list[str], query: str = "", top_n: int = 3) -> list[dict]:
    """
    Extracts the top N topics using TF-IDF across the individual papers
    (not one merged blob), then reweights by relatedness to *query* so
    method-boilerplate that co-occurs in an on-topic corpus does not beat
    query-anchored directions.

    docs: list of per-paper text blobs (title + abstract), one per paper.
    """
    try:
        docs = [d for d in (docs or []) if d and d.strip()]
        n_docs = len(docs)
        if n_docs == 0:
            return []

        q_stems = _query_tokens(query)
        query_phrase = " ".join(_tokenize(query)).strip()

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

        # Collect candidates with relatedness; pick in two passes so full query
        # matches rank first, then partial overlaps fill remaining slots.
        candidates: list[tuple[str, float, float]] = []  # term, score, rel

        for bg, count in total_bigram_counts.items():
            if _too_common(bigram_doc_freq[bg]):
                continue
            rel = _term_relatedness(bg, q_stems)
            if q_stems and rel < 0.35:
                continue
            base = _tfidf(count, bigram_doc_freq[bg]) * 2.5
            candidates.append((bg, base * rel, rel))

        for ug, count in total_unigram_counts.items():
            if _too_common(unigram_doc_freq[ug]):
                continue
            rel = _term_relatedness(ug, q_stems)
            if q_stems and rel < 0.35:
                continue
            part_of_bigram = any(ug in bg for bg in total_bigram_counts if total_bigram_counts[bg] > 1)
            base = _tfidf(count, unigram_doc_freq[ug]) * (0.5 if part_of_bigram else 1.0)
            candidates.append((ug, base * rel, rel))

        # Pin the user's exact phrase when it appears so Discover cannot ignore
        # "data science" in favour of "Science Education".
        if query_phrase and len(query_phrase.split()) >= 2:
            hit_docs = sum(1 for d in docs if query_phrase in d.lower())
            if hit_docs:
                candidates.append((query_phrase, 1e6, 99.0))

        def _pick(pool: list[tuple[str, float, float]], limit: int, picked_stems: list[set[str]]) -> list[dict]:
            out = []
            for term, score, _rel in sorted(pool, key=lambda x: (x[2], x[1]), reverse=True):
                if len(out) >= limit:
                    break
                stems = {_stemish(w) for w in term.split() if not _is_grammar(w)}
                if any(
                    stems and (stems <= prev or prev <= stems or len(stems & prev) / len(stems | prev) >= 0.7)
                    for prev in picked_stems
                ):
                    continue
                out.append({
                    "id": 0,  # renumbered below
                    "title": term.title(),
                    "impact": "High" if score > 5 else "Medium",
                })
                picked_stems.append(stems)
            return out

        picked_stems: list[set[str]] = []
        # Pass 1: strong matches (full coverage on short queries).
        strong = [c for c in candidates if c[2] >= 0.9]
        topics = _pick(strong, top_n, picked_stems)
        # Pass 2: fill with weaker but still overlapping phrases.
        if len(topics) < top_n:
            weak = [c for c in candidates if c[2] < 0.9]
            topics.extend(_pick(weak, top_n - len(topics), picked_stems))

        for i, t in enumerate(topics, start=1):
            t["id"] = i

        return topics

    except Exception as e:
        logger.error(f"Keyword extraction error: {e}")
        return []
