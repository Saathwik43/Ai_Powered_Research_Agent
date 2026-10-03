"""
2.3 — smarter queries.

These tests assert *our* intent: what we render, and which source receives
which rendering. They deliberately assert nothing about what PubMed or OpenAlex
do with the string — that half was settled against the live endpoints (see the
measurements in `integrations/query_expansion.py`) and re-checked on demand by
`scripts/verify_mesh_headings.py`. A test that mocked a source and asserted the
expansion "works" would be evidence about the mock.
"""

from unittest.mock import AsyncMock, patch

import pytest

from integrations import paper_search as ps
from integrations import query_expansion as qe
from integrations import registry


class TestLexicon:
    def test_no_surface_form_belongs_to_two_groups(self):
        """The index maps a form to exactly one group. Two groups claiming
        "tumor" would make the substitution order decide the query."""
        seen: dict[str, tuple] = {}
        for group in qe._GROUPS:
            for form in group:
                assert form not in seen or seen[form] == group, (
                    f"{form!r} appears in {seen.get(form)} and {group}"
                )
                seen[form] = group

    def test_every_group_has_at_least_two_forms(self):
        """A one-form group can never produce a variant — it is dead weight
        that reads as coverage."""
        for group in qe._GROUPS:
            assert len(group) >= 2, group

    def test_forms_are_lowercase_and_stripped(self):
        """Matching happens against a lowercased query, so an upper-case form
        would silently never fire."""
        for group in qe._GROUPS:
            for form in group:
                assert form == form.lower().strip(), form
        for concept in qe._MESH:
            assert concept == concept.lower().strip(), concept


class TestConceptMatching:
    def test_the_longest_concept_wins(self):
        """"type 2 diabetes" contains "diabetes"; substituting both would
        produce "type 2 diabetes mellitus mellitus"."""
        assert qe._matched_concepts("type 2 diabetes prevalence") == ["type 2 diabetes"]

    def test_a_concept_inside_a_longer_word_is_not_a_match(self):
        """"ml" must not fire on "html" or "mlops"."""
        assert "ml" not in qe._matched_concepts("html parsing")
        assert "ai" not in qe._matched_concepts("aiming for reproducibility")

    def test_hyphenated_forms_match(self):
        assert "covid-19" in qe._matched_concepts("covid-19 vaccine hesitancy")


class TestVariants:
    def test_an_acronym_expands_to_the_long_form(self):
        plan = qe.build_plan("llm agents")
        assert "large language model agents" in plan.variants

    def test_the_long_form_contracts_to_the_acronym(self):
        """Expansion is bidirectional: the author who writes it out should also
        reach the papers that used the acronym."""
        plan = qe.build_plan("convolutional neural network segmentation")
        assert "cnn segmentation" in plan.variants

    def test_two_spellings_are_fixed_in_one_clause(self):
        """The seed that killed the first design. Both British forms have to
        move together — a variant carrying one of each retrieves nothing new."""
        plan = qe.build_plan("randomised controlled trial tumour imaging")
        assert "randomized controlled trial tumor imaging" in plan.variants

    def test_variants_never_include_the_original(self):
        plan = qe.build_plan("machine learning")
        assert plan.original.lower() not in plan.variants

    def test_variant_count_is_capped(self):
        plan = qe.build_plan(
            "randomised optimisation of tumour behaviour modelling in paediatric anaemia"
        )
        assert len(plan.variants) <= qe.MAX_VARIANTS

    def test_a_query_with_no_lexicon_hit_is_left_alone(self):
        plan = qe.build_plan("sourdough fermentation kinetics")
        assert not plan.expanded
        assert plan.for_dialect(qe.PUBMED) == "sourdough fermentation kinetics"


class TestMesh:
    def test_a_mesh_clause_is_anchored_to_the_rest_of_the_query(self):
        """An unanchored heading matches tens of thousands of off-topic papers,
        and PubMed sorts by relevance into the rows we actually read."""
        plan = qe.build_plan("crispr gene editing")
        headings = [h for h, _ in plan.mesh]
        assert headings
        for _heading, anchor in plan.mesh:
            assert anchor.strip()

    def test_a_bare_concept_query_gets_no_mesh_clause(self):
        """Nothing is left to anchor with, so the clause would be the whole
        heading on its own."""
        plan = qe.build_plan("hypertension")
        assert plan.mesh == ()

    def test_mesh_clauses_are_capped(self):
        plan = qe.build_plan("machine learning for breast cancer and lung cancer imaging")
        assert len(plan.mesh) <= qe.MAX_MESH_CLAUSES


