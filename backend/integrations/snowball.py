"""
Citation snowballing (2.2) — follow citations in both directions.

Keyword search finds papers that *use the words you typed*. Snowballing finds
papers that the literature itself links to the ones you already trust, which is
how a real survey is assembled:

  * **backward** — the works a seed cites. Its reference list: the prior art the
    authors themselves considered load-bearing.
  * **forward** — the works citing a seed. Everything built on it since,
    including the papers published after whatever cut-off the seed had.

Two graphs are traversed, not one. OpenAlex and Semantic Scholar disagree about
roughly a third of any paper's edges — S2 parses reference lists out of PDFs and
resolves what it can, OpenAlex indexes publisher-deposited references — so
asking both and merging is the difference between a usable expansion and a
partial one. Records come back in the same shape keyword search produces and go
through the same dedupe, so a snowballed paper and a searched one merge into one
card rather than appearing twice.

Ranking is *not* the keyword ranker's job here. The signal that matters for an
expansion is **co-citation strength**: a paper five of your seeds all point at is
central to the area whether or not its title contains your query terms. That is
the dominant term in `_score`, with the lexical/citation ranker second.

It also has to carry the forward direction, where the graphs are uneven.
OpenAlex sorts citing works by citation count; Semantic Scholar's `/citations`
takes no sort parameter and returns an arbitrary page, which for a heavily cited
seed is mostly last month's papers citing it in passing. Co-citation and the
`isInfluential` flag are what pull the substantive ones back to the top.

Seams
-----
Retries, per-source health, telemetry and the durable cache all go through the
existing modules (`core.retry`, `services.api_health`, `services.api_telemetry`,
`core.shared_store`). The two fetchers are bound as module attributes
(`openalex_related`, `s2_related`) so the test suite can patch
``integrations.snowball.openalex_related`` the way it already patches
``integrations.paper_search.arxiv_search``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field

from core import shared_store
from core.paper_identity import identity_keys, paper_identity
from integrations.openalex import fetch_related as openalex_related
from integrations.paper_search import SourceOutcome, _deduplicate, _rank_papers
from integrations.semanticscholar import fetch_related as s2_related
from services import api_health

logger = logging.getLogger(__name__)

DIRECTIONS = ("backward", "forward")

# Which graph each source is asked for, in the order outcomes are reported.
# Same shape as `integrations.registry.SOURCES` and for the same reason: one
# list, so a source cannot be traversed without being reported.
_GRAPHS: tuple[tuple[str, str], ...] = (
    ("SemanticScholar", "s2_related"),
    ("OpenAlex", "openalex_related"),
)

# Defaults. A snowball is O(seeds x sources x directions) requests, so the seed
# cap is the single most important number here: 10 seeds against 2 graphs in
# both directions is 40 requests, which the per-source rate limits tolerate.
# 25 would be 100 and Semantic Scholar starts 429ing.
MAX_SEEDS = 10
PER_SEED = 25
DEFAULT_LIMIT = 30

# At most this many citation requests are in flight at once. Unbounded fan-out
# over 40 requests is what turns one snowball into a rate-limit outage for every
# other user of the deployment.
MAX_CONCURRENCY = 6

SNOWBALL_TTL = 1800


@dataclass(frozen=True)
class SnowballMeta:
    """How an expansion was obtained, for the same reasons `SearchMeta` exists."""

    cache: str  # "miss" | "exact"
    direction: str
    seeds_used: int = 0
    seeds_unresolvable: int = 0
    sources: tuple[SourceOutcome, ...] = field(default_factory=tuple)

    def sources_as_dicts(self) -> list[dict]:
        return [s.as_dict() for s in self.sources]

    def as_dict(self) -> dict:
        return {
            "cache": self.cache,
            "direction": self.direction,
            "seeds_used": self.seeds_used,
            "seeds_unresolvable": self.seeds_unresolvable,
            "sources": self.sources_as_dicts(),
        }


def _resolve(attr: str):
    """Late binding, so patching this module's attribute is honoured."""
    import integrations.snowball as module

    return getattr(module, attr)


def _requested_directions(direction: str) -> tuple[str, ...]:
    if direction == "both":
        return DIRECTIONS
    if direction in DIRECTIONS:
        return (direction,)
    raise ValueError(f"direction must be 'backward', 'forward' or 'both'; got {direction!r}")


def _seed_keys(seeds: list[dict]) -> set[str]:
    """Every identity key of the seed set.

    An expansion must never hand back a paper the user is already looking at:
    the reference lists of five papers in one area overlap heavily with each
    other, so without this the first page is mostly seeds.
    """
    keys: set[str] = set()
    for seed in seeds:
        keys.update(identity_keys(seed))
    return keys


