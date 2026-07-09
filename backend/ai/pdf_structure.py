import fitz
import re

_ARXIV_ID_RE = re.compile(r'^arXiv:|^\d{4}\.\d{4,5}(v\d+)?$')
_AUTHOR_SPLIT_RE = re.compile(r',|\band\b|;')
_NOISE_STRIP_RE = re.compile(r'[\d\*\u2020\u2021]')  # digits, *, dagger, double-dagger


def _page_blocks(page):
    """Get cleaned text blocks for one page: text, bbox, avg font size.
    Drops rotated/vertical blocks, arXiv id stamps, thin header/footer noise."""
    pw, ph = page.rect.width, page.rect.height
    raw_blocks = page.get_text("dict").get("blocks", [])
    kept = []

    for b in raw_blocks:
        lines = b.get("lines")
        if not lines:
            continue

        spans = [s for l in lines for s in l.get("spans", [])]
        text = " ".join(s.get("text", "") for s in spans).strip()
        if not text:
            continue

        x0, y0, x1, y1 = b["bbox"]
        width, height = x1 - x0, y1 - y0

        # skip thin header/footer noise (page numbers, running headers)
        if (y0 < ph * 0.04 or y1 > ph * 0.96) and len(text) < 50:
            continue

        # skip arXiv id / preprint stamp blocks
        if _ARXIV_ID_RE.match(text):
            continue

        # skip rotated/vertical text (e.g. arXiv sidebar rotated 90deg)
        is_rotated = any(abs(l.get("dir", (1, 0))[0]) < 0.9 for l in lines)
        if is_rotated:
            continue

        # skip narrow-tall sidebar blocks that report as "horizontal" anyway
        if height > width * 4 and width < pw * 0.08:
            continue

        avg_size = sum(s.get("size", 0) for s in spans) / max(1, len(spans))
        kept.append({"text": text, "x0": x0, "y0": y0, "width": width, "size": avg_size})

    return kept, pw


def _split_header_body(blocks, pw):
    """Header zone = blocks above the y where genuine right-column content
    starts (min y0 among blocks whose x0 sits past page mid). A short author
    line stays 'header' even though it's narrow -- classification is by y
    position, not block width. Returns (header_sorted_by_y, body_blocks)."""
    mid = pw / 2
    right_half = [b for b in blocks if b["x0"] > mid]
    if not right_half:
        return sorted(blocks, key=lambda b: b["y0"]), []

    col_start_y = min(b["y0"] for b in right_half)
    header = sorted([b for b in blocks if b["y0"] < col_start_y], key=lambda b: b["y0"])
    body = [b for b in blocks if b["y0"] >= col_start_y]
    return header, body


def _reading_order(blocks, pw):
    """Column-aware reading order: header zone top-to-bottom, then left
    column top-to-bottom, then right column top-to-bottom, then any trailing
    full-width blocks (tables) by y."""
    if not blocks:
        return []

    header, body = _split_header_body(blocks, pw)
    if not body:
        return header

    mid = pw / 2
    full_body = sorted([b for b in body if b["width"] >= pw * 0.55], key=lambda b: b["y0"])
    col_body = [b for b in body if b["width"] < pw * 0.55]
    left = sorted([b for b in col_body if b["x0"] + b["width"] / 2 < mid], key=lambda b: b["y0"])
    right = sorted([b for b in col_body if b["x0"] + b["width"] / 2 >= mid], key=lambda b: b["y0"])

    return header + left + right + full_body


def _detect_title_authors(header_blocks):
    """Title = largest-font block in header zone. Authors = next header block
    after title that looks like a name list. Returns confidence flag too."""
    if not header_blocks:
        return "", [], "low"

    title_block = max(header_blocks, key=lambda b: b["size"])
    title = title_block["text"]
    title_conf = "high" if len(title) >= 8 else "low"

    idx = header_blocks.index(title_block)
    authors = []
    author_conf = "low"
    for b in header_blocks[idx + 1:]:
        raw = _NOISE_STRIP_RE.sub("", b["text"]).strip()
        if not raw:
            continue
        candidates = [a.strip() for a in _AUTHOR_SPLIT_RE.split(raw) if a.strip()]
        if 1 <= len(candidates) <= 15 and all(len(a) < 60 for a in candidates):
            authors = candidates
            author_conf = "high"
        break

    return title, authors, title_conf if not authors else author_conf


