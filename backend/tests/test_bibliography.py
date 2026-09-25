"""2.8 — the bibliography is a real one.

Two defects motivated this module, and both were visible in the shipped
artefact rather than in any test:

* every reference was exported as ``@article``, whatever it actually was;
* the journal field fell back to ``paper["source"]``, so real exports carried
  ``journal = {OpenAlex}`` — the name of the search service, printed as though
  it were the venue.

The assertions below are about emitted bytes, not about whether a helper was
called: a mocked BibTeX writer proves nothing about the .bib a user opens.
"""

import json

from ai.bibliography import (
    entry_type,
    normalize_doi,
    to_bibtex,
    to_csl_json,
    to_ris,
    venue_of,
)


class TestEntryType:
    def test_a_reported_crossref_type_wins(self):
        assert entry_type({"type": "journal-article"}) == "article"
        assert entry_type({"type": "proceedings-article"}) == "inproceedings"
        assert entry_type({"type": "book-chapter"}) == "incollection"
        assert entry_type({"type": "dissertation"}) == "phdthesis"
        assert entry_type({"type": "report"}) == "techreport"
        assert entry_type({"type": "book"}) == "book"

    def test_a_preprint_is_misc_even_when_the_record_claims_otherwise(self):
        # arXiv records sometimes carry a journal-ish type from a later
        # publication that never happened.
        assert entry_type({"type": "posted-content", "subtype": "preprint"}) == "misc"
        assert entry_type({"eprint": "1810.04805"}) == "misc"

    def test_an_eprint_with_a_real_venue_is_not_a_preprint(self):
        # Published-then-mirrored: arXiv reports journal_ref, so it is the
        # version of record that should be cited.
        paper = {"eprint": "1706.03762", "venue": "Nature", "type": "journal-article"}
        assert entry_type(paper) == "article"

    def test_the_host_venue_kind_is_used_when_no_type_was_reported(self):
        assert entry_type({"venue_type": "conference", "venue": "Whatever"}) == "inproceedings"
        assert entry_type({"venue_type": "journal", "venue": "Whatever"}) == "article"

    def test_abbreviated_conference_names_are_recognised(self):
        # Venue strings are routinely abbreviated; spelling out "proceedings"
        # was not enough and filed real conference papers as @article.
        assert entry_type({"venue": "Proc. of ICML"}) == "inproceedings"
        assert entry_type({"venue": "Int. Conf. on Robotics"}) == "inproceedings"
        assert entry_type({"venue": "IEEE Symp. on Security"}) == "inproceedings"

    def test_a_journal_name_is_not_mistaken_for_a_conference(self):
        assert entry_type({"venue": "Chem. Rev."}) == "article"
        assert entry_type({"venue": "Nature"}) == "article"

    def test_an_unlabelled_record_defaults_to_article(self):
        # The corpus is overwhelmingly journal papers, and @article is the type
        # every style file renders sanely.
        assert entry_type({"title": "Something", "authors": "A. Smith"}) == "article"


class TestVenue:
    def test_the_name_of_a_search_service_is_never_a_venue(self):
        # The regression: `journal = {OpenAlex}` in shipped .bib files.
        for service in ("OpenAlex", "Semantic Scholar", "arXiv", "Crossref", "PubMed"):
            assert venue_of({"source": service, "venue": service}) == ""

    def test_a_real_venue_is_kept(self):
        assert venue_of({"journal": "Chem. Rev."}) == "Chem. Rev."
        assert venue_of({"venue": "NeurIPS"}) == "NeurIPS"

    def test_journal_is_preferred_over_venue(self):
        assert venue_of({"journal": "Nature", "venue": "Something Else"}) == "Nature"


class TestDoi:
    def test_url_and_scheme_prefixes_are_stripped(self):
        assert normalize_doi("https://doi.org/10.1021/acs.9b00447") == "10.1021/acs.9b00447"
        assert normalize_doi("http://dx.doi.org/10.1021/acs.9b00447") == "10.1021/acs.9b00447"
        assert normalize_doi("doi:10.1021/acs.9b00447") == "10.1021/acs.9b00447"

    def test_a_bare_doi_is_untouched(self):
        assert normalize_doi("10.48550/arXiv.1706.03762") == "10.48550/arXiv.1706.03762"

    def test_junk_is_rejected_rather_than_printed(self):
        # An unvalidated field is how `doi = {n/a}` reaches a bibliography.
        for junk in ("n/a", "", None, "not a doi", "10.x/bad", "https://example.org/paper"):
            assert normalize_doi(junk) == ""


