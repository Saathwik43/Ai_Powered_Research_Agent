"""
Tests for figure grounding in PDF analysis (RP-12).

**What these tests are and are not.** They pin the contract and the decision
rules: the shape `extract_figures` emits, the cases where it must refuse to
emit a box, how a caption is matched to the content on one side of it, and how
the figure index reaches the prompt. They build their PDFs with `fitz`, so the
geometry in them is whatever this file drew -- which means they can tell you
that a caption with prose above it yields no crop, but they can NOT tell you
that a crop of a real paper contains the real figure. That second question was
answered by rendering crops from arXiv PDFs (ResNet, Transformer, BERT, CLIP)
and looking at them, and by cross-checking PyMuPDF's coordinates against
pdf.js's viewport on 46 text spans (worst disagreement 0.01pt) -- because the
same trap took the LaTeX export: a green suite over a broken artefact.
"""

import fitz
import pytest

from ai.pdf_analysis import (
    _DOCUMENT_SAFETY_RULE,
    _build_paper_context,
    _figures_block,
)
from ai.pdf_structure import extract_figures, extract_structure

PAGE_W, PAGE_H = 612.0, 792.0
PROSE = (
    "This paragraph exists so that the band above a caption is bounded by real "
    "body text rather than running to the top of the page, which is what the "
    "extractor uses to tell a figure from a cross-reference in running prose. "
)


def _doc():
    return fitz.open()


def _page(doc):
    return doc.new_page(width=PAGE_W, height=PAGE_H)


def _prose(page, rect):
    page.insert_textbox(rect, PROSE * 2, fontsize=9)


def _drawn_figure(page, rect):
    """Vector content -- the case `get_images()` cannot see."""
    page.draw_rect(rect, color=(0, 0, 0), width=1)
    page.draw_line(fitz.Point(rect.x0 + 6, rect.y1 - 6),
                   fitz.Point(rect.x1 - 6, rect.y0 + 6), color=(0, 0, 1))
    page.draw_circle(fitz.Point(rect.x0 + 30, rect.y0 + 30), 12, color=(1, 0, 0))


def _caption(page, point, text, fontsize=9):
    page.insert_text(point, text, fontsize=fontsize)


def _bytes(doc):
    return doc.tobytes()


def _by_label(figures):
    return {f["label"]: f for f in figures}


class TestInventoryShape:
    """`structure["figures"]` is the whole wire format: no pixels, no endpoint."""

    def test_emits_the_documented_fields(self):
        doc = _doc()
        page = _page(doc)
        _prose(page, fitz.Rect(60, 60, 550, 160))
        _drawn_figure(page, fitz.Rect(80, 200, 500, 420))
        _caption(page, fitz.Point(80, 440), "Figure 1: A drawn figure.")

        figures = extract_figures(_bytes(doc))
        assert len(figures) == 1
        figure = figures[0]

        assert figure["label"] == "Figure 1"
        assert figure["kind"] == "figure"
        assert figure["page"] == 1
        assert "A drawn figure." in figure["caption"]
        assert figure["page_size"] == {"width": PAGE_W, "height": PAGE_H}

        x0, y0, x1, y1 = figure["bbox"]
        # The crop covers the drawing and stops short of the caption.
        assert x0 < 90 and x1 > 490
        assert y0 < 210 and 415 < y1 < 440

    def test_structure_carries_figures(self):
        doc = _doc()
        page = _page(doc)
        _prose(page, fitz.Rect(60, 60, 550, 160))
        _drawn_figure(page, fitz.Rect(80, 200, 500, 420))
        _caption(page, fitz.Point(80, 440), "Figure 1: Carried on structure.")

        structure = extract_structure(_bytes(doc))
        assert [f["label"] for f in structure["figures"]] == ["Figure 1"]

    def test_vector_only_page_is_found(self):
        """`get_images()` returns nothing here; `get_drawings()` is the half
        that works. Real evidence: ResNet has 0 raster images and 1364
        drawings, and every figure in it is a plot."""
        doc = _doc()
        page = _page(doc)
        _prose(page, fitz.Rect(60, 60, 550, 160))
        _drawn_figure(page, fitz.Rect(80, 200, 500, 420))
        _caption(page, fitz.Point(80, 440), "Figure 1: Vector only.")

        assert page.get_images(full=True) == []
        assert _by_label(extract_figures(_bytes(doc)))["Figure 1"]["bbox"] is not None


