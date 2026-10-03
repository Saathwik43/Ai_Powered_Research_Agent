"""
Smarter queries (2.3) — synonyms, Boolean, MeSH where relevant.

Every source used to receive the user's raw string. That is fine for a
relevance-ranked free-text endpoint and a waste of the two sources that speak
real query languages: an author searching "llm agents" never sees the paper
that wrote "large language model agents", and a British speller searching
"randomised controlled trial tumour imaging" never sees the American half of
the literature.

What this module does NOT do, and why
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The obvious design — split the query into concepts and re-tag each one as a
field-qualified phrase AND — was built, probed against the real endpoints, and
thrown away. Measured on 2026-09-30:

    ("randomised controlled trial"[tiab] OR "randomized controlled trial"[tiab])
      AND ("tumour imaging"[tiab] OR "tumor imaging"[tiab])

    PubMed   9354 -> 8        arXiv  482025 -> 0

A phrase-AND throws away PubMed's Automatic Term Mapping, which was quietly
doing most of the work, and demands an exact phrase that nobody writes. The
shape that survived keeps the user's query verbatim as one clause and ORs
*whole-query variants* beside it, so the expansion is a strict superset and
recall can only rise:

    (randomised controlled trial tumour imaging)
      OR (randomized controlled trial tumor imaging)      9354 -> 9354
    (llm agents) OR (large language model agents)          827 -> 1822

Dialects are per-source and evidence-gated
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Recall is the easy half. The fan-out only ever reads the first ~15-20 rows a
source returns, so a widened OR that raises hitCount while pushing junk into
row 1 is a regression. Probed plain-vs-variant top-N on the same day:

    OpenAlex   superset, top-N equal or better on all four seeds — "crispr gene
               editing" promoted Doudna's "Genome engineering using the
               CRISPR-Cas9 system" to #1, "llm agents" promoted the two
               canonical agent surveys.                            ENABLED
    PubMed     superset on all four; top-5 on-topic, mostly the same records
               reordered. Its own sort is sort=relevance, so this was checked
               with real titles, not counts.                       ENABLED
    EuropePMC  recall up 6046 -> 105029 but top-N degraded on 3 of 4 seeds:
               "LLM Agents: A Survey" at #1 was displaced by an off-topic
               opinion-dynamics paper.                             NOT enabled
    DOAJ       top-2 held on 3 of 4 seeds, then the fourth replaced all three
               rows with off-topic records. A PubMed field tag in the URL path
               also answers HTTP 400.                              NOT enabled
    arXiv      quoted-phrase variants collapse it: 145468 -> 5169, and two
               seeds went to 0 and 2 results.                      NOT enabled

Semantic Scholar answered 429 to every probe (no key in that environment), so
it was never measured; its /paper/search endpoint is documented as free-text
relevance, and an unmeasured source stays on ``plain``. Crossref, Springer,
Zenodo, OpenReview, ACL and GitHub are ``plain`` for the same reason — not
because they cannot do better, but because nobody has shown they do.

Everything here is deterministic and offline: a curated lexicon, no LLM call,
no extra HTTP round trip. Query expansion sits in front of the cache key, which
is still derived from the raw query by ``core.query_key`` — a plan is a
rendering of a query, never a new identity for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.query_key import GRAMMAR_STOPS

# Dialect names. A source declares one in `integrations.registry`; the renderer
# below is the only thing that knows what each means.
PLAIN = "plain"
BOOLEAN = "boolean"
PUBMED = "pubmed"

# An OR of whole-query clauses is cheap at two or three and indiscriminate at
# ten. The cap is on rendered clauses, not on lexicon hits.
MAX_VARIANTS = 2

# One MeSH clause. Each extra one widens the OR without narrowing anything.
MAX_MESH_CLAUSES = 1

# esearch and the OpenAlex `search` parameter both travel in a URL. Long is not
# free: a rendered query past this is truncated clause by clause from the end,
# never mid-clause.
MAX_RENDERED_CHARS = 500

# Boolean operators, field tags and quoting mean the user is writing the query
# language themselves. Expanding an expert query is how a tool earns the
# reputation of fighting its users, so we hand it over untouched.
_EXPERT_MARKERS = re.compile(
    r"""(\bAND\b|\bOR\b|\bNOT\b|\bANDNOT\b)   # explicit Boolean, case-sensitive
        |"                                     # a quoted phrase
        |\[[A-Za-z][^\]]*\]                    # a field tag: [tiab], [MeSH Terms]
        |[()]                                  # explicit grouping
    """,
    re.VERBOSE,
)

# ── The lexicon ───────────────────────────────────────────────────────────────
#
# Groups of surface forms that retrieve the same literature. The first form is
# the one preferred when generating a variant, so it reads as the expansion an
# author would have written: "llm" -> "large language model", never the reverse.
#
# Groups must stay disjoint — one surface form, one group — or a query hits two
# groups and the substitution order decides the result. `test_query_expansion`
# asserts that property rather than trusting review.

_ACRONYMS: tuple[tuple[str, ...], ...] = (
    ("machine learning", "ml"),
    ("deep learning", "dl"),
    ("artificial intelligence", "ai"),
    ("natural language processing", "nlp"),
    ("large language model", "llm", "large language models", "llms"),
    ("vision language model", "vlm"),
    ("retrieval augmented generation", "rag"),
    ("named entity recognition", "ner"),
    ("convolutional neural network", "cnn", "convolutional neural networks"),
    ("recurrent neural network", "rnn"),
    ("graph neural network", "gnn"),
    ("generative adversarial network", "gan"),
    ("reinforcement learning", "rl"),
    ("support vector machine", "svm"),
    ("optical character recognition", "ocr"),
    ("automatic speech recognition", "asr"),
    ("internet of things", "iot"),
    ("electronic health record", "ehr", "electronic medical record", "emr"),
    ("magnetic resonance imaging", "mri"),
    ("computed tomography", "ct scan"),
    ("positron emission tomography", "pet scan"),
    ("electroencephalography", "eeg"),
    ("electrocardiography", "ecg", "ekg"),
    ("randomized controlled trial", "randomised controlled trial", "rct"),
    ("genome wide association study", "gwas"),
    ("quantitative structure activity relationship", "qsar"),
    ("single cell rna sequencing", "scrna-seq"),
    ("body mass index", "bmi"),
    ("intensive care unit", "icu"),
    ("chronic obstructive pulmonary disease", "copd"),
    ("type 2 diabetes", "t2dm", "type ii diabetes"),
    ("human immunodeficiency virus", "hiv"),
    ("coronavirus disease 2019", "covid-19", "covid19", "sars-cov-2"),
)

# British -> American and back. Cheap, high yield, and the one expansion a user
# can neither guess nor be blamed for missing.
_SPELLINGS: tuple[tuple[str, ...], ...] = (
    ("randomized", "randomised"),
    ("randomization", "randomisation"),
    ("optimization", "optimisation"),
    ("optimize", "optimise"),
    ("characterization", "characterisation"),
    ("organization", "organisation"),
    ("analyze", "analyse"),
    ("analyzed", "analysed"),
    ("modeling", "modelling"),
    ("labeled", "labelled"),
    ("tumor", "tumour"),
    ("tumors", "tumours"),
    ("behavior", "behaviour"),
    ("behavioral", "behavioural"),
    ("anemia", "anaemia"),
    ("hemoglobin", "haemoglobin"),
    ("hemorrhage", "haemorrhage"),
    ("pediatric", "paediatric"),
    ("esophageal", "oesophageal"),
    ("fetal", "foetal"),
    ("edema", "oedema"),
    ("diarrhea", "diarrhoea"),
    ("leukemia", "leukaemia"),
    ("estrogen", "oestrogen"),
    ("aging", "ageing"),
    ("fiber", "fibre"),
    ("sulfur", "sulphur"),
    ("catalog", "catalogue"),
)

# Lay term <-> the term the literature indexes it under. Deliberately short:
# every pair here is one somebody has actually typed into the search box.
_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("myocardial infarction", "heart attack"),
    ("hypertension", "high blood pressure"),
    ("cerebrovascular accident", "stroke"),
    ("gene editing", "genome editing"),
    # Not strictly synonymous — Cas9 is one nuclease used for CRISPR editing —
    # but retrieval-equivalent in practice, and probed: 25517 -> 25787 on
    # PubMed with the top-5 unchanged in substance.
    ("crispr", "cas9"),
    ("neoplasm", "malignancy"),
    ("diabetes mellitus", "diabetes"),
)

_GROUPS: tuple[tuple[str, ...], ...] = _ACRONYMS + _SPELLINGS + _SYNONYMS

# ── MeSH ──────────────────────────────────────────────────────────────────────
#
# Curated concept -> MeSH heading. Small on purpose: PubMed's own Automatic Term
# Mapping already translates most free text into MeSH, so this only carries the
# headings where ATM needs an anchor or where the user's wording is not the
# indexed one. Every heading in this map is checked against live PubMed by
# `scripts/verify_mesh_headings.py` — a heading that matches nothing is dead
# weight, and a misspelled one silently never fires.
_MESH: dict[str, str] = {
    "machine learning": "Machine Learning",
    "deep learning": "Deep Learning",
    "artificial intelligence": "Artificial Intelligence",
    "natural language processing": "Natural Language Processing",
    # Introduced by NLM recently enough that Automatic Term Mapping does not
    # always reach it from "llm" — 4726 indexed papers as of 2026-09-30.
    "large language model": "Large Language Models",
    "convolutional neural network": "Neural Networks, Computer",
    "neural network": "Neural Networks, Computer",
    "gene editing": "Gene Editing",
    "crispr": "CRISPR-Cas Systems",
    "randomized controlled trial": "Randomized Controlled Trials as Topic",
    "electronic health record": "Electronic Health Records",
    "magnetic resonance imaging": "Magnetic Resonance Imaging",
    "computed tomography": "Tomography, X-Ray Computed",
    "positron emission tomography": "Positron-Emission Tomography",
    "electroencephalography": "Electroencephalography",
    "electrocardiography": "Electrocardiography",
    "genome wide association study": "Genome-Wide Association Study",
    "medical imaging": "Diagnostic Imaging",
    "precision medicine": "Precision Medicine",
    "drug discovery": "Drug Discovery",
    "immunotherapy": "Immunotherapy",
    "gene therapy": "Genetic Therapy",
    "microbiome": "Microbiota",
    "single cell rna sequencing": "Single-Cell Analysis",
    "body mass index": "Body Mass Index",
    "intensive care unit": "Intensive Care Units",
    "chronic obstructive pulmonary disease": "Pulmonary Disease, Chronic Obstructive",
    "type 2 diabetes": "Diabetes Mellitus, Type 2",
    "diabetes mellitus": "Diabetes Mellitus",
    "human immunodeficiency virus": "HIV",
    "coronavirus disease 2019": "COVID-19",
    "tuberculosis": "Tuberculosis",
    "myocardial infarction": "Myocardial Infarction",
    "hypertension": "Hypertension",
    "breast cancer": "Breast Neoplasms",
    "lung cancer": "Lung Neoplasms",
    "alzheimer disease": "Alzheimer Disease",
    "parkinson disease": "Parkinson Disease",
    "sepsis": "Sepsis",
    "obesity": "Obesity",
    "malaria": "Malaria",
    "stroke": "Stroke",
    "depression": "Depression",
}


def _build_index() -> dict[str, tuple[str, ...]]:
    """surface form -> its group. Raises on an overlap rather than picking one."""
    index: dict[str, tuple[str, ...]] = {}
    for group in _GROUPS:
        for form in group:
            if form in index and index[form] != group:
                raise ValueError(
                    f"lexicon form {form!r} is in two groups: {index[form]} and {group}"
                )
            index[form] = group
    return index


_INDEX = _build_index()

# Longest first, so "diabetes mellitus" is matched as itself and not as
# "diabetes", and "randomized controlled trial" beats "randomized".
_CONCEPTS = tuple(sorted(set(_INDEX) | set(_MESH), key=len, reverse=True))
_CONCEPT_RES = {
    concept: re.compile(r"(?<![a-z0-9])" + re.escape(concept) + r"(?![a-z0-9])")
    for concept in _CONCEPTS
}


@dataclass(frozen=True)
class QueryPlan:
    """
    One query, rendered per source dialect.

    ``original`` is always the first clause of every rendering, so no dialect
    can return less than the unexpanded search would have. ``expert`` marks a
    query that already speaks a query language: variants and MeSH are empty and
    every dialect renders it verbatim.
    """

    original: str
    variants: tuple[str, ...] = ()
    mesh: tuple[tuple[str, str], ...] = ()  # (heading, anchor)
    expert: bool = False

    @property
    def expanded(self) -> bool:
        return bool(self.variants or self.mesh)

    def for_dialect(self, dialect: str) -> str:
        """The string to send to a source speaking *dialect*."""
        if dialect == PUBMED:
            return self._render(self.variants, self.mesh)
        if dialect == BOOLEAN:
            return self._render(self.variants, ())
        # PLAIN, and anything unrecognised: the raw query, unchanged. A new
        # dialect name that nobody taught the renderer must degrade to today's
        # behaviour, not to an empty search.
        return self.original

    def _render(self, variants: tuple[str, ...], mesh: tuple[tuple[str, str], ...]) -> str:
        if not variants and not mesh:
            # Nothing to add: send exactly what the plain sources get, so an
            # unexpandable query cannot be made worse by parenthesising it.
            return self.original
        clauses = [f"({self.original})"]
        clauses += [f"({v})" for v in variants]
        clauses += [f'("{heading}"[MeSH Terms] AND {anchor})' for heading, anchor in mesh]
        rendered = " OR ".join(clauses)
        while len(rendered) > MAX_RENDERED_CHARS and len(clauses) > 1:
            clauses.pop()
            rendered = " OR ".join(clauses)
        return rendered

    def as_dict(self) -> dict:
        """For log lines and tests — never part of an API response."""
        return {
            "original": self.original,
            "variants": list(self.variants),
            "mesh": [h for h, _ in self.mesh],
            "expert": self.expert,
        }


def _matched_concepts(lowered: str) -> list[str]:
    """
    Lexicon concepts present in *lowered*, longest first, non-overlapping.

    Overlap matters: "type 2 diabetes" contains "diabetes", and substituting
    both would produce "type 2 diabetes mellitus mellitus".
    """
    found: list[str] = []
    claimed: list[tuple[int, int]] = []
    for concept in _CONCEPTS:
        for match in _CONCEPT_RES[concept].finditer(lowered):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in claimed):
                continue
            claimed.append(span)
            found.append(concept)
            break
    return found


def _substitute(lowered: str, concept: str, replacement: str) -> str:
    return _CONCEPT_RES[concept].sub(replacement, lowered)


def _variants_for(lowered: str, concepts: list[str]) -> list[str]:
    """
    Whole-query rewrites, most useful first.

    The all-at-once rewrite leads because that is the one a speller needs:
    "randomised ... tumour ..." has to become "randomized ... tumor ..." in a
    single clause, not as two clauses that each still carry one British form.
    """
    out: list[str] = []

    def alternatives(concept: str) -> list[str]:
        group = _INDEX.get(concept)
        if not group:
            return []
        return [form for form in group if form != concept]

    substitutable = [c for c in concepts if alternatives(c)]

    if len(substitutable) > 1:
        combined = lowered
        for concept in substitutable:
            combined = _substitute(combined, concept, alternatives(concept)[0])
        out.append(combined)

    for concept in substitutable:
        for replacement in alternatives(concept)[:1]:
            out.append(_substitute(lowered, concept, replacement))

    # Dedupe, drop anything that is just the query again, keep order.
    seen = {lowered}
    unique: list[str] = []
    for candidate in out:
        candidate = " ".join(candidate.split())
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique[:MAX_VARIANTS]


def _mesh_for(lowered: str, concepts: list[str]) -> list[tuple[str, str]]:
    """
    (heading, anchor) pairs. The anchor is what is left of the query once the
    MeSH concept is removed.

    An unanchored heading is not worth sending: "Gene Editing"[MeSH Terms] on
    its own matches tens of thousands of papers that have nothing to do with
    the rest of the query, and PubMed's relevance sort would let them into the
    top rows the fan-out actually reads.
    """
    out: list[tuple[str, str]] = []
    for concept in concepts:
        heading = _MESH.get(concept)
        if not heading:
            continue
        remainder = _substitute(lowered, concept, " ")
        anchor = " ".join(
            word for word in remainder.split() if word not in GRAMMAR_STOPS
        ).strip()
        if not anchor:
            continue
        out.append((heading, anchor))
        if len(out) >= MAX_MESH_CLAUSES:
            break
    return out


def build_plan(query: str) -> QueryPlan:
    """
    Plan the outbound queries for *query*. Never raises, never returns empty.

    The raw string is preserved verbatim as ``original``; callers must keep
    using it for cache identity and for ranking. Only the per-source rendering
    changes.
    """
    # Only surrounding whitespace is removed. `original` is what a `plain`
    # source receives, and every source received the caller's string before
    # 2.3 — so this must not rewrite the query, only frame it.
    original = str(query or "").strip()
    if not original:
        return QueryPlan(original=original)

    if _EXPERT_MARKERS.search(original):
        return QueryPlan(original=original, expert=True)

    # Matching and variant generation work on a collapsed lowercase copy, so a
    # double space in the query cannot hide a concept from the lexicon.
    lowered = " ".join(original.lower().split())
    concepts = _matched_concepts(lowered)
    if not concepts:
        return QueryPlan(original=original)

    return QueryPlan(
        original=original,
        variants=tuple(_variants_for(lowered, concepts)),
        mesh=tuple(_mesh_for(lowered, concepts)),
    )
