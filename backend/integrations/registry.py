"""
One list of the search sources (P1 API self-heal).

The fan-out order lived as a hardcoded block of nine `_task(...)` calls inside
`_execute_search`, the display order lived as a separate `SOURCE_NAMES` tuple
beside it, and the admin desk kept a third list of its own. Removing BASE and
CORE meant editing all three, and any one of them being missed showed up as a
source that was searched but never reported, or reported but never searched.

Each entry names the source, how to reach it, and its own latency budget —
PubMed answers in about a second and arXiv routinely takes ten, so one global
timeout either cuts arXiv off or waits on PubMed for no reason.

The callable is resolved by attribute name on ``integrations.paper_search``
rather than captured at import. That module already imports every search
function into its own namespace, and that namespace is the seam the test suite
patches (``patch("integrations.paper_search.arxiv_search", ...)``); a captured
reference would quietly bypass those patches and let a unit test make real
network calls. Late binding keeps the single list *and* the seam.

``sync`` marks a source whose client is blocking — GitHub reads checked-out
repositories off disk — so the caller hands it to a thread rather than awaiting.

``dialect`` names the query language a source actually honours (2.3). It belongs
here for the same reason ``timeout`` does: it is a fact about the source, not
about the search. What each dialect renders is ``integrations.query_expansion``'s
business, and which sources earned one is recorded there with the measurements.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from integrations.query_expansion import BOOLEAN, PLAIN, PUBMED


@dataclass(frozen=True)
class Source:
    """One searchable database."""

    name: str
    # Attribute on `integrations.paper_search` holding the search callable.
    attr: str
    # Per-source ceiling. The aggregate ceiling in the caller still applies;
    # this is what stops one slow source consuming the whole budget alone.
    timeout: float = 15.0
    # True when the callable is a plain blocking function, not a coroutine one.
    sync: bool = False
    # Query language this source honours: see integrations.query_expansion.
    # `plain` is the safe default — it sends the user's raw string, which is
    # what every source received before 2.3.
    dialect: str = PLAIN

    def resolve(self):
        from integrations import paper_search

        return getattr(paper_search, self.attr)

    def call(self, query: str, limit: int):
        """The awaitable for one search against this source."""
        fn = self.resolve()
        if self.sync:
            return asyncio.to_thread(fn, query)
        return fn(query, limit=limit)


# Order is the fan-out order *and* the order per-database yield is reported in,
# so a PRISMA-style "which databases were searched" line is stable across cache
# hits. One list, one order.
#
# BASE and CORE were removed rather than left to time out: BASE answers "Access
# denied for IP address" (it allow-lists registered egress IPs, which a PaaS
# dyno does not have), and CORE's own index returns HTTP 500 "not enough
# resources were available to cover 100% of the index". Both cost the full
# per-source timeout and contributed zero papers.
SOURCES: tuple[Source, ...] = (
    Source("SemanticScholar", "s2_search", timeout=12.0),
    Source("OpenAlex", "openalex_search", timeout=12.0, dialect=BOOLEAN),
    Source("Crossref", "crossref_search", timeout=12.0),
    Source("PubMed", "pubmed_search", timeout=10.0, dialect=PUBMED),
    Source("arXiv", "arxiv_search", timeout=15.0),
    Source("OpenReview", "openreview_search", timeout=15.0),
    Source("ACLAnthology", "acl_search", timeout=15.0),
    Source("Zenodo", "zenodo_search", timeout=12.0),
    Source("GitHub", "search_github_knowledge", timeout=8.0, sync=True),
    Source("Springer", "springer_search", timeout=12.0),
    Source("EuropePMC", "europepmc_search", timeout=12.0),
    Source("DOAJ", "doaj_search", timeout=12.0),
)


def all_sources() -> tuple[Source, ...]:
    return SOURCES


def source_names() -> tuple[str, ...]:
    return tuple(s.name for s in SOURCES)