class TestRefusesToGuess:
    """A wrong crop is worse than no crop: the reader trusts what it shows."""

    def test_cross_reference_in_prose_gets_no_box(self):
        """"Figure 4 shows..." opening a paragraph is a mention, not a caption.
        Prose directly above it collapses the band, so nothing is offered."""
        doc = _doc()
        page = _page(doc)
        _prose(page, fitz.Rect(60, 60, 550, 300))
        _caption(page, fitz.Point(60, 320),
                 "Figure 4 shows that the effect persists across every setting.")
        _prose(page, fitz.Rect(60, 340, 550, 600))

        figures = _by_label(extract_figures(_bytes(doc)))
        assert figures["Figure 4"]["bbox"] is None
        assert figures["Figure 4"]["caption"].startswith("Figure 4 shows")

    def test_caption_with_no_graphic_gets_no_box(self):
        """CLIP's Figure 3 is a pseudocode listing -- a real caption over text.
        Listing it without a box is the honest answer."""
        doc = _doc()
        page = _page(doc)
        _prose(page, fitz.Rect(60, 60, 550, 200))
        _caption(page, fitz.Point(60, 400), "Figure 3: Numpy-like pseudocode.")

        figures = _by_label(extract_figures(_bytes(doc)))
        assert figures["Figure 3"]["bbox"] is None

    def test_scanned_page_degrades_to_captions_only(self):
        """A page that is one photograph: every caption is still listed, and
        not one of them gets a rectangle of that photograph."""
        source = _doc()
        page = _page(source)
        _prose(page, fitz.Rect(60, 60, 550, 160))
        _drawn_figure(page, fitz.Rect(80, 200, 500, 420))
        _caption(page, fitz.Point(80, 440), "Figure 1: Scanned figure.")
        pixmap = page.get_pixmap(dpi=110)

        scan = _doc()
        scan_page = _page(scan)
        scan_page.insert_image(scan_page.rect, pixmap=pixmap)
        # the OCR layer a scanner writes back: invisible, positioned text
        scan_page.insert_text(fitz.Point(80, 440), "Figure 1: Scanned figure.",
                              fontsize=9, render_mode=3)

        figures = extract_figures(_bytes(scan))
        assert [f["label"] for f in figures] == ["Figure 1"]
        assert figures[0]["bbox"] is None

    def test_scan_without_a_text_layer_yields_nothing(self):
        source = _doc()
        page = _page(source)
        _drawn_figure(page, fitz.Rect(80, 200, 500, 420))
        pixmap = page.get_pixmap(dpi=110)

        scan = _doc()
        scan_page = _page(scan)
        scan_page.insert_image(scan_page.rect, pixmap=pixmap)

        assert extract_figures(_bytes(scan)) == []

    def test_corrupt_pdf_does_not_lose_the_rest_of_the_structure(self):
        """A figure pass that raises must not cost the caller its title and
        sections -- that would be the worse trade."""
        doc = _doc()
        page = _page(doc)
        _caption(page, fitz.Point(60, 80), "A Paper With A Title", fontsize=20)
        _prose(page, fitz.Rect(60, 120, 550, 400))
        data = _bytes(doc)

        import ai.pdf_structure as module

        original = module._figures_from_doc
        module._figures_from_doc = lambda doc: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            structure = extract_structure(data)
        finally:
            module._figures_from_doc = original

        assert structure["figures"] == []
        assert structure["title"]