def _cache_key(seeds: list[dict], directions: tuple[str, ...], per_seed: int) -> str:
    """Digest of the seed identities, so the same seed set in a different order
    is one cache entry rather than N! of them."""
    identities = sorted({paper_identity(seed) for seed in seeds})
    blob = "|".join(identities) + f"::{','.join(directions)}::{per_seed}"
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=16).hexdigest()


@dataclass
class _Provenance:
    """Which seeds reached a paper, and how."""

    directions: set[str] = field(default_factory=set)
    seeds: set[str] = field(default_factory=set)
    influential: bool = False


def _label(seed: dict) -> str:
    title = (seed.get("title") or "").strip()
    return title or paper_identity(seed)


async def _one_call(
    source_name: str,
    attr: str,
    seed: dict,
    direction: str,
    per_seed: int,
    semaphore: asyncio.Semaphore,
) -> tuple[str, str, dict, list[dict], Exception | None]:
    """One (source, seed, direction) fetch. Never raises — the caller needs a
    result row for every request it made, including the failures."""
    async with semaphore:
        try:
            papers = await _resolve(attr)(seed, direction, limit=per_seed)
            return source_name, direction, seed, papers or [], None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - fetchers swallow their own
            return source_name, direction, seed, [], exc


def _score(
    paper: dict,
    provenance: _Provenance,
    seeds_used: int,
) -> float:
    """Rank within an expansion.

    Co-citation dominates (60%): the whole point of snowballing is to surface
    what the seed set collectively points at, and "four of your five papers
    cite this" is a far stronger signal than any keyword overlap. The lexical +
    citation + recency ranker contributes 30% as the tie-break among papers
    reached by the same number of seeds, and S2's `isInfluential` verdict the
    last 10%.
    """
    reach = len(provenance.seeds) / seeds_used if seeds_used else 0.0
    lexical = float(paper.get("_relevance_rank") or 0.0)
    influential = 1.0 if provenance.influential else 0.0
    return (0.60 * min(reach, 1.0)) + (0.30 * lexical) + (0.10 * influential)


