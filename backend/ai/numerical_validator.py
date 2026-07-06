import re

KEYWORDS = [
    "efficiency", "accuracy", "yield", "rate", "precision", 
    "recall", "f1", "factor", "ratio", "score", "capacity", 
    "voltage", "current", "bandwidth", "frequency", "temperature", 
    "power", "density", "conductance", "mobility", "viscosity", 
    "solubility", "resolution", "error", "p-value", "coefficient"
]

def validate_numerical_claims(generated_text: str, source_papers: list) -> dict:
    """
    Validates numerical claims in generated_text against the text of source_papers.
    Returns a dictionary with 'unverified_numbers' listing any hallucinated stats.
    """
    if not source_papers:
        return {"unverified_numbers": []}
    
    source_text = ""
    for p in source_papers:
        source_text += p.get("title", "") + " "
        source_text += p.get("abstract", "") + " "
        source_text += p.get("text", "") + " "
        
    source_text_lower = source_text.lower()
    
    all_numbers = re.finditer(r'\b(\d+(?:\.\d+)?)\b', generated_text)
    extracted_claims = []
    
    # helper to check if a range overlaps with any existing ranges
    percentage_ranges = [m.span() for m in re.finditer(r'\b(\d+(?:\.\d+)?)\s*(%|percent)', generated_text, re.IGNORECASE)]
    unit_ranges = [m.span() for m in re.finditer(r'\b(\d+(?:\.\d+)?)\s*(nm|mA/cm²|eV|V|W|Hz|μm|mm|cm|m|mg|g|kg|s|min|h|dB|F|Pa|°C|K|M|mM|µM|L|mL|µL)\b', generated_text)]
    
    for match in re.finditer(r'\b(\d+(?:\.\d+)?)\s*(%|percent)', generated_text, re.IGNORECASE):
        num_val = match.group(1)
        original_str = match.group(0).strip()
        extracted_claims.append((num_val, original_str, "percentage"))
        
    for match in re.finditer(r'\b(\d+(?:\.\d+)?)\s*(nm|mA/cm²|eV|V|W|Hz|μm|mm|cm|m|mg|g|kg|s|min|h|dB|F|Pa|°C|K|M|mM|µM|L|mL|µL)\b', generated_text):
        num_val = match.group(1)
        original_str = match.group(0).strip()
        extracted_claims.append((num_val, original_str, "unit"))
        
    for match in all_numbers:
        span = match.span()
        # skip if this number overlaps with a percentage or unit
        if any(s[0] <= span[0] and span[1] <= s[1] for s in percentage_ranges + unit_ranges):
            continue
            
        num_val = match.group(1)
        start = max(0, match.start() - 40)
        end = min(len(generated_text), match.end() + 40)
        window = generated_text[start:end].lower()
        
        if any(kw in window for kw in KEYWORDS):
            extracted_claims.append((num_val, match.group(0).strip(), "bare"))
            
    unverified = []
    verified_originals = set()
    
    for num_val, original_str, ctype in extracted_claims:
        is_verified = False
        
        if ctype == "percentage":
            pat = r'\b' + re.escape(num_val) + r'\s*(?:%|percent)'
            if re.search(pat, source_text_lower):
                is_verified = True
        elif ctype == "unit":
            unit_part = original_str[len(num_val):].strip().lower()
            pat = r'\b' + re.escape(num_val) + r'\s*' + re.escape(unit_part)
            if re.search(pat, source_text_lower):
                is_verified = True
        else:
            pat = r'\b' + re.escape(num_val) + r'\b'
            if re.search(pat, source_text_lower):
                is_verified = True
                
        if is_verified:
            verified_originals.add(original_str)
        else:
            if original_str not in unverified:
                unverified.append(original_str)

    # Sort and remove duplicates cleanly while preserving order
    seen = set()
    deduped = []
    for item in unverified:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
            
    return {"unverified_numbers": deduped}
