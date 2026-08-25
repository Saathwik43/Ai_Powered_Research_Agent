"""
Audit Tier 7 — LaTeX export.

Phase 1 (L1, L5): the exported .tex must actually compile. Everything else in
the tier is unverifiable until it does.

Fixtures are lifted from a real export, `Sample paper/paper.tex` (IEEE venue,
topic "perovskite efficiency"), which is where these defects were reported.
"""

import re
import unittest

from ai.latex_export import (
    _latex_escape,
    _markdown_to_latex,
    export_manuscript,
    fill_reference_holes,
    match_manuscript_refs_to_papers,
)

# Real sentences from the reported export.
LIT_REVIEW_LINE = (
    "certified efficiencies from the seminal 9.7% reported in 2012 for "
    "methylammonium lead iodide ($CH_3NH_3PbI_3$)-sensitized mesoscopic "
    "devices[^4] to over 25% in recent single-junction architectures[^3]."
)
UNICODE_LINE = (
    "SiO₂ antireflection nanoparticles at 55±5°C — a 27.8% boost – measured "
    "with 2 × 10 samples and a 1 μm diffusion length."
)

# Anything still matching this in text mode is a LaTeX compile error.
_BARE_SPECIAL = re.compile(r"(?<!\\)[\^~]")


def _outside_math(tex: str) -> str:
    """Blank out $...$ / $$...$$ so assertions only see text mode."""
    return re.sub(r"\$\$.*?\$\$|\$[^$]*\$", " ", tex, flags=re.DOTALL)


def _body(tex: str) -> str:
    """Drop LaTeX comment lines -- the citation map and the Mermaid TODO block
    legitimately contain text that must not appear in the rendered document."""
    return "\n".join(ln for ln in tex.split("\n") if not ln.lstrip().startswith("%"))


class TestL1Escaping(unittest.TestCase):
    """
    Regression: `_latex_escape` escaped &, %, #, _, { and } but not `\\`, `^`
    or `~`. The generator emits Markdown footnote citations ([^1]...[^15]) in
    every lit_review, so `^` landed in text mode and the export failed with
    `! Missing $ inserted` -- the file never compiled at all.
    """

    def test_footnote_citations_no_longer_leave_a_bare_caret(self):
        out = _latex_escape(LIT_REVIEW_LINE)
        self.assertNotRegex(_outside_math(out), _BARE_SPECIAL)
        self.assertIn(r"\textasciicircum{}", out)

    def test_caret_is_escaped(self):
        self.assertEqual(_latex_escape("x^2"), r"x\textasciicircum{}2")

    def test_tilde_is_escaped(self):
        # Silently became a non-breaking space before, which is worse than an error.
        self.assertEqual(_latex_escape("~1.68 eV"), r"\textasciitilde{}1.68 eV")

    def test_backslash_is_escaped(self):
        self.assertEqual(_latex_escape(r"a\b"), r"a\textbackslash{}b")

    def test_backslash_replacement_is_not_re_escaped(self):
        # The old sequential loop could not express this: \ -> \textbackslash{}
        # emits braces, which the later {/} passes would then mangle.
        self.assertNotIn(r"\textbackslash\{\}", _latex_escape(r"a\b"))

    def test_existing_escapes_still_work(self):
        self.assertEqual(_latex_escape("50% & #1_a {x}"),
                         r"50\% \& \#1\_a \{x\}")

    def test_balanced_math_is_untouched(self):
        for src in ("$CH_3NH_3PbI_3$", r"$$\text{FA}_{0.83}$$", "$x^2 + y^2$"):
            with self.subTest(src=src):
                self.assertEqual(_latex_escape(src), src)

    def test_a_stray_dollar_is_escaped(self):
        self.assertEqual(_latex_escape("costs 5$ per unit"), r"costs 5\$ per unit")

    def test_math_survives_alongside_escaped_text(self):
        out = _latex_escape("rate ~5% in $MAPbI_3$")
        self.assertIn("$MAPbI_3$", out)
        self.assertIn(r"\textasciitilde{}", out)
        self.assertIn(r"\%", out)