async def snowball(
    seeds: list[dict],
    *,
    direction: str = "both",
    query: str = "",
    per_seed: int = PER_SEED,
    limit: int = DEFAULT_LIMIT,
    max_seeds: int = MAX_SEEDS,
    total_timeout: float = 25.0,
) -> tuple[list[dict], SnowballMeta]:
    """Papers reached from *seeds* by following citations.

    *query* is only used for the lexical tie-break; an expansion is well defined
    without it. Every returned paper carries its provenance:

      ``snowball_direction``  "backward" | "forward" | "both"
      ``snowball_seeds``      titles of the seeds that reached it
      ``snowball_seed_count`` how many distinct seeds reached it

    The caller needs those to explain the result — "this appeared because 3 of
    your papers cite it" is the whole justification for showing it, and an
    expansion whose rows cannot be explained is indistinguishable from noise.
    """
    directions = _requested_directions(direction)
    usable = [s for s in seeds if isinstance(s, dict) and (s.get("title") or s.get("doi") or s.get("id"))]
    seeds_used = usable[:max_seeds]
    if not seeds_used:
        return [], SnowballMeta(cache="miss", direction=direction)

    cache_key = _cache_key(seeds_used, directions, per_seed)
    cached = await shared_store.get(shared_store.NS_SNOWBALL, cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("papers"), list) and cached["papers"]:
        return cached["papers"][:limit], SnowballMeta(
            cache="exact",
            direction=direction,
            seeds_used=len(seeds_used),
            seeds_unresolvable=int(cached.get("seeds_unresolvable") or 0),
            sources=_outcomes_from_dicts(cached.get("sources")),
        )

    seed_keys = _seed_keys(seeds_used)
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    started = time.perf_counter()

    by_name: dict[str, SourceOutcome] = {}
    tasks = []
    for source_name, attr in _GRAPHS:
        reason = api_health.blocked_reason(source_name)
        if reason is not None and not api_health.allow(source_name):
            by_name[source_name] = SourceOutcome(name=source_name, status="skipped", error=reason)
            continue
        for seed in seeds_used:
            for one_direction in directions:
                tasks.append(asyncio.create_task(
                    _one_call(source_name, attr, seed, one_direction, per_seed, semaphore)
                ))

    done: set = set()
    if tasks:
        done, pending = await asyncio.wait(set(tasks), timeout=total_timeout)
        if pending:
            logger.warning(
                "snowball: %.0fs ceiling reached with %d of %d citation request(s) outstanding",
                total_timeout, len(pending), len(tasks),
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    provenance: dict[str, _Provenance] = {}
    records: list[dict] = []
    # Per source: how many requests landed, how many produced anything, how
    # many failed. A source that answered "no edges" for every seed is a very
    # different result from one that errored on every seed, and the UI must be
    # able to tell them apart.
    counts: dict[str, dict[str, int]] = {name: {"papers": 0, "errors": 0} for name, _ in _GRAPHS}
    seeds_with_a_hit: set[str] = set()

    for task in done:
        try:
            source_name, one_direction, seed, papers, exc = task.result()
        except asyncio.CancelledError:
            continue
        stats = counts.setdefault(source_name, {"papers": 0, "errors": 0})
        if exc is not None:
            stats["errors"] += 1
            continue
        if papers:
            seeds_with_a_hit.add(paper_identity(seed))
        seed_label = _label(seed)
        for paper in papers:
            keys = identity_keys(paper)
            if not keys or seed_keys.intersection(keys):
                # No identity, or it *is* one of the seeds.
                continue
            stats["papers"] += 1
            # Registered under every key so the record survives dedupe merging
            # two copies that each knew a different identifier.
            entry = None
            for key in keys:
                existing = provenance.get(key)
                if existing is not None:
                    entry = existing
                    break
            if entry is None:
                entry = _Provenance()
            entry.directions.add(one_direction)
            entry.seeds.add(seed_label)
            entry.influential = entry.influential or bool(paper.get("influential"))
            for key in keys:
                provenance.setdefault(key, entry)
            records.append(paper)

    for source_name, _ in _GRAPHS:
        if source_name in by_name:
            continue  # circuit open — outcome already recorded
        stats = counts.get(source_name, {"papers": 0, "errors": 0})
        if stats["papers"]:
            status = "ok"
        elif stats["errors"]:
            status = "error"
        else:
            status = "empty"
        by_name[source_name] = SourceOutcome(
            name=source_name,
            status=status,
            count=stats["papers"],
            ms=elapsed_ms,
            error=f"{stats['errors']} request(s) failed" if stats["errors"] else None,
        )
    sources = tuple(by_name[name] for name, _ in _GRAPHS if name in by_name)

    unique = _deduplicate(records)
    _rank_papers(query, unique)
    for paper in unique:
        entry = _merged_provenance(paper, provenance)
        found_in = sorted(entry.directions)
        paper["snowball_direction"] = "both" if len(found_in) > 1 else (found_in[0] if found_in else direction)
        paper["snowball_seeds"] = sorted(entry.seeds)
        paper["snowball_seed_count"] = len(entry.seeds)
        paper["_snowball_rank"] = round(_score(paper, entry, len(seeds_used)), 4)
        paper.pop("influential", None)
    unique.sort(key=lambda p: p.get("_snowball_rank", 0.0), reverse=True)

    # Seeds that reached nothing anywhere. Almost always a paper with no DOI and
    # no arXiv id — neither graph can be queried for it, and saying so beats
    # letting the user wonder why 10 seeds produced 4 papers. Zero when no graph
    # was traversed at all: with both circuits open every seed would be reported
    # as untraceable, which blames the seeds for an outage the `sources` rows
    # already state plainly.
    unresolvable = (len(seeds_used) - len(seeds_with_a_hit)) if tasks else 0

    if unique:
        await shared_store.set(
            shared_store.NS_SNOWBALL,
            cache_key,
            {
                "papers": unique,
                "sources": [s.as_dict() for s in sources],
                "seeds_unresolvable": unresolvable,
            },
            SNOWBALL_TTL,
        )

    return unique[:limit], SnowballMeta(
        cache="miss",
        direction=direction,
        seeds_used=len(seeds_used),
        seeds_unresolvable=unresolvable,
        sources=sources,
    )


def _merged_provenance(paper: dict, provenance: dict[str, _Provenance]) -> _Provenance:
    """Union the provenance of every identity the surviving record carries.

    Dedupe merges records field by field, and a `set` is not a field it can
    union — so provenance is tracked beside the records and reattached here.
    Without this, a paper that both graphs returned from different seeds would
    report only the seeds of whichever copy happened to win the merge.
    """
    merged = _Provenance()
    seen: set[int] = set()
    for key in identity_keys(paper):
        entry = provenance.get(key)
        if entry is None or id(entry) in seen:
            continue
        seen.add(id(entry))
        merged.directions |= entry.directions
        merged.seeds |= entry.seeds
        merged.influential = merged.influential or entry.influential
    return merged


def _outcomes_from_dicts(raw) -> tuple[SourceOutcome, ...]:
    if not isinstance(raw, list):
        return ()
    fields = {"name", "status", "count", "ms", "error"}
    return tuple(
        SourceOutcome(**{k: v for k, v in item.items() if k in fields})
        for item in raw
        if isinstance(item, dict) and item.get("name")
    )