class TestBibtex:
    def test_entry_types_reach_the_emitted_bib(self):
        bib = to_bibtex([
            {"index": "1", "title": "Conf", "authors": "A. B", "year": "2020",
             "type": "proceedings-article", "venue": "NeurIPS"},
            {"index": "2", "title": "Pre", "authors": "C. D", "year": "2021",
             "type": "posted-content", "subtype": "preprint", "eprint": "2101.00001"},
        ])
        assert "@inproceedings{" in bib
        assert "@misc{" in bib
        assert "@article{" not in bib

    def test_a_conference_paper_uses_booktitle_not_journal(self):
        bib = to_bibtex([{"index": "1", "title": "T", "authors": "A. B", "year": "2020",
                          "type": "proceedings-article", "venue": "NeurIPS"}])
        assert "booktitle = {NeurIPS}" in bib
        assert "journal" not in bib

    def test_the_search_service_never_becomes_a_journal(self):
        bib = to_bibtex([{"index": "1", "title": "T", "authors": "A. B",
                          "year": "2020", "source": "OpenAlex"}])
        assert "OpenAlex" not in bib

    def test_latex_specials_in_a_title_are_escaped(self):
        # `&` and `%` are unescaped-fatal: BibTeX aborts the whole run on them.
        bib = to_bibtex([{"index": "1", "title": "R&D at 50% Yield",
                          "authors": "A. B", "year": "2020"}])
        assert r"R\&D at 50\% Yield" in bib

    def test_et_al_does_not_become_an_author(self):
        # Every source truncates to three names and appends " et al.", so the
        # marker arrives attached to the last author.
        bib = to_bibtex([{"index": "1", "title": "T", "year": "2017",
                          "authors": "A. Vaswani, N. Shazeer, N. Parmar et al."}])
        assert "author = {A. Vaswani and N. Shazeer and N. Parmar}" in bib

    def test_an_arxiv_record_carries_eprint_fields(self):
        bib = to_bibtex([{"index": "1", "title": "T", "authors": "A. B", "year": "2018",
                          "eprint": "1810.04805", "type": "posted-content"}])
        assert "eprint = {1810.04805}" in bib
        assert "archivePrefix = {arXiv}" in bib

    def test_an_invalid_doi_is_omitted_not_printed(self):
        bib = to_bibtex([{"index": "1", "title": "T", "authors": "A. B",
                          "year": "2020", "doi": "n/a"}])
        assert "doi" not in bib

    def test_the_cite_key_uses_the_stored_index(self):
        bib = to_bibtex([{"index": "7", "title": "T", "authors": "Y. Liu", "year": "2024"}])
        assert "@article{Liu20247," in bib

    def test_a_placeholder_author_does_not_become_a_surname_in_the_key(self):
        # Found in a real export: the "Unknown Authors" placeholder was keyed
        # `Authors2021`, i.e. cited as though someone named Authors wrote it.
        bib = to_bibtex([{"index": "4", "title": "Orphan", "authors": "Unknown Authors",
                          "year": "2021"}])
        assert "@article{ref20214," in bib
        assert "Authors" not in bib

    def test_a_missing_author_omits_the_field_rather_than_inventing_one(self):
        # Style files all handle an absent author; none handles an author
        # literally named "Unknown", which previously got typeset.
        bib = to_bibtex([{"index": "1", "title": "Orphan", "year": "2021"}])
        assert "author" not in bib
        assert "Unknown" not in bib


class TestRis:
    REFS = [{
        "index": "1", "title": "Attention Is All You Need",
        "authors": "A. Vaswani, N. Shazeer, N. Parmar et al.", "year": "2017",
        "type": "proceedings-article", "venue": "NeurIPS", "pages": "5998--6008",
        "doi": "10.5555/3295222", "url": "https://example.org/x",
    }]

    def test_every_record_is_delimited(self):
        ris = to_ris(self.REFS)
        assert ris.startswith("TY  - ")
        assert "ER  - " in ris

    def test_the_reference_type_maps_from_the_entry_type(self):
        assert "TY  - CPAPER" in to_ris(self.REFS)
        assert "TY  - JOUR" in to_ris([{"index": "1", "title": "T", "venue": "Nature"}])

    def test_authors_are_surname_first_and_carry_no_et_al(self):
        ris = to_ris(self.REFS)
        assert "AU  - Vaswani, A." in ris
        assert "AU  - Parmar, N." in ris
        # The bug this replaced: `AU  - al, N. Parmar et`.
        assert "et" not in ris.split("TI  -")[0].replace("Attention", "")

    def test_a_page_range_is_split_into_start_and_end(self):
        ris = to_ris(self.REFS)
        assert "SP  - 5998" in ris
        assert "EP  - 6008" in ris

    def test_a_placeholder_author_is_not_emitted_as_a_person(self):
        ris = to_ris([{"index": "1", "title": "T", "authors": "Unknown Authors"}])
        assert "AU  -" not in ris


class TestCslJson:
    def test_it_is_valid_json_and_a_list(self):
        items = json.loads(to_csl_json([{"index": "1", "title": "T", "authors": "A. B", "year": "2020"}]))
        assert isinstance(items, list) and len(items) == 1

    def test_csl_types_map_from_the_entry_type(self):
        items = json.loads(to_csl_json([
            {"index": "1", "title": "A", "type": "journal-article"},
            {"index": "2", "title": "B", "type": "proceedings-article"},
            {"index": "3", "title": "C", "type": "book-chapter"},
        ]))
        assert [i["type"] for i in items] == ["article-journal", "paper-conference", "chapter"]

    def test_the_year_is_a_nested_date_part_not_a_string(self):
        items = json.loads(to_csl_json([{"index": "1", "title": "T", "year": "2020"}]))
        assert items[0]["issued"] == {"date-parts": [[2020]]}

    def test_authors_are_split_into_family_and_given(self):
        items = json.loads(to_csl_json([{"index": "1", "title": "T", "authors": "John A. Smith"}]))
        assert items[0]["author"] == [{"family": "Smith", "given": "John A."}]

    def test_a_page_range_uses_a_single_hyphen(self):
        # BibTeX wants `--`; CSL wants `-`.
        items = json.loads(to_csl_json([{"index": "1", "title": "T", "pages": "10--20"}]))
        assert items[0]["page"] == "10-20"
