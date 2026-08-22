import fitz
import re

_ARXIV_ID_RE = re.compile(r'^arXiv:|^\d{4}\.\d{4,5}(v\d+)?$')
_AUTHOR_SPLIT_RE = re.compile(r',|\band\b|;')
_NOISE_STRIP_RE = re.compile(r'[\d\*\u2020\u2021]')  # digits, *, dagger, double-dagger

# Author-zone parsing. GROBID read authors out of the TEI header; without it we
# recover them from the blocks between the title and the abstract heading.
# Splitting on commas alone was not enough: multi-column author rows flatten
# into a single space-separated run ("Jacob Devlin Ming-Wei Chang Kenton Lee"),
# which the old splitter returned as one 75-char "name" and then rejected.
_ABSTRACT_HEAD_RE = re.compile(r'^\s*abstract\b', re.IGNORECASE)
_AFFIL_RE = re.compile(
    r'@|\b(univ|universit|institute|instituto|laborator|labs?|inc\.?|ltd|gmbh|dept|department'
    r'|college|school|academy|hospital|center|centre|research|corp|technolog|google|microsoft'
    r'|facebook|meta|openai|deepmind|amazon|nvidia|ibm|apple)\b',
    re.IGNORECASE,
)
# "First Last", "First M. Last", "Jean-Luc Picard", "Kristina Toutanova"
_NAME_RE = re.compile(
    r"\b[A-Z][A-Za-z'\u2019\-]+(?:\s+(?:[A-Z]\.|[A-Z][A-Za-z'\u2019\-]+)){1,3}\b"
)

# Section-heading numbering. A dotted prefix ("3.1", "2.4.1") marks a
# subsection: its body belongs to the parent section, not to a new top-level
# key. Without this the Transformer paper produced 24 "sections" and CLIP 111,
# most of which no SECTION_ALIASES entry could ever match.
_HEAD_NUM_RE = re.compile(r'^\s*(\d+(?:\.\d+)*)\.?\s+')
_ROMAN_NUM_RE = re.compile(r'^\s*([IVXLC]+|[A-Z])\.\s+')


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


def _running_key(text):
    """Identity used to spot a block repeated as a running header.

    Digits are stripped because the header carries the page number
    ("... Natural Language Supervision 7"), so an exact-text tally counts each
    page separately and never reaches the repeat threshold.
    """
    return re.sub(r'\s+', ' ', re.sub(r'\d+', '', text)).strip().lower()


# Unicode-aware: [A-Z] alone drops non-ASCII given names such as "Łukasz".
_WORD_TOKEN_RE = re.compile(r"^[^\W\d_][\w'’\-]*$", re.UNICODE)
_INITIAL_RE = re.compile(r"^[^\W\d_]\.$", re.UNICODE)


def _is_name_token(tok):
    return bool(_WORD_TOKEN_RE.match(tok)) and tok[:1].isupper()


def _split_name_run(text):
    """Split a flattened author row into individual names.

    Multi-column author blocks extract as one run with the column gaps
    collapsed to single spaces ("Jacob Devlin Ming-Wei Chang Kenton Lee"), so a
    plain regex scan returns one 60-char pseudo-name. Names are consumed
    greedily instead: two capitalised tokens, or three when the middle one is
    an initial ("Tom B. Brown"). Anything not capitalised ends the run.
    """
    tokens = text.split()
    names, i = [], 0
    while i < len(tokens):
        if not _is_name_token(tokens[i]):
            i += 1
            continue
        if (
            i + 2 < len(tokens)
            and _INITIAL_RE.match(tokens[i + 1])
            and _is_name_token(tokens[i + 2])
        ):
            names.append(" ".join(tokens[i:i + 3]))
            i += 3
        elif i + 1 < len(tokens) and _is_name_token(tokens[i + 1]):
            names.append(" ".join(tokens[i:i + 2]))
            i += 2
        else:
            i += 1
    return [n for n in names if len(n) <= 60]


def _authors_from_zone(page0_ordered, title):
    """Recover the author list from the blocks between the title and the
    abstract heading on page 1.

    ``_detect_title_authors`` only looked inside the header zone, but on papers
    whose author row is laid out in columns (Attention Is All You Need) the
    column split puts the authors in the *body* zone, so the header-only search
    found nothing. Scanning the title -> abstract window catches both layouts.
    """
    try:
        start = next(i for i, b in enumerate(page0_ordered) if b["text"] == title) + 1
    except StopIteration:
        start = 0

    names = []
    seen = set()
    for b in page0_ordered[start:]:
        text = b["text"].strip()
        if _ABSTRACT_HEAD_RE.match(text):
            break
        if not text or len(text) > 400:
            continue
        # Author lines are usually "Name * Affiliation email". Cutting at the
        # first affiliation/email token keeps the name; dropping the whole line
        # (the obvious move) loses every author on papers laid out this way.
        cut = _AFFIL_RE.search(text)
        if cut:
            text = text[:cut.start()]
        cleaned = _NOISE_STRIP_RE.sub(" ", text)
        # Stripping the superscript affiliation markers leaves a wide gap where
        # each name ended, so split on those gaps before falling back to the
        # greedy two-token rule. Without this, "Alec Radford *1 Jong Wook Kim"
        # pairs across the boundary and yields "Jong Wook" + "Kim Chris".
        chunks = [c for c in re.split(r'\s{2,}', cleaned) if c.strip()] or [cleaned]
        for chunk in chunks:
            for name in _split_name_run(chunk):
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                names.append(name)
        if len(names) >= 15:
            break

    return names[:15]


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