class TestRendering:
    def test_plain_dialect_is_the_raw_query(self):
        """Every source behaved this way before 2.3 and most still must, so a
        plain rendering may strip surrounding whitespace and nothing else."""
        raw = "llm agents"
        assert qe.build_plan(raw).for_dialect(qe.PLAIN) == raw
        assert qe.build_plan("  llm  agents ").for_dialect(qe.PLAIN) == "llm  agents"

    def test_an_unknown_dialect_degrades_to_plain(self):
        """A dialect name nobody taught the renderer must fall back to today's
        behaviour, not to an empty search."""
        raw = "llm agents"
        assert qe.build_plan(raw).for_dialect("sparql") == raw

    def test_the_original_is_always_the_first_clause(self):
        """The expansion is a superset by construction: no rendering can return
        less than the unexpanded search would have."""
        rendered = qe.build_plan("llm agents").for_dialect(qe.BOOLEAN)
        assert rendered.startswith("(llm agents)")

    def test_boolean_dialect_carries_no_field_tags(self):
        """Probe #2: a [MeSH Terms] tag sent to Europe PMC cut 138170 hits to
        2451, and DOAJ answered HTTP 400. Field tags are PubMed-only."""
        rendered = qe.build_plan("crispr gene editing").for_dialect(qe.BOOLEAN)
        assert "[" not in rendered and "MeSH" not in rendered

    def test_pubmed_dialect_carries_the_mesh_clause(self):
        rendered = qe.build_plan("crispr gene editing").for_dialect(qe.PUBMED)
        assert "[MeSH Terms]" in rendered

    def test_rendering_is_length_capped_by_dropping_whole_clauses(self):
        plan = qe.QueryPlan(
            original="a" * 200,
            variants=("b" * 200, "c" * 200),
            mesh=(("Heading", "anchor"),),
        )
        rendered = plan.for_dialect(qe.PUBMED)
        assert len(rendered) <= qe.MAX_RENDERED_CHARS
        # Truncation drops from the end and never mid-clause.
        assert rendered.startswith("(" + "a" * 200 + ")")
        assert rendered.count("(") == rendered.count(")")

    def test_an_empty_query_renders_empty_everywhere(self):
        plan = qe.build_plan("   ")
        for dialect in (qe.PLAIN, qe.BOOLEAN, qe.PUBMED):
            assert plan.for_dialect(dialect) == ""


class TestExpertPassthrough:
    @pytest.mark.parametrize("raw", [
        'diabetes AND ("insulin resistance" OR obesity)',
        '"gene editing"',
        "crispr[tiab]",
        "(machine learning) NOT survey",
    ])
    def test_a_query_already_in_a_query_language_is_untouched(self, raw):
        """Rewriting an expert's Boolean is how a tool earns the reputation of
        fighting its users."""
        plan = qe.build_plan(raw)
        assert plan.expert and not plan.expanded
        for dialect in (qe.PLAIN, qe.BOOLEAN, qe.PUBMED):
            assert plan.for_dialect(dialect) == raw

    def test_a_lowercase_and_is_not_an_operator(self):
        """PubMed only honours upper-case operators, and "cats and dogs" is a
        topic, not a Boolean."""
        plan = qe.build_plan("machine learning and drug discovery")
        assert not plan.expert


class TestRegistryWiring:
    def test_every_declared_dialect_is_one_the_renderer_knows(self):
        known = {qe.PLAIN, qe.BOOLEAN, qe.PUBMED}
        for source in registry.all_sources():
            assert source.dialect in known, (source.name, source.dialect)

    def test_only_the_measured_sources_carry_a_non_plain_dialect(self):
        """Europe PMC, DOAJ and arXiv were measured and did worse; Semantic
        Scholar was never measurable (429 on every probe). An unmeasured source
        stays plain — see the table in query_expansion's docstring."""
        expanded = {s.name for s in registry.all_sources() if s.dialect != qe.PLAIN}
        assert expanded == {"PubMed", "OpenAlex"}


class TestFanOut:
    @pytest.mark.anyio
    async def test_each_source_receives_the_rendering_for_its_own_dialect(self):
        """The point of the ID, end to end: PubMed gets Boolean + MeSH, OpenAlex
        gets Boolean without tags, and everyone else gets the raw string."""
        ps._cache.clear()
        ps._inflight.clear()

        received: dict[str, str] = {}

        def recorder(name):
            async def _call(query, limit=15, **_kwargs):
                received[name] = query
                return []
            return _call

        def sync_recorder(name):
            def _call(query, *_a, **_k):
                received[name] = query
                return []
            return _call

        patches = [
            patch.object(
                ps, s.attr,
                sync_recorder(s.name) if s.sync else recorder(s.name),
            )
            for s in registry.all_sources()
        ]
        for p in patches:
            p.start()
        try:
            with patch("integrations.unpaywall.enrich_papers_with_oa",
                       AsyncMock(side_effect=lambda x: x)):
                await ps._execute_search(
                    "crispr gene editing",
                    limit_per_source=5,
                    semantic_rerank=False,
                    source_timeout=5.0,
                    oa_timeout=1.0,
                    exclude_sources=set(),
                    cache_key="test_dialect_fan_out",
                )
        finally:
            for p in patches:
                p.stop()
            ps._cache.clear()

        assert "[MeSH Terms]" in received["PubMed"]
        assert received["OpenAlex"].startswith("(crispr gene editing)")
        assert "[" not in received["OpenAlex"]
        assert received["arXiv"] == "crispr gene editing"
        assert received["SemanticScholar"] == "crispr gene editing"

    @pytest.mark.anyio
    async def test_the_cache_key_and_ranking_still_see_the_raw_query(self):
        """A plan is a rendering, never a new identity. If the rendered string
        leaked into the cache key, "llm agents" and a later paraphrase would
        stop sharing a fan-out."""
        ps._cache.clear()
        ps._inflight.clear()
        ranked_with: list[str] = []

        original_rank = ps._rank_papers

        def spy(query, papers):
            ranked_with.append(query)
            return original_rank(query, papers)

        empty = AsyncMock(return_value=[])
        patches = [
            patch.object(ps, s.attr, (lambda *a, **k: []) if s.sync else empty)
            for s in registry.all_sources()
        ]
        patches.append(patch.object(ps, "_rank_papers", spy))
        for p in patches:
            p.start()
        try:
            with patch("integrations.unpaywall.enrich_papers_with_oa",
                       AsyncMock(side_effect=lambda x: x)):
                await ps.search_all("llm agents", semantic_rerank=False)
        finally:
            for p in patches:
                p.stop()
            ps._cache.clear()
            ps._inflight.clear()

        assert ranked_with and all(q == "llm agents" for q in ranked_with)
