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


def validate_layer_b(text: str) -> bool:
    """
    Validates input using Layer B (injection/sanitization check).
    Returns False if the input fails validation.
    """
    if not text or not text.strip():
        return False

    injection_patterns = [
        r"ignore all previous instructions",
        r"system prompt",
        r"bypass",
        r"drop table",
        r"exec\(",
        r"eval\(",
    ]
    text_lower = text.lower()
    for pattern in injection_patterns:
        if re.search(pattern, text_lower):
            return False

    return True

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
