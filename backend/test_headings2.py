import urllib.request
from ai.pdf_structure import extract_structure
import fitz

url = "https://arxiv.org/pdf/2209.11154.pdf"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req) as response:
    pdf_bytes = response.read()

res = extract_structure(pdf_bytes)
print("Keys:", list(res["sections"].keys()))

# Let's also debug what it considered headings:
import sys
sys.path.append('.')
from ai.pdf_structure import _page_blocks, _reading_order, _split_header_body
import statistics
import re

doc = fitz.open(stream=pdf_bytes, filetype="pdf")
ordered_all = []
header_zone = []
for i, page in enumerate(doc):
    blocks, pw = _page_blocks(page)
    ordered = _reading_order(blocks, pw)
    ordered_all.extend(ordered)
    if i == 0:
        header_zone, _ = _split_header_body(blocks, pw)

body_blocks = [b for b in ordered_all if b not in header_zone]
median_size = statistics.median(b["size"] for b in body_blocks) if body_blocks else 10
_HEADING_REGEX = re.compile(r'^(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.?|[A-Z]\.)\s+[A-Z]')
_KEYWORD_HEADING_REGEX = re.compile(r'^(?:Introduction|Method(?:s)?|Results|Discussion|Conclusion(?:s)?|References|Materials|Acknowledgments?)$', re.IGNORECASE)

print(f"Median size: {median_size}")
for b in ordered_all:
    text = b["text"].strip()
    words = text.split()
    if len(words) >= 12 or not text: continue
    size = b["size"]
    is_head = False
    if size >= 1.15 * median_size: is_head = True
    elif _HEADING_REGEX.match(text) or _KEYWORD_HEADING_REGEX.match(text): is_head = True
    if is_head:
        print(f"HEADING: {text!r} (size: {size:.2f})")