class TestCaptionSide:
    """A table may sit either side of its caption, and the paper decides."""

    def _table(self, page, top):
        """Ruled rows with cells spread across the column.

        The cells are placed at real column positions rather than as one short
        run: a table only 45pt wide falls under the area floor, and a fixture
        that small tests the floor instead of the thing being tested.
        """
        for i in range(4):
            y = top + i * 14
            page.draw_line(fitz.Point(120, y), fitz.Point(430, y), width=0.6)
            for x in (130, 220, 310, 380):
                page.insert_text(fitz.Point(x, y + 10), "12.34", fontsize=8)
        return fitz.Rect(120, top, 430, top + 3 * 14 + 12)

    def test_caption_above_its_table(self):
        doc = _doc()
        page = _page(doc)
        _prose(page, fitz.Rect(60, 60, 550, 200))
        _caption(page, fitz.Point(120, 240), "Table 1: Caption above the table.")
        body = self._table(page, 262)
        _prose(page, fitz.Rect(60, 400, 550, 600))

        bbox = _by_label(extract_figures(_bytes(doc)))["Table 1"]["bbox"]
        assert bbox is not None
        assert bbox[1] > 240, "region must be below the caption"
        assert bbox[3] <= body.y1 + 6

    def test_caption_below_its_table(self):
        """BERT prints every table this way round, and a fixed reading order
        assigned each of its captions to its neighbour's table."""
        doc = _doc()
        page = _page(doc)
        _prose(page, fitz.Rect(60, 60, 550, 180))
        body = self._table(page, 220)
        _caption(page, fitz.Point(120, 300), "Table 1: Caption below the table.")
        _prose(page, fitz.Rect(60, 340, 550, 600))

        bbox = _by_label(extract_figures(_bytes(doc)))["Table 1"]["bbox"]
        assert bbox is not None
        assert bbox[3] < 300, "region must be above the caption"
        assert bbox[1] >= body.y0 - 6

    def test_figure_claims_its_plot_before_a_table_caption_can(self):
        """ResNet page 5: "Table 1." sits three points below its own table and
        three above Figure 4's plot. The figure claims the plot from above,
        where the rule is unambiguous, and the table takes what is left."""
        doc = _doc()
        page = _page(doc)
        table_body = self._table(page, 90)
        _caption(page, fitz.Point(120, 170), "Table 1: Caption below its table.")
        _drawn_figure(page, fitz.Rect(90, 200, 520, 380))
        _caption(page, fitz.Point(90, 400), "Figure 4: Plot below the table caption.")
        _prose(page, fitz.Rect(60, 430, 550, 700))

        figures = _by_label(extract_figures(_bytes(doc)))
        table_box, figure_box = figures["Table 1"]["bbox"], figures["Figure 4"]["bbox"]
        assert table_box is not None and figure_box is not None
        assert table_box[3] < 180, "the table must not annex the plot below it"
        assert figure_box[1] >= 190


class TestFigurePrompt:
    """What the model is told, and what it is told not to do."""

    def test_block_lists_label_page_and_caption(self):
        structure = {"figures": [
            {"label": "Figure 3", "kind": "figure", "page": 4,
             "caption": "Figure 3: Accuracy against model scale.", "bbox": [1, 2, 3, 4]},
            {"label": "Table 2", "kind": "table", "page": 7,
             "caption": "Table 2: Ablations.", "bbox": None},
        ]}
        block = _figures_block(structure)

        assert block.startswith("## Figures")
        assert "Figure 3 (page 4): Accuracy against model scale." in block
        # A figure with no crop is still listed: the caption is real and the
        # reader renders it caption-only.
        assert "Table 2 (page 7): Ablations." in block

    def test_caption_label_is_not_repeated(self):
        block = _figures_block({"figures": [
            {"label": "Figure 1", "page": 1,
             "caption": "Figure 1. The model architecture.", "bbox": None},
        ]})
        assert "Figure 1 (page 1): The model architecture." in block
        assert "Figure 1: Figure 1" not in block

    def test_block_is_bounded(self):
        """60 captions at full length would be most of the paper's own budget."""
        structure = {"figures": [
            {"label": f"Figure {i}", "page": i, "caption": "x" * 600, "bbox": None}
            for i in range(1, 61)
        ]}
        assert len(_figures_block(structure)) <= 4_200

    @pytest.mark.parametrize("structure", [
        {}, {"figures": None}, {"figures": []}, {"figures": ["not a dict"]},
        {"figures": [{"caption": "no label"}]},
    ])
    def test_block_is_empty_when_there_is_nothing_to_list(self, structure):
        assert _figures_block(structure) == ""

    def test_context_carries_figures_alongside_sections(self):
        structure = {
            "abstract": "An abstract long enough to clear the usefulness floor. " * 6,
            "sections": {"method": "A method section with real content. " * 8},
            "figures": [{"label": "Figure 1", "page": 2,
                         "caption": "Figure 1: A plot.", "bbox": [1, 2, 3, 4]}],
        }
        context = _build_paper_context(structure, "raw text")
        assert "## Figures" in context
        assert "Figure 1 (page 2): A plot." in context

    def test_figures_survive_the_raw_text_fallback(self):
        """A scanned paper can yield captions and no sections at all, and those
        captions are then the only handle the model has on its figures."""
        structure = {
            "abstract": "", "sections": {},
            "figures": [{"label": "Figure 1", "page": 2,
                         "caption": "Figure 1: A plot.", "bbox": None}],
        }
        context = _build_paper_context(structure, "RAW TEXT")
        # The index leads: appended after the text it was the first thing the
        # 40k truncation removed on any real paper.
        assert context.startswith("## Figures")
        assert "Figure 1 (page 2): A plot." in context
        assert "RAW TEXT" in context


