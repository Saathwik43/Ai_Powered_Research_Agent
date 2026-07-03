import re

def validate_input_layers_a_b(text: str) -> bool:
    """
    Validates input using Layer A (syntactic) and Layer B (injection).
    Returns False if the input fails validation.
    """
    if not text or not text.strip():
        return False
        
    # Layer A: Syntactic regex check (keyboard mash, no-vowel, char-repeat)
    text_alpha = re.sub(r'[^a-zA-Z]', '', text)

    # Minimum length: inputs with fewer than 3 letters are too short to be
    # a meaningful research topic and trivially bypass the heuristic checks
    # below (e.g. "a" contains a vowel and can't match a 5-char consonant run).
    if len(text_alpha) < 3:
        return False

    if text_alpha:
        is_gibberish = not re.search(r'[aeiouyAEIOUY]', text_alpha, re.IGNORECASE) or re.search(r'[bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ]{5,}', text_alpha, re.IGNORECASE)
        # Check for character repeats (e.g., "aaaaa")
        has_repeats = bool(re.search(r'(.)\1{4,}', text_alpha))
        if is_gibberish or has_repeats:
            return False

    # Layer B: Injection/sanitization check
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