class TestL5Unicode(unittest.TestCase):
    """
    Regression: no template loaded inputenc/fontenc, and glyph-less characters
    (subscript digits, ×, μ) were passed through raw to pdfLaTeX.
    """

    def test_subscript_digits_become_math(self):
        self.assertEqual(_latex_escape("SiO₂"), "SiO$_2$")

    def test_superscript_digits_become_math(self):
        self.assertEqual(_latex_escape("x²"), "x$^2$")

    def test_multiplication_sign_becomes_math(self):
        self.assertIn(r"$\times$", _latex_escape("2 × 10"))

    def test_greek_becomes_math(self):
        self.assertIn(r"$\mu$", _latex_escape("1 μm"))

    def test_inserted_math_is_not_double_escaped(self):
        # The rewrite happens before the math stash, so its $...$ is protected.
        self.assertNotIn(r"\$", _latex_escape("SiO₂"))

    def test_dashes_and_degrees_survive_for_inputenc(self):
        # These DO have T1 glyphs; inputenc handles them, so leave them alone
        # rather than mangling readable text into math.
        out = _latex_escape("55±5°C — dash – en")
        for char in "±°—–":
            with self.subTest(char=char):
                self.assertIn(char, out)

    def test_the_reported_unicode_line_has_no_glyphless_chars_left(self):
        out = _latex_escape(UNICODE_LINE)
        for char in "₂×μ":
            with self.subTest(char=char):
                self.assertNotIn(char, out)


class TestTemplatePreambles(unittest.TestCase):
    CONTENT = {"abstract": "An abstract.", "methodology": "A method."}

    def _tex(self, venue):
        tex, _bib, _w = export_manuscript("a topic", self.CONTENT, venue, [])
        return tex

    def test_encoding_packages_are_loaded(self):
        for venue in ("ieee", "springer", "elsevier"):
            with self.subTest(venue=venue):
                tex = self._tex(venue)
                self.assertIn(r"\usepackage[utf8]{inputenc}", tex)
                self.assertIn(r"\usepackage[T1]{fontenc}", tex)

    def test_acm_is_deliberately_left_alone(self):
        # acmart loads both itself; a second \usepackage is an option clash.
        tex = self._tex("acm")
        self.assertNotIn(r"\usepackage[utf8]{inputenc}", tex)


class TestExportedDocumentIsCompilable(unittest.TestCase):
    """End-to-end: the assertions that stand in for 'pdflatex exits 0'."""

    CONTENT = {
        "abstract": "Efficiency reached 9.7% in $CH_3NH_3PbI_3$ cells [5].",
        "lit_review": LIT_REVIEW_LINE + "\n\n" + UNICODE_LINE,
        "methodology": "Annealed at ~150 °C using a 4:1 DMF/DMSO ratio.",
    }
    # The fixture cites [5], [^4] and [^3], so it needs a reference set behind
    # it: since L10, citing with no reference set is a hard error rather than a
    # silently empty bibliography.
    CONTENT_REFS = [
        {"index": str(i), "title": f"Source {i}", "authors": "A. Author", "year": "2024"}
        for i in (3, 4, 5)
    ]

    def setUp(self):
        self.tex, _bib, _w = export_manuscript(
            "perovskite efficiency", self.CONTENT, "ieee", self.CONTENT_REFS
        )

    def test_no_bare_caret_or_tilde_in_text_mode(self):
        self.assertNotRegex(_outside_math(self.tex), _BARE_SPECIAL)

    def test_no_glyphless_unicode_survives(self):
        for char in "₂×μ":
            with self.subTest(char=char):
                self.assertNotIn(char, self.tex)

    def test_math_from_the_manuscript_is_preserved(self):
        self.assertIn("$CH_3NH_3PbI_3$", self.tex)

    def test_mermaid_is_still_commented_out_not_dropped(self):
        tex, _bib, _w = export_manuscript(
            "t", {"results": "See:\n\n```mermaid\npie title X\n```\n"}, "ieee", []
        )
        self.assertIn("% TODO: Mermaid diagram omitted", tex)
        self.assertIn("% pie title X", tex)


