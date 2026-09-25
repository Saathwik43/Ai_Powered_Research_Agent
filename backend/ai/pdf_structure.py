import fitz
import logging
import re

logger = logging.getLogger(__name__)

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


# --------------------------------------------------------------- figures -----
#
# Figure grounding (RP-12): the chat cites `[Figure 3]` and the reader renders
# the paper's own crop from the pdf.js document it already has, so what ships
# from here is coordinates and caption text -- never pixels.
#
# `get_drawings()` is the load-bearing half of the graphic scan. matplotlib and
# TikZ plots are vector operators, not embedded bitmaps, so on a paper like
# ResNet `get_images()` returns *nothing at all* (0 raster images against 1364
# drawings) while every figure in it is a real plot.

_CAPTION_RE = re.compile(
    r'^(Fig(?:ure)?|Table|Chart|Scheme)\s*\.?\s*(\d+|[IVXLC]+)\b',
    re.IGNORECASE,
)
# Case-insensitive because IEEE sets table captions in caps ("TABLE I"), which
# also means roman numerals have to be accepted alongside digits.
_CAPTION_WORDS = {
    "fig": "Figure", "figure": "Figure",
    "table": "Table", "chart": "Chart", "scheme": "Scheme",
}

# The label plus the opening sentences are what lets the model cite a figure;
# the tail of a long caption is description that would just crowd the prompt.
_CAPTION_CHARS = 600
# `figures` rides on `structure` through upload -> pdf_chats -> reload, so the
# list is bounded rather than however many captions a 60-page appendix has.
_MAX_FIGURES = 60

# A candidate region has to be big enough to be a figure rather than a stray
# rule or a logo. Both floors ended up well below the ~3% first tried, because
# real paper furniture is smaller than it sounds: ResNet's "Residual learning:
# a building block" is 2.4% of its page and its Table 2 is 0.9% and 33pt tall.
# The geometry does the real rejecting -- a cross-reference has prose directly
# above it, so its band collapses long before any size test is reached.
_MIN_FIG_AREA_FRAC = 0.008
_MIN_FIG_SIDE_PT = 24.0
# A rect this close to the full page is a background box or a scanned page
# image, not a figure.
_PAGE_COVER_FRAC = 0.85
# Walking a table body away from its caption: a vertical gap wider than this is
# the end of the table, not the next row. The step from the caption to the
# first row is its own, larger budget -- that gap is typographic whitespace and
# runs to 30pt in BERT, which stalled the walk before it started and lost the
# table entirely.
_TABLE_ROW_GAP_PT = 26.0
_TABLE_LEAD_GAP_PT = 44.0
# Walking graphics upwards from a caption: a gap wider than this means the run
# has ended and whatever is above belongs to something else.
#
# Left at 20pt on evidence: swept against the corpus at 20/30/40/50/72/100 and
# not one of the 81 located figures changes size at any value, so no real figure
# in it is bounded by this rule. Raise it only against a document that is.
_GRAPHIC_CLUSTER_GAP_PT = 20.0
# Prose bounds a figure band; plot furniture must not. A block qualifies as
# prose only if it is long *and* densely set: body paragraphs run about ten
# words to the line, while a plot's tick-label block can reach 18 words across
# 11 lines at under two words each.
_PROSE_WORDS = 12
_PROSE_WORDS_PER_LINE = 5.0
# A block has to be about this wide to mark a column edge; tick labels and
# panel letters are not gutters.
_COLUMN_BLOCK_FRAC = 0.25
# How far a piece of the plot's own lettering may sit from the rest of the
# figure and still belong to it -- a panel heading above the drawing, an axis
# title below it.
_PLOT_TEXT_GAP_PT = 12.0
# A table candidate overlapping an already-claimed figure by more than this is
# that figure, seen from the wrong side of a caption.
_CLAIM_OVERLAP_FRAC = 0.5
# A caption often extracts as several blocks -- CLIP's Figure 1 caption breaks
# after its first line -- so the continuation is stitched back on while the gap
# stays this small and the block stays inside the caption's own column.
_CAPTION_LINE_GAP_PT = 8.0
# A wrapped caption line resumes at the caption's own left margin, within a
# rounding error. A table's first row does not -- Transformer's Table 4 body
# starts 43pt in -- and that one number is what keeps the stitcher from eating
# the table it is supposed to be labelling.
_CAPTION_ALIGN_PT = 3.0
# Breathing room around the final crop, so a plot's outermost stroke is not
# shaved off by half-point rounding.
_FIG_PAD_PT = 3.0


