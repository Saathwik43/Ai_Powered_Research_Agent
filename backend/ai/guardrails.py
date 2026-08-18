import re

# Layer A syntactic heuristics operate per word token, never on the
# whitespace-stripped string: concatenating words creates artificial consonant
# runs across word boundaries ("CNN classification" -> "CNNcl") and rejects
# perfectly ordinary acronym-heavy research queries.
_TOKEN_RE = re.compile(r'[A-Za-z]+')
_VOWEL_RE = re.compile(r'[aeiouy]', re.IGNORECASE)
_CONSONANT_RUN_RE = re.compile(r'[bcdfghjklmnpqrstvwxz]{5,}', re.IGNORECASE)
_CHAR_REPEAT_RE = re.compile(r'(.)\1{4,}')

MIN_ALPHA_CHARS = 3
ACRONYM_MAX_LEN = 6


def _is_acronym(token: str) -> bool:
    """Short all-caps tokens (LLM, NLP, SVM, TCP) are legitimate query terms."""
    return 2 <= len(token) <= ACRONYM_MAX_LEN and token.isupper()


def _is_wordlike(token: str) -> bool:
    """True when a single token looks like a real word or a known-shape acronym."""
    if len(token) < 2:
        return False
    if _is_acronym(token):
        return True
    if _CHAR_REPEAT_RE.search(token):
        return False
    if not _VOWEL_RE.search(token):
        return False
    if _CONSONANT_RUN_RE.search(token):
        return False
    return True


# Layer B matches *instruction-shaped* text, not bare vocabulary.
#
# The previous list contained bare nouns -- "bypass", "system prompt", "exec(",
# "eval(", "drop table" -- and was applied to extracted PDF text as well as to
# user input. That rejected entire fields of legitimate research: coronary
# artery *bypass* grafting, cache-*bypass* architectures, *bypass* capacitors,
# and any LLM-safety paper that says "system prompt". A term only signals an
# injection attempt when it appears as a command aimed at the assistant, so
# every pattern below requires that imperative framing.
_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}?"
        r"\b(previous|prior|above|preceding|earlier|initial|original|all)\b"
        r"[^.\n]{0,20}?\b(instruction|prompt|rule|direction|command|guideline)s?\b",
        r"\b(reveal|repeat|print|output|show|display|leak)\b[^.\n]{0,30}?"
        r"\byour\b[^.\n]{0,20}?\b(system\s+prompt|instructions|rules)\b",
        r"\byou\s+are\s+now\b[^.\n]{0,20}?\b(a|an|the)\b",
        r"\bact\s+as\s+(if\s+you|though\s+you)\b",
        r"<\s*/?\s*(system|assistant)\s*>",
    )
]


def validate_layer_b(text: str) -> bool:
    """
    Layer B injection check for **user-authored input** (topics, queries,
    custom prompts).

    Do not call this on retrieved or extracted document content -- use
    ``validate_document_text`` for that. Untrusted source material is defended
    by prompt structure (an explicit ``<document>`` delimiter plus a
    system-prompt rule telling the model to treat it as data), not by a
    keyword blocklist.

    Returns False if the input fails validation.
    """
    if not text or not text.strip():
        return False

    return not any(p.search(text) for p in _INJECTION_PATTERNS)


def validate_document_text(text: str) -> bool:
    """
    Validity check for **extracted document text** (PDF, uploaded source, URL).

    Deliberately does not scan for injection phrases. A paper is allowed to
    contain any words it likes, including words that would look like an attack
    if a user had typed them. This only confirms extraction produced something
    usable; callers must still pass the text to the model inside a
    ``<document>`` delimiter so it is treated as data rather than instructions.
    """
    return bool(text and text.strip())

def validate_input_layers_a_b(text: str) -> bool:
    """
    Validates input using Layer A (syntactic) and Layer B (injection).
    Returns False if the input fails validation.
    """
    if not text or not text.strip():
        return False

    # Layer A: Syntactic check (keyboard mash, no-vowel, char-repeat), per token.
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return False

    # Minimum length: inputs with fewer than 3 letters are too short to be
    # a meaningful research topic and trivially bypass the heuristic checks
    # below (e.g. "a" contains a vowel and can't match a 5-char consonant run).
    if sum(len(t) for t in tokens) < MIN_ALPHA_CHARS:
        return False

    # One real-looking token is enough — Layer C (LLM coherence) is the
    # authority on meaning, this layer only filters obvious keyboard mash.
    if not any(_is_wordlike(t) for t in tokens):
        return False

    # Layer B: Injection/sanitization check
    return validate_layer_b(text)