REFS = [
    {"index": "1", "title": "Electron injection and defect passivation",
     "authors": "J. Liu, X. Chen", "year": "2024", "doi": "10.1000/aaa"},
    {"index": "2", "title": "Perovskite LEDs with EQE Exceeding 30%",
     "authors": "W. Bai, T. Xuan", "year": "2023",
     "url": "https://example.org/bai"},
    {"index": "3", "title": "High-Efficiency Perovskite Solar Cells",
     "authors": "J. Y. Kim", "year": "2020", "journal": "Chem. Rev."},
]


class TestL4Bibliography(unittest.TestCase):
    """
    Regression: references.bib was zero bytes on EVERY export. Two independent
    causes -- the router passed `manuscript_refs` (index -> formatted *string*,
    which `_flatten_references` drops), and nothing emitted a \\cite command, so
    BibTeX had no \\citation entries to resolve either way.
    """

    CONTENT = {"abstract": "Reached 22.2% [1].", "results": "Also [2,3] and [^1]."}

    def setUp(self):
        self.tex, self.bib, self.warnings = export_manuscript(
            "perovskite efficiency", self.CONTENT, "ieee", REFS
        )

    def test_bib_is_not_empty(self):
        self.assertEqual(self.bib.count("@article{"), 3)

    def test_doi_and_url_survive_the_snapshot(self):
        self.assertIn("10.1000/aaa", self.bib)
        self.assertIn("https://example.org/bai", self.bib)

    def test_numeric_markers_become_cite_commands(self):
        self.assertIn(r"\cite{", self.tex)
        # Comment lines are excluded: the citation map deliberately prints [1].
        self.assertNotIn("[1]", _body(self.tex))

    def test_footnote_markers_become_cite_commands_too(self):
        # [^1] is what the generator emits in lit_review.
        self.assertNotIn(r"[\textasciicircum{}1]", self.tex)

    def test_a_grouped_marker_becomes_one_cite_with_two_keys(self):
        match = re.search(r"\\cite\{([^}]*)\}", self.tex.split("Also")[1])
        self.assertEqual(len(match.group(1).split(",")), 2)

    def test_every_cite_key_exists_in_the_bib(self):
        for keys in re.findall(r"\\cite\{([^}]*)\}", self.tex):
            for key in keys.split(","):
                with self.subTest(key=key):
                    self.assertIn("@article{" + key + ",", self.bib)

    def test_keys_follow_the_stored_index_not_the_loop_counter(self):
        # REFS[0] carries index "1"; enumerate() from 0 would have produced
        # `liu20240`, which is the C1/C4 off-by-one waiting to happen.
        self.assertIn("@article{Liu20241,", self.bib)

    def test_out_of_order_references_still_map_by_index(self):
        _tex, bib, _w = export_manuscript(
            "t", {"results": "see [2]"}, "ieee", list(reversed(REFS))
        )
        self.assertIn("@article{Bai20232,", bib)

    def test_a_citation_map_comment_is_emitted(self):
        self.assertIn("% --- Citation map", self.tex)
        self.assertIn("% [1] -> Liu20241", self.tex)

    def test_the_literal_references_section_is_dropped(self):
        tex, _bib, _w = export_manuscript(
            "t", {"results": "see [1]", "references": "1. J. Liu, ..."}, "ieee", REFS
        )
        self.assertNotIn(r"\section{References}", tex)

    def test_the_references_section_is_kept_when_there_is_no_bib(self):
        tex, _bib, _w = export_manuscript(
            "t", {"results": "prose", "references": "1. J. Liu, ..."}, "ieee", []
        )
        self.assertIn(r"\section{References}", tex)