def _caption_label(text: str):
    """``("Figure 3", "figure")`` for a caption-looking block, else ``(None, None)``."""
    m = _CAPTION_RE.match(text.strip())
    if not m:
        return None, None
    word = _CAPTION_WORDS.get(m.group(1).lower().rstrip("."))
    if not word:
        return None, None
    num = m.group(2)
    return f"{word} {num if num.isdigit() else num.upper()}", (
        "table" if word == "Table" else "figure"
    )


def _tidy_caption(text: str) -> str:
    caption = re.sub(r'\s+', ' ', text).strip()
    if len(caption) <= _CAPTION_CHARS:
        return caption
    cut = caption[:_CAPTION_CHARS]
    space = cut.rfind(" ")
    return (cut[:space] if space > _CAPTION_CHARS * 0.6 else cut).rstrip() + "…"


def _merge_caption(index, blocks):
    """The caption at ``blocks[index]``, plus the lines it was split across.

    PyMuPDF breaks a caption into several blocks often enough that taking only
    the first one truncates mid-sentence ("...and a linear classifier to
    predict"), which then reaches both the prompt and the reader's screen. A
    following block is part of the caption while it sits directly underneath
    and inside the same column; the next paragraph is 27pt down, and the next
    caption announces itself.

    Returns ``(text, rect_like)`` -- the rect spans every block taken, so a
    table caption's band starts below the whole caption rather than inside it.
    """
    head = blocks[index]
    parts = [head["text"]]
    x0, y0, x1, y1 = head["x0"], head["y0"], head["x1"], head["y1"]

    for block in blocks[index + 1:]:
        if block["y0"] - y1 > _CAPTION_LINE_GAP_PT:
            break
        if abs(block["x0"] - head["x0"]) > _CAPTION_ALIGN_PT:
            break
        if _caption_label(block["text"])[0]:
            break
        parts.append(block["text"])
        x0, y0 = min(x0, block["x0"]), min(y0, block["y0"])
        x1, y1 = max(x1, block["x1"]), max(y1, block["y1"])
        if sum(len(p) for p in parts) > _CAPTION_CHARS * 2:
            break

    return " ".join(parts), {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _text_rects(page):
    """Text blocks with full bboxes and line counts, sorted in page order.

    ``_page_blocks`` is not reusable here: it keeps only ``x0``/``y0``/``width``
    and filters blocks on their content, and figure banding needs the raw
    geometry of *every* text block -- including the running header, which is
    exactly the block a figure at the top of a page sits below.
    """
    out = []
    for b in page.get_text("dict").get("blocks", []):
        lines = b.get("lines")
        if not lines:
            continue
        text = " ".join(
            s.get("text", "") for l in lines for s in l.get("spans", [])
        ).strip()
        if not text:
            continue
        # rotated arXiv sidebar stamp: never a caption, never a neighbour
        if any(abs(l.get("dir", (1, 0))[0]) < 0.9 for l in lines):
            continue
        x0, y0, x1, y1 = b["bbox"]
        out.append({
            "text": text, "lines": len(lines),
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        })
    return sorted(out, key=lambda b: (b["y0"], b["x0"]))


def _is_prose(block) -> bool:
    """Body text, as opposed to a plot's own lettering.

    Word count alone was not enough: a matplotlib axis block on a CLIP page
    carries 18 words, which cleared a 12-word floor and dragged the band top
    down to nothing. Words *per line* separates the two cleanly -- prose sets
    about ten to the line, plot furniture one or two.
    """
    words = len(block["text"].split())
    if words < _PROSE_WORDS:
        return False
    return words / max(1, block["lines"]) >= _PROSE_WORDS_PER_LINE


def _centre_in(band, rect) -> bool:
    """Membership by centre point: a rect that starts inside the band but runs
    off the page does not drag the union out with it."""
    return band.contains(
        fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    )


def _graphic_rects(page, page_rect):
    """Every vector and raster graphic extent on the page, page-sized boxes out."""
    page_area = max(1.0, page_rect.width * page_rect.height)
    raw = []

    for info in page.get_images(full=True):
        try:
            raw.extend(page.get_image_rects(info[0]))
        except Exception:
            continue
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is not None:
            raw.append(rect)

    kept = []
    for rect in raw:
        clipped = fitz.Rect(rect) & page_rect
        if clipped.is_empty or clipped.width <= 0 or clipped.height <= 0:
            continue
        if clipped.get_area() / page_area >= _PAGE_COVER_FRAC:
            continue
        kept.append(clipped)
    return kept


def _page_is_scan(page, page_rect) -> bool:
    """One image covering the whole page: a photograph of a page.

    Any crop taken from it would be an arbitrary rectangle of a picture rather
    than a figure, so the caller lists the captions and ships no bbox at all.
    """
    page_area = max(1.0, page_rect.width * page_rect.height)
    for info in page.get_images(full=True):
        try:
            rects = page.get_image_rects(info[0])
        except Exception:
            continue
        for rect in rects:
            if (fitz.Rect(rect) & page_rect).get_area() / page_area >= _PAGE_COVER_FRAC:
                return True
    return False


def _column_bounds(caption, blocks, page_rect):
    """The x-range a figure may occupy: its caption's column, or the full text
    width when the caption is full-width.

    The gutter is read off the page rather than assumed to be the midline. In a
    two-column paper the right column starts about three points past the middle,
    so a midline split -- with any slack at all for figures that overhang --
    pulled the *other* column's paragraphs in as neighbours, and those bounded
    the band down to nothing. Where there is no gutter (a single-column paper)
    both branches fall through to the full width, which is the right answer.
    """
    pw = page_rect.width
    if (caption["x1"] - caption["x0"]) >= pw * 0.55:
        return 0.0, pw

    wide = [
        b for b in blocks
        if b is not caption and (b["x1"] - b["x0"]) >= pw * _COLUMN_BLOCK_FRAC
    ]
    if (caption["x0"] + caption["x1"]) / 2 < pw / 2:
        starts = [b["x0"] for b in wide if b["x0"] >= caption["x1"] - 2]
        return 0.0, (min(starts) - 1.0) if starts else pw
    ends = [b["x1"] for b in wide if b["x1"] <= caption["x0"] + 2]
    return ((max(ends) + 1.0) if ends else 0.0), pw


def _neighbours(caption, blocks, x0, x1):
    return [
        b for b in blocks
        if b is not caption and b["x1"] > x0 and b["x0"] < x1
    ]


def _figure_band(caption, blocks, x0, x1, furniture=()):
    """A figure sits above its caption, below the prose that precedes it.

    Deliberately not "below the nearest text block": every tick label, axis
    title and legend entry inside a plot is its own text block, and the nearest
    of those is a few points above the caption -- which collapsed the band and
    lost every vector figure in the paper (ResNet: 4 of 5, all real plots).

    The running header is deliberately *not* a bound here. It looked like a
    free win -- a figure at the top of a page has nothing else above it -- but
    `_running_furniture` flags any text repeating on three or more pages, and
    in a paper full of plots that catches axis labels and legend entries. Using
    it as an upper bound put the band top just above the caption and cost 14 of
    85 figures across the corpus.
    """
    top = 0.0
    for b in _neighbours(caption, blocks, x0, x1):
        if b["y1"] > caption["y0"] + 1:
            continue
        if _is_prose(b) or _caption_label(b["text"])[0]:
            top = max(top, b["y1"])
    return fitz.Rect(x0, top, x1, caption["y0"])


def _table_band(caption, blocks, x0, x1):
    """A table caption usually sits *above* its content, so this band runs down.

    It ends at the first real paragraph gap, the first prose block, or the next
    caption: table rows are tightly spaced, so neither the text that resumes
    after the table nor the table stacked below it can be told apart by the gap
    alone. Stopping at the next caption is what keeps three stacked tables in
    one column from all resolving to the first one.
    """
    below = sorted(
        (b for b in _neighbours(caption, blocks, x0, x1) if b["y0"] >= caption["y1"] - 1),
        key=lambda b: b["y0"],
    )
    cursor = bottom = caption["y1"]
    started = False
    for b in below:
        budget = _TABLE_ROW_GAP_PT if started else _TABLE_LEAD_GAP_PT
        if b["y0"] - cursor > budget or _is_prose(b):
            break
        if _caption_label(b["text"])[0]:
            break
        cursor = max(cursor, b["y1"])
        bottom = cursor
        started = True
    return fitz.Rect(x0, caption["y1"], x1, max(bottom, caption["y1"]))


def _graphic_cluster(graphics, band):
    """The contiguous run of graphics sitting directly against the caption.

    Walking up from the caption and stopping at the first real gap is what keeps
    a figure from annexing the rule under a running header, or the tail of a
    plot belonging further up the column.
    """
    inside = sorted(
        (r for r in graphics if _centre_in(band, r)),
        key=lambda r: r.y1,
        reverse=True,
    )
    if not inside:
        return None
    cluster = fitz.Rect(inside[0])
    for rect in inside[1:]:
        if cluster.y0 - rect.y1 > _GRAPHIC_CLUSTER_GAP_PT:
            break
        cluster |= rect
    return cluster


def _with_plot_text(region, blocks, band, furniture):
    """Grow a region to cover the plot's own lettering.

    Axis labels, legends and panel headings are drawn as text, so they fall
    outside every rect ``get_drawings()`` reports; cropping to the strokes alone
    slices the y-axis label off a matplotlib figure and the "(1) Contrastive
    pre-training" heading off the top of CLIP's Figure 1.

    Growth is iterative because those pieces chain: the heading is out of reach
    of the drawing but within reach of the label above it. It stays anchored
    because only non-prose blocks inside the band qualify, each has to be near
    the region **both** vertically and horizontally, and the running header is
    excluded outright -- otherwise a figure at the top of a page walks up into
    the paper's own title.
    """
    candidates = []
    for b in blocks:
        if _is_prose(b) or _running_key(b["text"]) in furniture:
            continue
        rect = fitz.Rect(b["x0"], b["y0"], b["x1"], b["y1"]) & band
        if not rect.is_empty and rect.width > 0 and rect.height > 0:
            candidates.append(rect)

    grown = fitz.Rect(region)
    growing = True
    while growing:
        growing = False
        held = []
        for rect in candidates:
            near = (
                rect.y1 >= grown.y0 - _PLOT_TEXT_GAP_PT
                and rect.y0 <= grown.y1 + _PLOT_TEXT_GAP_PT
                and rect.x1 >= grown.x0 - _PLOT_TEXT_GAP_PT
                and rect.x0 <= grown.x1 + _PLOT_TEXT_GAP_PT
            )
            if near:
                grown |= rect
                growing = True
            else:
                held.append(rect)
        candidates = held
    return grown


def _table_region(band, graphics, blocks, furniture):
    """A table's extent: its rules together with its cells.

    The text is the load-bearing half -- booktabs draws three horizontal rules
    and nothing else, so cropping to the strokes would keep the rules and lose
    every number between them. Which is also why the running header has to be
    excluded by name here: it is a text block like any other, it is not prose
    by the words-per-line test, and a table at the top of a CLIP page sits
    directly beneath it -- so the crop came out with the paper's own title
    pasted across the top of the table.
    """
    union = None

    def add(rect):
        nonlocal union
        clipped = fitz.Rect(rect) & band
        if clipped.is_empty or clipped.width <= 0 or clipped.height <= 0:
            return
        union = clipped if union is None else (union | clipped)

    for rect in graphics:
        if _centre_in(band, rect):
            add(rect)
    for b in blocks:
        if _running_key(b["text"]) in furniture:
            continue
        rect = fitz.Rect(b["x0"], b["y0"], b["x1"], b["y1"])
        if _centre_in(band, rect):
            add(rect)

    return union


def _region_ok(region, page_rect) -> bool:
    if min(region.width, region.height) < _MIN_FIG_SIDE_PT:
        return False
    page_area = max(1.0, page_rect.width * page_rect.height)
    return region.get_area() / page_area >= _MIN_FIG_AREA_FRAC


def _claimed_by_other(region, claimed) -> bool:
    """Is this candidate really the figure next door?

    ResNet sets its table captions *below* the table, so walking downwards from
    "Table 1." lands squarely on Figure 4's plot -- which Figure 4 has already
    claimed from above, where the caption-follows-content rule is unambiguous.
    Letting figures claim first and testing tables against them settles the
    direction per instance, instead of guessing a house style per paper.
    """
    for taken in claimed:
        overlap = (fitz.Rect(region) & taken).get_area()
        smaller = min(region.get_area(), taken.get_area())
        if smaller > 0 and overlap / smaller >= _CLAIM_OVERLAP_FRAC:
            return True
    return False


def _pad(region, page_rect):
    return (
        fitz.Rect(region)
        + (-_FIG_PAD_PT, -_FIG_PAD_PT, _FIG_PAD_PT, _FIG_PAD_PT)
    ) & page_rect


def _figure_region(caption, blocks, graphics, page_rect, furniture, claimed):
    """A figure's crop. Always above its caption -- that one is not a convention."""
    x0, x1 = _column_bounds(caption, blocks, page_rect)
    band = _figure_band(caption, blocks, x0, x1, furniture)
    if band.height < _MIN_FIG_SIDE_PT:
        return None
    cluster = _graphic_cluster(graphics, band)
    if cluster is None:
        return None
    region = _pad(_with_plot_text(cluster, blocks, band, furniture), page_rect)
    if not _region_ok(region, page_rect) or _claimed_by_other(region, claimed):
        return None
    return region


def _table_candidates(caption, blocks, graphics, page_rect, claimed, furniture):
    """Both readings of a table caption, as ``{side: (gap, region)}``.

    A table may sit on either side of its caption: LaTeX convention puts the
    caption above, but BERT prints all of its tables the other way round and
    ResNet mixes the two on a single page. Which side is right is not decided
    here -- both are costed and the caller chooses.
    """
    x0, x1 = _column_bounds(caption, blocks, page_rect)
    options = {}
    for side, band in (
        ("up", _figure_band(caption, blocks, x0, x1, furniture)),
        ("down", _table_band(caption, blocks, x0, x1)),
    ):
        if band.height < _MIN_FIG_SIDE_PT:
            continue
        found = _table_region(band, graphics, blocks, furniture)
        if found is None:
            continue
        region = _pad(found, page_rect)
        if not _region_ok(region, page_rect) or _claimed_by_other(region, claimed):
            continue
        gap = (
            caption["y0"] - region.y1 if side == "up"
            else region.y0 - caption["y1"]
        )
        options[side] = (max(0.0, gap), region)
    return options


def _table_side(per_page) -> str:
    """Which side of its caption this paper's tables sit on.

    Proximity alone cannot answer it: on BERT's page 7 the table above the
    caption is 15.3pt away and the one below is 15.2pt, so a nearest-wins rule
    assigned every table on the page to its neighbour's caption -- the model
    would cite Table 2 and the reader would see Table 3's numbers, which is
    worse than showing no crop at all.

    The document does know, though. Captions where only one side yields a region
    at all -- a table at the top of a column, one with nothing but prose below --
    are unambiguous, so they vote, and the majority settles the rest. A paper
    that never offers an unambiguous case falls back to the LaTeX convention.
    """
    votes = {"up": 0, "down": 0}
    for record in per_page:
        for options in record["tables"].values():
            if len(options) == 1:
                votes[next(iter(options))] += 1
    if votes["up"] == votes["down"]:
        return "down"
    return "up" if votes["up"] > votes["down"] else "down"


def _running_furniture(per_page_blocks) -> set:
    """Text that repeats across the document: running headers, page numbers.

    Same rule ``extract_structure`` already uses to stop a repeated title being
    read as a section heading -- reused rather than re-invented, because it is
    the same problem seen from the other side: furniture that sits right next
    to a figure without belonging to it.
    """
    counts = {}
    for blocks in per_page_blocks:
        for key in {_running_key(b["text"]) for b in blocks}:
            counts[key] = counts.get(key, 0) + 1
    return {key for key, n in counts.items() if n >= 3 and len(key) < 200}


def _outranks(candidate, incumbent) -> bool:
    """Which of two blocks claiming the same label is the real one.

    Papers say "Figure 3" many times; only one of those blocks has a graphic
    banded against it, and among survivors the larger region is the figure
    rather than a cross-reference that happened to clear the size floor.
    """
    if bool(candidate["bbox"]) != bool(incumbent["bbox"]):
        return bool(candidate["bbox"])
    if candidate["bbox"] and incumbent["bbox"]:
        def area(box):
            return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        return area(candidate["bbox"]) > area(incumbent["bbox"])
    return len(candidate["caption"]) > len(incumbent["caption"])


def _figures_from_doc(doc) -> list:
    """Caption-anchored figure and table inventory for an open document.

    Three passes, because each one needs the answer from the last: figures claim
    their regions first (their side is unambiguous), the leftover table readings
    then vote on where this paper puts its table captions, and only then is
    every table assigned.
    """
    pages = [(pno, page, _text_rects(page)) for pno, page in enumerate(doc)]
    furniture = _running_furniture([blocks for _, _, blocks in pages])

    per_page = []
    for pno, page, blocks in pages:
        captions = []
        for index, block in enumerate(blocks):
            label, kind = _caption_label(block["text"])
            if not label:
                continue
            text, rect = _merge_caption(index, blocks)
            captions.append((label, kind, rect, text))
        if not captions:
            continue

        page_rect = page.rect
        # Resolved once per page: both walk the whole operator list.
        scanned = _page_is_scan(page, page_rect)
        graphics = [] if scanned else _graphic_rects(page, page_rect)

        claimed = []
        regions = {}
        if not scanned:
            for index, (label, kind, caption, _text) in enumerate(captions):
                if kind != "figure":
                    continue
                region = _figure_region(
                    caption, blocks, graphics, page_rect, furniture, claimed
                )
                if region is not None:
                    claimed.append(region)
                    regions[index] = region

        tables = {}
        if not scanned:
            for index, (label, kind, caption, _text) in enumerate(captions):
                if kind == "table":
                    tables[index] = _table_candidates(
                        caption, blocks, graphics, page_rect, claimed, furniture
                    )

        per_page.append({
            "pno": pno, "rect": page_rect, "captions": captions,
            "claimed": claimed, "regions": regions, "tables": tables,
        })

    prefer = _table_side(per_page)

    found = {}
    for record in per_page:
        claimed = record["claimed"]
        for index, options in record["tables"].items():
            ordered = sorted(
                options.items(),
                key=lambda kv: (kv[0] != prefer, kv[1][0]),
            )
            for _, (_, region) in ordered:
                # Re-tested, not trusted: an earlier table on this page may have
                # claimed this ink since the options were costed.
                if not _claimed_by_other(region, claimed):
                    claimed.append(region)
                    record["regions"][index] = region
                    break

        for index, (label, kind, caption, text) in enumerate(record["captions"]):
            region = record["regions"].get(index)
            entry = {
                "label": label,
                "kind": kind,
                "caption": _tidy_caption(text),
                "page": record["pno"] + 1,
                # None is a first-class answer: a scan, a code listing, or a
                # caption with no graphic banded against it is listed for the
                # prompt and rendered caption-only. Never a guessed rectangle.
                "bbox": (
                    [round(v, 2) for v in (region.x0, region.y0, region.x1, region.y1)]
                    if region is not None else None
                ),
                "page_size": {
                    "width": round(record["rect"].width, 2),
                    "height": round(record["rect"].height, 2),
                },
            }
            incumbent = found.get(label)
            if incumbent is None or _outranks(entry, incumbent):
                found[label] = entry

    ordered = sorted(
        found.values(),
        key=lambda f: (f["page"], f["bbox"][1] if f["bbox"] else 0.0),
    )
    return ordered[:_MAX_FIGURES]


def extract_figures(file_bytes: bytes) -> list:
    """``[{label, kind, caption, page, bbox, page_size}, ...]`` for PDF bytes.

    ``bbox`` is ``[x0, y0, x1, y1]`` in PDF points with the origin at the top
    left of the page, matching what pdf.js's viewport reports at scale 1;
    ``page_size`` travels with it so the reader can rescale if the two ever
    disagree about the page box.
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        return _figures_from_doc(doc)
    finally:
        doc.close()


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

    # Figure inventory (RP-12). Wrapped because a malformed content stream can
    # raise deep inside the drawing scan, and a paper whose figures cannot be
    # located is still a paper whose title, abstract and sections extracted
    # fine -- losing those to a figure crash would be the worse trade.
    try:
        figures = _figures_from_doc(doc)
    except Exception as exc:
        logger.warning("Figure extraction failed, continuing without figures: %s", exc)
        figures = []

    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "sections": sections,
        "figures": figures,
        "confidence": confidence,
    }