# Ordered so the more specific phrase wins: "results and discussion" must be
# tested before "results", "related work" before "work". Keys match the
# vocabulary SECTION_ALIASES in ai/pdf_extraction.py already knows how to map.
_SECTION_ALIASES = (
    ("results and discussion", "results_and_discussion"),
    ("related work", "related_work"),
    ("prior work", "related_work"),
    ("threats to validity", "limitations"),
    ("future work", "future_work"),
    ("acknowledg", "acknowledgments"),
    ("bibliograph", "references"),
    ("reference", "references"),
    ("introduction", "introduction"),
    ("background", "background"),
    ("limitation", "limitations"),
    ("material", "materials"),
    ("methodolog", "method"),
    ("method", "method"),
    ("approach", "method"),
    ("architecture", "method"),
    ("experiment", "results"),
    ("evaluation", "results"),
    ("result", "results"),
    ("finding", "results"),
    ("discussion", "discussion"),
    ("conclusion", "conclusion"),
    ("abstract", "abstract"),
    ("appendix", "appendix"),
)


def _normalize_heading(text: str):
    """Map a raw heading to a canonical section key.

    Returns ``(key, is_subsection)``. The numbering prefix is stripped before
    matching so "2 Background" and "II. Background" both reach ``background``;
    a dotted prefix ("3.1") flags a subsection so the caller can fold it into
    its parent instead of emitting an unmatchable top-level key.
    """
    raw = text.strip()
    is_sub = False

    m = _HEAD_NUM_RE.match(raw)
    if m:
        is_sub = "." in m.group(1)
        raw = raw[m.end():]
    else:
        m = _ROMAN_NUM_RE.match(raw)
        if m:
            raw = raw[m.end():]

    lowered = raw.strip().lower()
    for needle, key in _SECTION_ALIASES:
        if needle in lowered:
            return key, is_sub
    return (lowered or text.strip().lower()), is_sub


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

    per_page = []
    for i, page in enumerate(doc):
        blocks, pw = _page_blocks(page)
        ordered = _reading_order(blocks, pw)
        per_page.append(ordered)
        if i == 0:
            page0_blocks, page0_pw = blocks, pw

    # Running headers repeat the paper title on every page. The heading test
    # below (font size >= 1.15 * median) fires on each repeat, so CLIP came back
    # with 111 "sections", most of them the same running header. Drop any short
    # block whose text recurs on three or more pages -- real section headings
    # appear once.
    page_counts = {}
    for ordered in per_page:
        for norm in {_running_key(b["text"]) for b in ordered}:
            page_counts[norm] = page_counts.get(norm, 0) + 1
    repeated = {t for t, n in page_counts.items() if n >= 3 and len(t) < 200}

    for ordered in per_page:
        ordered_all.extend(
            b for b in ordered
            if _running_key(b["text"]) not in repeated
        )

    # title / authors from page-1 header zone, else fallback
    header_zone, _ = _split_header_body(page0_blocks, page0_pw)
    page0_ordered = _reading_order(page0_blocks, page0_pw)

    title, authors, conf = _detect_title_authors(header_zone)
    if not title:
        title, authors = _fallback_title_authors(page0_ordered)
        conf = "low"
    if not authors:
        authors = _authors_from_zone(page0_ordered, title)
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

        # Guard against caption label bleed (e.g. "(c)", "(c) (d)")
        if re.match(r'^\(?[a-hA-H]\)?(\s*\(?[a-hA-H]\)?)*$', text):
            continue

        # Table rows and wrapped body lines also clear the font-size test in
        # papers that set tables in a larger face. Real headings do not end mid
        # word or mid sentence, and do not carry a row of measurements.
        if text[-1] in ',;:-–—':
            continue
        if len(words) > 3 and text.endswith('.') and not _HEAD_NUM_RE.match(text):
            continue
        if len(re.findall(r'\d+\.\d+', text)) >= 2:
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
        last_top_key = None
        for idx, (block_idx, b, title_text) in enumerate(headings):
            start_idx = block_idx + 1
            end_idx = headings[idx + 1][0] if idx + 1 < len(headings) else len(ordered_all)

            sec_text = "\n".join(ob["text"] for ob in ordered_all[start_idx:end_idx]).strip()
            if not sec_text:
                continue

            key, is_sub = _normalize_heading(title_text)
            if is_sub and last_top_key:
                # "3.1 Encoder and Decoder Stacks" is part of "3 Model
                # Architecture" -- fold it in so the caller sees one `method`
                # section rather than a dozen unmatched subsection keys.
                key = last_top_key
            elif not is_sub:
                last_top_key = key

            # Repeat headings (a section continued after a figure, or an
            # appendix reusing a name) append rather than overwrite, matching
            # the div-merge behaviour the TEI parser had.
            sections[key] = (sections[key] + "\n" + sec_text) if key in sections else sec_text

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