class TestL9CitationVerification(unittest.TestCase):
    """
    Once [N] becomes \\cite{}, a numbering mismatch stops being cosmetic: the
    paper compiles cleanly and cites the WRONG sources with nothing visibly
    wrong. C1 and C4 were both citation-numbering drift, so this is a
    demonstrated failure mode, not a hypothetical one.
    """

    def test_a_marker_with_no_reference_is_a_hard_error(self):
        with self.assertRaises(ValueError) as ctx:
            export_manuscript("t", {"results": "see [99]"}, "ieee", REFS)
        self.assertIn("[99]", str(ctx.exception))

    def test_the_error_names_every_offending_marker(self):
        with self.assertRaises(ValueError) as ctx:
            export_manuscript("t", {"results": "[7] and [9]"}, "ieee", REFS)
        message = str(ctx.exception)
        self.assertIn("[7]", message)
        self.assertIn("[9]", message)

    def test_uncited_references_are_a_warning_not_an_error(self):
        _tex, _bib, warnings = export_manuscript(
            "t", {"results": "only [1]"}, "ieee", REFS
        )
        self.assertTrue(any("[2]" in w and "[3]" in w for w in warnings))

    def test_a_fully_cited_manuscript_warns_about_nothing(self):
        _tex, _bib, warnings = export_manuscript(
            "t", {"results": "[1] [2] [3]"}, "ieee", REFS
        )
        self.assertEqual(warnings, [])

    def test_markers_inside_the_references_section_do_not_count_as_citations(self):
        # The hand-numbered list is "1." not "[1]", but be explicit about it.
        _tex, _bib, warnings = export_manuscript(
            "t", {"results": "[1] [2] [3]", "references": "[1] [2] [3]"}, "ieee", REFS
        )
        self.assertEqual(warnings, [])


class TestL10EmptyReferenceSet(unittest.TestCase):
    """
    L10: the empty reference set was the one case L9 did not check.

    `if references:` wrapped the whole verification, so a draft citing [1]..[12]
    with no stored reference set exported happily -- zero-byte references.bib,
    no \\cite, no citation map, literal [N] left in the prose. A single stray
    [99] among real references still failed loudly, so the check was inverted
    with respect to severity. Verified on a real export 2026-08-21
    (perovskite-efficiency-ieee.zip).

    This class replaces `test_no_references_means_no_verification`, which pinned
    the defect as if it were the intended behaviour.
    """

    def test_citing_with_no_reference_set_is_a_hard_error(self):
        with self.assertRaises(ValueError):
            export_manuscript("t", {"results": "see [4]"}, "ieee", [])

    def test_the_error_names_the_recovery_not_just_the_markers(self):
        with self.assertRaises(ValueError) as ctx:
            export_manuscript("t", {"abstract": "a [1]", "results": "b [5]"}, "ieee", [])
        message = str(ctx.exception)
        self.assertIn("[1]", message)
        self.assertIn("[5]", message)
        # The user cannot guess "regenerate a section" from a list of markers.
        self.assertIn("Regenerate", message)
        self.assertIn("references.bib", message)

    def test_a_draft_with_no_markers_and_no_references_still_exports(self):
        # Nothing cited, nothing to verify: an early draft must not be blocked.
        tex, bib, warnings = export_manuscript("t", {"results": "plain prose"}, "ieee", [])
        self.assertEqual(bib, "")
        self.assertEqual(warnings, [])
        self.assertIn("plain prose", tex)

    def test_markers_only_inside_the_references_section_do_not_block_export(self):
        # The hand-numbered list is not a citation; blocking on it would make
        # every pre-snapshot draft unexportable for the wrong reason.
        _tex, bib, warnings = export_manuscript(
            "t", {"results": "prose", "references": "[1] J. Liu, ..."}, "ieee", []
        )
        self.assertEqual(bib, "")
        self.assertEqual(warnings, [])


