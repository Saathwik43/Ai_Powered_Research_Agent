"""
Canonical query identity.

Two searches that *mean* the same thing must resolve to the same cache entry.
``canonical()`` reduces a raw query to a stable key: Unicode-normalised,
case-folded, punctuation-stripped, grammar words removed, remaining words
lightly stemmed, deduplicated and sorted.

    "Machine Learning"  ─┐
    "machine  learning" ─┼─→  key = "learning machine"
    " MACHINE LEARNING "─┘

The key is for **cache identity only**. Every outbound API call, every ranking
pass and everything shown to the user must use ``display`` — the key discards
word order and inflection, which search backends need and users expect to see.

Consequence worth knowing: the first variant of a query to miss the cache is
the one whose exact wording gets sent to the sources and used for ranking.
Later variants that canonicalise to the same key reuse that result set.

This module is the single home for ``GRAMMAR_STOPS`` and ``stem``;
``ai.keyword_extractor`` imports both from here so query tokenisation and
topic extraction can never drift apart.
"""

import re
import unicodedata
from dataclasses import dataclass

# Pure grammar / closed-class words. Never domain vocabulary — dropping a
# content word (even a generic one like "data") changes what a query means.
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

_NON_WORD = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def stem(word: str) -> str:
    """
    Deliberately minimal suffix strip — plurals only.

    A real stemmer would collapse "learning"/"learned"/"learner", which is
    wrong for a cache key: those are different queries. Plural folding is the
    one case where users clearly mean the same thing ("neural network" vs
    "neural networks").
    """
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


@dataclass(frozen=True)
class CanonicalQuery:
    """``display`` goes to APIs and the UI; ``key`` goes to caches."""

    display: str
    key: str
    tokens: tuple[str, ...]


def canonical(raw: str) -> CanonicalQuery:
    """Reduce *raw* to its cache identity. Never raises."""
    display = _WHITESPACE.sub(" ", str(raw or "")).strip()

    # NFKC folds ligatures, full-width forms and compatibility characters, so
    # a pasted "ﬁne-tuning" keys the same as a typed one.
    folded = _NON_WORD.sub(" ", unicodedata.normalize("NFKC", display).casefold()).strip()
    if not folded:
        return CanonicalQuery(display=display, key="", tokens=())

    words = folded.split()
    # Length is not a filter here: "vitamin d", "e coli" and "k means" all
    # depend on their one-letter token.
    content = [stem(w) for w in words if w not in GRAMMAR_STOPS]
    if not content:
        # An all-grammar query ("what is the") still needs a distinct key.
        content = [stem(w) for w in words]

    tokens = tuple(sorted(set(content)))
    return CanonicalQuery(display=display, key=" ".join(tokens), tokens=tokens)


def canonical_key(raw: str) -> str:
    """Cache-identity string for *raw*. Shorthand for ``canonical(raw).key``."""
    return canonical(raw).key
