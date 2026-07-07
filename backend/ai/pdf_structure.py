import fitz
import re

def extract_structure(file_bytes: bytes) -> dict:
    """
    Deterministically extract document structure (title, authors, abstract, sections)
    using PyMuPDF font heuristics and regex.
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    
    title = ""
    authors = []
    abstract = ""
    sections = {}
    
    # Page 1 heuristic for title and authors
    if len(doc) > 0:
        page = doc[0]
        blocks = page.get_text("dict").get("blocks", [])
        
        # find largest font block for title
        largest_size = 0
        title_block = None
        for b in blocks:
            if "lines" in b:
                for l in b.get("lines", []):
                    for s in l.get("spans", []):
                        if s.get("size", 0) > largest_size:
                            largest_size = s["size"]
                            title_block = b
        
        if title_block:
            title_text = " ".join([s.get("text", "") for l in title_block.get("lines", []) for s in l.get("spans", [])])
            title = title_text.strip()
            
            # find next block for authors
            try:
                title_idx = blocks.index(title_block)
                if title_idx + 1 < len(blocks):
                    author_block = blocks[title_idx + 1]
                    if "lines" in author_block:
                        raw_authors = " ".join([s.get("text", "") for l in author_block.get("lines", []) for s in l.get("spans", [])])
                        raw_authors = raw_authors.strip()
                        
                        # strip superscript digit markers and asterisks
                        raw_authors = re.sub(r'[\d\*]', '', raw_authors)
                        # split on comma or 'and'
                        author_list = [a.strip() for a in re.split(r',|\band\b', raw_authors) if a.strip()]
                        authors = author_list
            except ValueError:
                pass

    # Extract abstract and sections from full text
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
        
    abstract_match = re.search(r'(?i)\bAbstract\b(.*?)(?=\n(?:1\.?\s+Introduction|Keywords|I\.?\s+Introduction))', full_text, re.DOTALL)
    if abstract_match:
        abstract = abstract_match.group(1).strip()
        
    # extract sections (best effort)
    section_pattern = re.compile(r'^(?:(?:\d+\.?\s+[A-Z][a-zA-Z\s]+)|(?:Introduction|Method(?:s)?|Results|Discussion|Conclusion|References))\b', re.MULTILINE | re.IGNORECASE)
    
    matches = list(section_pattern.finditer(full_text))
    if matches:
        for i, match in enumerate(matches):
            sec_name = full_text[match.start():match.end()].strip()
            start_idx = match.end()
            end_idx = matches[i+1].start() if i+1 < len(matches) else len(full_text)
            sec_text = full_text[start_idx:end_idx].strip()
            
            key = sec_name.lower()
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
    else:
        sections = {"full_text": full_text}
        
    return {
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "sections": sections
    }