class TestKnownPhase3To4Gaps(unittest.TestCase):
    """L2 / L3 / L6 / L7 / L8 / L-min — converters that used to pass markdown through."""

    def test_pipe_tables_become_tabular(self):
        md = "| A | B |\n| :--- | :--- |\n| 1 | 2 |"
        tex = _markdown_to_latex(md)
        self.assertIn(r"\begin{tabular}", tex)
        self.assertNotIn("| A |", tex)

    def test_headings_become_subsections(self):
        tex = _markdown_to_latex("## Material Synthesis")
        self.assertIn(r"\subsection{Material Synthesis}", tex)
        self.assertNotIn(r"\#\#", tex)

    def test_bold_heading_line_becomes_subsection(self):
        tex = _markdown_to_latex("**Material Synthesis**")
        self.assertIn(r"\subsection{Material Synthesis}", tex)

    def test_inline_display_math_becomes_inline(self):
        tex = _markdown_to_latex(r"iodide ($$\text{CH}_3$$) was used")
        self.assertNotIn("$$", tex)
        self.assertIn(r"($\text{CH}_3$)", tex)

    def test_latex_bracket_delimiters_become_math_not_literal_brackets(self):
        tex = _markdown_to_latex(
            r"via \[ L_n = \frac{1}{h} \] and \( E = mc^2 \)."
        )
        self.assertIn(r"\frac{1}{h}", tex)
        self.assertIn("E = mc^2", tex)
        self.assertNotIn(r"\textbackslash", tex)
        self.assertNotIn("[ L_n", tex)

    def test_bracket_math_inside_a_fence_is_left_alone(self):
        tex = _markdown_to_latex("```mermaid\nflowchart TD\n A[Start]\n```\n")
        self.assertIn("A[Start]", tex)
        self.assertIn("% TODO: Mermaid diagram omitted", tex)

    def test_section_keys_use_display_titles(self):
        tex, _bib, _w = export_manuscript("t", {"lit_review": "x"}, "ieee", [])
        self.assertIn(r"\section{Literature Review}", tex)
        self.assertNotIn(r"Lit_Review", tex)

    def test_horizontal_rules_are_dropped(self):
        tex = _markdown_to_latex("before\n---\nafter")
        self.assertNotIn("---", tex)
        self.assertIn("before", tex)
        self.assertIn("after", tex)

    def test_blockquote_is_a_quote_env_not_a_greater_than(self):
        tex = _markdown_to_latex("> cited claim")
        self.assertIn(r"\begin{quote}", tex)
        self.assertIn("cited claim", tex)
        self.assertNotIn(r"\textgreater{}", tex)

    def test_comparison_greater_than_is_escaped(self):
        self.assertIn(r"\textgreater{}", _latex_escape("n > 3"))
        self.assertIn(r"\textless{}", _latex_escape("n < 3"))

    def test_title_is_title_cased_preserving_acronyms(self):
        tex, _bib, _w = export_manuscript("CNN for MRI imaging", {"results": "x"}, "ieee", [])
        self.assertIn(r"\title{CNN For MRI Imaging}", tex)

    def test_keywords_are_derived_from_the_topic(self):
        tex, _bib, _w = export_manuscript("perovskite efficiency", {"results": "x"}, "ieee", [])
        self.assertIn("perovskite, efficiency", tex)

    def test_explicit_keywords_win(self):
        tex, _bib, _w = export_manuscript(
            "perovskite efficiency", {"results": "x"}, "ieee", [], keywords="solar, cells"
        )
        self.assertIn("solar, cells", tex)
        self.assertNotIn("perovskite, efficiency", tex)