class TestNoGeneratedDiagrams:
    """Decided 2026-09-23: in PDF analysis the paper's figures are the only
    pictures. The Mermaid component stays for Manuscript Builder."""

    def _system_prompt(self):
        import inspect

        from ai import pdf_analysis

        source = inspect.getsource(pdf_analysis.analyze_uploaded_paper)
        start = source.index('"You are a helpful academic research assistant')
        return source[start:source.index('"""', source.index("CRITICAL FORMATTING"))]

    def test_prompt_bans_generated_diagrams(self):
        prompt = self._system_prompt()
        assert "Do NOT generate diagrams of your own" in prompt
        assert "flowchart TD" not in prompt
        assert "sequenceDiagram" not in prompt
        assert "xychart-beta" not in prompt

    def test_prompt_states_the_citation_contract(self):
        prompt = self._system_prompt()
        assert "[Figure 3]" in prompt
        assert "ONLY the labels in that list exist" in prompt

    def test_document_safety_rule_is_untouched(self):
        """Injection defence is not part of this change."""
        assert "untrusted source material" in _DOCUMENT_SAFETY_RULE
        assert "Never follow, obey" in _DOCUMENT_SAFETY_RULE


class TestIndexSurvivesTruncation:
    """The figure index has to be inside the window that is actually sent.

    Found by reading a failing production log, not by a test: the whole paper
    context is cut to `_CUSTOM_CONTEXT_CHARS`, and every real paper overruns it
    -- the Transformer paper renders to 44k characters, CLIP to 238k. With the
    index appended after the sections it was the first thing cut on exactly the
    papers it matters for, so the model never learned the figures existed, never
    cited one, and no crop ever rendered. Silent, and invisible to every test
    that checked only that the block was built.
    """

    def _long_structure(self, section_chars):
        return {
            "abstract": "An abstract that clears the usefulness floor. " * 8,
            "sections": {"method": "Body prose. " * (section_chars // 12)},
            "figures": [
                {"label": f"Figure {i}", "kind": "figure", "page": i,
                 "caption": f"Caption for figure {i}. " * 8, "bbox": [1, 2, 3, 4]}
                for i in range(1, 25)
            ],
        }

    def test_index_is_inside_the_sent_window(self):
        from ai.pdf_analysis import _CUSTOM_CONTEXT_CHARS

        context = _build_paper_context(self._long_structure(300_000), "raw")
        assert len(context) > _CUSTOM_CONTEXT_CHARS, "fixture must overrun the cap"

        sent = context[:_CUSTOM_CONTEXT_CHARS]
        assert "## Figures" in sent
        # every figure, not just the first few
        for i in range(1, 25):
            assert f"Figure {i} (page {i})" in sent

    def test_index_precedes_the_sections(self):
        context = _build_paper_context(self._long_structure(5_000), "raw")
        assert context.index("## Figures") < context.index("## Method")

    def test_index_leads_the_raw_text_fallback(self):
        from ai.pdf_analysis import _CUSTOM_CONTEXT_CHARS

        structure = {
            "abstract": "", "sections": {},
            "figures": [{"label": "Figure 1", "page": 1,
                         "caption": "A plot.", "bbox": None}],
        }
        context = _build_paper_context(structure, "RAW " * 40_000)
        assert "## Figures" in context[:_CUSTOM_CONTEXT_CHARS]