def _fallback_title_authors(all_ordered_blocks):
    """If header-zone heuristic fails (empty title / no columns detected),
    fall back to first two reasonable text blocks in full reading order."""
    candidates = [b for b in all_ordered_blocks if len(b["text"]) >= 4]
    if not candidates:
        return "", []
    title = candidates[0]["text"]
    authors = []
    if len(candidates) > 1:
        raw = _NOISE_STRIP_RE.sub("", candidates[1]["text"]).strip()
        parts = [a.strip() for a in _AUTHOR_SPLIT_RE.split(raw) if a.strip()]
        if 1 <= len(parts) <= 15:
            authors = parts
    return title, authors


def extract_structure(file_bytes: bytes) -> dict:
    """
    Column-aware deterministic structure extraction (title, authors, abstract,
    sections) via PyMuPDF. Fixes 2-column reading order, rotated sidebar
    contamination (e.g. arXiv id strip), and header/footer bleed into section
    text that the old largest-font/next-block heuristic mishandled.
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")

    page0_blocks, page0_pw = ([], 0)
    ordered_all = []
    confidence = {"title": "low", "authors": "low", "abstract": "low"}

    for i, page in enumerate(doc):
        blocks, pw = _page_blocks(page)
        ordered = _reading_order(blocks, pw)
        ordered_all.extend(ordered)
        if i == 0:
            page0_blocks, page0_pw = blocks, pw

    # title / authors from page-1 header zone, else fallback
    header_zone, _ = _split_header_body(page0_blocks, page0_pw)
    page0_ordered = _reading_order(page0_blocks, page0_pw)

    title, authors, conf = _detect_title_authors(header_zone)
    if not title:
        title, authors = _fallback_title_authors(page0_ordered)
        conf = "low"
    confidence["title"] = conf
    confidence["authors"] = "high" if authors else "low"

    full_text = "\n".join(b["text"] for b in ordered_all)

    abstract = ""
    abstract_match = re.search(
        r'(?i)\bAbstract\b[:\-\s]*(.*?)(?=\n(?:1\.?\s+Introduction|Keywords|I\.?\s+Introduction))',
        full_text, re.DOTALL,
    )
    if abstract_match:
        abstract = abstract_match.group(1).strip()
        confidence["abstract"] = "high" if len(abstract) >= 40 else "low"

    import statistics

    # Compute median font size from body blocks
    body_blocks = [b for b in ordered_all if b not in header_zone]
    if not body_blocks:
        body_blocks = ordered_all
    median_size = statistics.median(b["size"] for b in body_blocks) if body_blocks else 10

    _HEADING_REGEX = re.compile(r'^(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.?|[A-Z]\.)\s+[A-Z]')
    _KEYWORD_HEADING_REGEX = re.compile(r'^(?:Introduction|Method(?:s)?|Results(?:\s+and\s+Discussion)?|Discussion|Conclusion(?:s)?|References|Materials|Acknowledgments?)$', re.IGNORECASE)

    headings = []
    author_texts = [a.lower() for a in authors] if authors else []
    
    for i, b in enumerate(ordered_all):
        text = b["text"].strip()
        if not text or text == title:
            continue
            
        # Skip blocks that look like the author list
        if i < 15 and authors:
            matches = sum(1 for a in author_texts if a in text.lower())
            if matches > 0 and matches >= len(author_texts) / 2:
                continue
            
        words = text.split()
        if len(words) >= 12:
            continue

        size = b["size"]
        is_head = False
        
        # Criterion 1: font size is meaningfully larger
        if size >= 1.15 * median_size:
            is_head = True
        # Criterion 2: matches numbering or known keywords exactly
        elif _HEADING_REGEX.match(text) or _KEYWORD_HEADING_REGEX.match(text):
            is_head = True

        if is_head:
            headings.append((i, b, text))

    sections = {}
    font_headings_count = sum(1 for _, b, _ in headings if b["size"] >= 1.15 * median_size)
    
    if headings:
        for idx, (block_idx, b, title_text) in enumerate(headings):
            start_idx = block_idx + 1
            end_idx = headings[idx + 1][0] if idx + 1 < len(headings) else len(ordered_all)
            
            sec_text = "\n".join(ob["text"] for ob in ordered_all[start_idx:end_idx]).strip()
            if not sec_text:
                continue
                
            key = title_text.lower()
            if "introduction" in key:
                key = "introduction"
            elif "method" in key:
                key = "method"
            elif "result" in key:
                key = "results"
            elif "discussion" in key:
                key = "discussion"
            elif "conclusion" in key:
                key = "conclusion"
            elif "reference" in key:
                key = "references"
                
            sections[key] = sec_text
            
        confidence["sections"] = "high" if font_headings_count >= 3 else "low"
    else:
        sections = {"full_text": full_text}
        confidence["sections"] = "low"

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "sections": sections,
        "confidence": confidence,
    }