class TestL14SnapshotRebuild(unittest.TestCase):
    def test_display_strings_match_cached_papers_by_title(self):
        papers = [
            {"title": "Electron injection", "authors": "J. Liu", "year": "2024", "doi": "10.1000/aaa"},
            {"title": "Other paper", "authors": "A. B", "year": "2020"},
        ]
        refs = {"1": 'J. Liu, "Electron injection", 2024.'}
        snapshot = match_manuscript_refs_to_papers(refs, papers)
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0]["index"], "1")
        self.assertEqual(snapshot[0]["doi"], "10.1000/aaa")

    def test_dict_valued_refs_are_kept(self):
        snapshot = match_manuscript_refs_to_papers(
            {"2": {"title": "Kept", "authors": "A", "year": "2021"}},
            [],
        )
        self.assertEqual(snapshot[0]["title"], "Kept")
        self.assertEqual(snapshot[0]["index"], "2")

    def test_unmatched_display_strings_become_stubs_not_holes(self):
        papers = [{"title": "Electron injection", "authors": "J. Liu", "year": "2024"}]
        refs = {
            "1": 'J. Liu, "Electron injection", 2024.',
            "2": 'Z. Other, "No matching paper in the cache", 2020.',
        }
        snapshot = match_manuscript_refs_to_papers(refs, papers)
        self.assertEqual([p["index"] for p in snapshot], ["1", "2"])
        stub = next(p for p in snapshot if p["index"] == "2")
        self.assertTrue(stub["from_display_only"])
        self.assertIn("No matching paper", stub["title"])


class TestCitationHolesAndDiagrams(unittest.TestCase):
    """The live error: markers [2], [6], [7], [8] vs an 11-entry snapshot."""

    HOLEY_REFS = [
        {"index": str(i), "title": f"Source {i}", "authors": "A. Author", "year": "2024"}
        for i in (1, 3, 4, 5, 9, 10, 11, 12, 13, 14, 15)
    ]

    def test_eleven_holey_refs_match_the_reported_error_without_repair(self):
        self.assertEqual(len(self.HOLEY_REFS), 11)
        with self.assertRaises(ValueError) as ctx:
            export_manuscript(
                "t",
                {"results": "See [2], [6], [7] and [8]."},
                "ieee",
                self.HOLEY_REFS,
            )
        message = str(ctx.exception)
        self.assertIn("[2], [6], [7], [8]", message)
        self.assertIn("11 reference(s)", message)

    def test_display_list_fills_the_cited_holes(self):
        display = {n: f'Author, "Paper {n}", 2024.' for n in ("2", "6", "7", "8")}
        tex, bib, warnings = export_manuscript(
            "t",
            {"results": "See [2], [6], [7] and [8]."},
            "ieee",
            self.HOLEY_REFS,
            manuscript_refs=display,
        )
        self.assertIn(r"\cite{", tex)
        self.assertEqual(bib.count("@article{"), 15)
        self.assertTrue(any("citation list" in w for w in warnings))

    def test_mermaid_bar_arrays_are_not_citation_markers(self):
        tex, bib, warnings = export_manuscript(
            "t",
            {"results": (
                "Projected accuracy follows prior work [1].\n\n"
                "```mermaid\nxychart-beta\n"
                "    x-axis [\"A\", \"B\", \"C\", \"D\"]\n"
                "    bar [2, 6, 7, 8]\n"
                "```\n"
            )},
            "ieee",
            self.HOLEY_REFS,
        )
        self.assertIn("% [1] ->", tex)
        self.assertNotIn("% [2] ->", tex)
        self.assertIn(r"\cite{", tex)
        self.assertIn("%     bar [2, 6, 7, 8]", tex)
        self.assertEqual(bib.count("@article{"), 11)
        self.assertFalse(any("citation list" in w for w in warnings))

    def test_fill_only_attaches_cited_holes(self):
        filled = fill_reference_holes(
            self.HOLEY_REFS,
            {n: f"Paper {n}" for n in ("2", "6", "16")},
            used_indices={"2", "6"},
        )
        indices = {p["index"] for p in filled}
        self.assertIn("2", indices)
        self.assertIn("6", indices)
        self.assertNotIn("16", indices)


if __name__ == "__main__":
    unittest.main()
