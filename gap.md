## Publisher template report

**Structure comparison:**

| | IEEE | ACM (sigconf) | Springer LNCS | Elsevier |
|---|---|---|---|---|
| Layout | 2-col, A4/Letter | 2-col (single-col for review) | **1-col**, A4 | 1-col |
| Body font | Times 10pt | Libertine 9-10pt | 10pt | 10pt |
| Section style | Roman numerals (I. INTRODUCTION) | Numbered | Numbered, max 2 sub-levels | Numbered |
| Abstract | ~150-250 words | ~150-250 words + CCS concepts + keywords | ≤250 words, 9pt | Structured + Highlights (3-5 bullets) |
| Refs | Numbered `[1]`, IEEEtran style | ACM Reference Format | `splncs04.bst` numbered | Numbered or author-year (`elsarticle`) |
| Page limit | 6-8pp typical | Varies by venue | 12-16pp incl. refs | Varies, often no hard limit |
| Class file | `IEEEtran.cls` | `acmart.cls` | `llncs.cls` | `elsarticle.cls` |
| Submission | IEEE PDF eXpress + portal | TAPS (auto single→double col) | Editorial Manager / EquinOCS | Editorial Manager |
| Special sections | none extra | CCS Concepts, ACM Ref Format block | none extra | **Highlights**, Graphical Abstract |

**Universal core** (every publisher wants this regardless of format):
Title → Authors/Affiliations → Abstract → Keywords → Introduction → Related Work → Methodology → Results/Experiments → Discussion → Conclusion → Acknowledgments (optional) → References

**What "publish-ready" actually requires beyond content** — this is the gap:
1. Exact class file compilation (`IEEEtran`/`acmart`/`llncs`/`elsarticle`) — cosmetic formatting alone (fonts/margins in Word) fails IEEE PDF eXpress and similar automated checks.
2. Correct bib style per publisher (`splncs04.bst` ≠ IEEEtran refs ≠ ACM Reference Format) — same reference list, different rendering rules.
3. Publisher-specific extras: ACM's CCS Concepts + keywords block, Elsevier's Highlights + Graphical Abstract, IEEE's Index Terms.
4. Copyright/disclosure blocks — IEEE copyright notice footer, Springer competing-interest statement, ACM rights block.

**Current state of your repo:** manuscript generation produces plain markdown/LaTeX-math-inline text only — no `.tex` class-file compilation, no publisher-specific structure switch, no bib-style mapping. "Direct-edit-then-publish" isn't possible yet; output would need manual reformatting per venue today.

**To actually deliver "produce exact structure, user edits then submits":**
1. Add a **template selector** (IEEE / ACM / Springer LNCS / Elsevier) to manuscript gen.
2. Store 4 LaTeX skeleton files (`IEEEtran`, `acmart`-sigconf, `llncs`, `elsarticle`) as templates; generation fills sections into placeholders, not free text.
3. Map citations to the right `.bst`/reference style per selection.
4. Export as compilable `.tex` + `.bib` (not just markdown) — needs a LaTeX compile step (e.g. via `tectonic`/`pdflatex` in a sandboxed job) to give a true camera-ready PDF preview.

## Publisher-template feature: implementation plan

**Current state confirmed:** manuscript content stored as `Dict[str, Any]` keyed by section (`main.py:156`), markdown text per section already includes LaTeX math syntax (`$...$`) natively from the generation prompt. `citation_format.py` is deterministic (no LLM) and already supports ieee/apa/chicago/oxford — reusable directly, no rework needed there. Zero existing LaTeX/template infrastructure.

### Phase 1 — Backend template engine (`backend/ai/latex_export.py`)

Four skeleton `.tex` files as string templates with placeholders (`{{TITLE}}`, `{{AUTHORS}}`, `{{ABSTRACT}}`, `{{SECTIONS}}`, `{{BIBLIOGRAPHY}}`):
- `IEEEtran` — two-column, numbered refs
- `acmart` (sigconf) — needs extra CCS Concepts + ACM Reference Format block
- `llncs` — single-column, `\inst{}` affiliation syntax, `splncs04` bib style
- `elsarticle` — needs Highlights block (3-5 bullets, separate from abstract)

Core function `export_manuscript(topic, content_dict, venue, references) -> (tex_str, bib_str)`:
1. Markdown→LaTeX per section: `**bold**`→`\textbf{}`, `- item`→`\item`, headers stripped (sections already come headerless per prompt rule #2). Math stays as-is (already LaTeX).
2. Mermaid diagram blocks → flagged as `% TODO: insert figure` placeholder + original chart data preserved in a comment (mermaid doesn't compile in LaTeX — needs manual figure re-creation, document this limitation to the user explicitly rather than silently dropping).
3. References → `.bib` entries built from existing paper metadata (title/authors/year/venue already in DB), `\bibliographystyle` set per venue (`IEEEtran`/`ACM-Reference-Format`/`splncs04`/`elsarticle-num`).
4. Venue-specific extras injected: ACM CCS Concepts (skip or leave placeholder — needs classification, out of scope v1), Elsevier Highlights (skip — needs distinct short bullets, out of scope v1, flag as manual-fill placeholder).

### Phase 2 — API

`POST /api/manuscript/export-latex` — payload `{topic, venue}`. Pulls saved manuscript, calls `export_manuscript()`, zips `paper.tex` + `references.bib` + `README.txt` (points to official CTAN/Overleaf class-file link — don't embed publisher class files, they're maintained externally and redistribution adds staleness risk). Returns download link, same pattern as existing PDF endpoints (GridFS or direct stream).

### Phase 3 — Frontend

Venue dropdown (IEEE/ACM/Springer LNCS/Elsevier) in `ManuscriptBuilder.jsx`, "Export LaTeX" button → hits new endpoint → downloads zip. Reuses existing download-button pattern already in the file.

### Explicitly out of scope v1 (flag, don't silently skip)
- Server-side PDF compile (`pdflatex`/`tectonic`) — needs a LaTeX toolchain in the Render container, meaningful image-size/cold-start cost. Ship `.tex` only; user compiles in Overleaf (standard workflow anyway, every publisher's own docs point there).
- CCS Concepts / Highlights auto-generation — needs a real classification step, not string templating. Placeholder + manual fill for v1.
- Mermaid→native figure conversion — flag as manual step, don't fake it.

