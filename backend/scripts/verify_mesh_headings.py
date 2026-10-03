"""
Check every MeSH heading in `integrations.query_expansion._MESH` against live
PubMed.

Why this is a script and not a test: the MeSH vocabulary is a third party's
artefact and it moves — headings are introduced, renamed and retired every
year. A unit test asserting "Deep Learning" is a MeSH heading would be a test
of NLM's 2026 vocabulary pinned into our suite, and the suite must not need the
network. So the map is verified out-of-band, on demand, against the real
endpoint, exactly like `scripts/api_doctor.py` probes the search sources.

A heading that matches nothing is not a crash — the clause is OR'd, so it
cannot reduce recall — which is precisely why it has to be checked on purpose:
a misspelled heading fails silently forever.

Run:
    cd backend && python -m scripts.verify_mesh_headings
Exit code is the number of headings that matched nothing.
"""

import sys
import time

import httpx

from integrations.query_expansion import _MESH

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
TIMEOUT = 30.0
# NCBI asks for 3 requests/second without a key. One every 0.4s is inside that
# even if a retry lands in the same window.
PAUSE = 0.4


def hits(term: str) -> int:
    resp = httpx.get(
        ESEARCH,
        params={"db": "pubmed", "retmode": "json", "term": term, "retmax": 0},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return int(resp.json().get("esearchresult", {}).get("count", -1))


def main() -> int:
    dead: list[tuple[str, str]] = []
    print(f"Checking {len(_MESH)} MeSH headings against live PubMed\n")
    for concept, heading in sorted(_MESH.items(), key=lambda kv: kv[1]):
        term = f'"{heading}"[MeSH Terms]'
        try:
            count = hits(term)
        except Exception as exc:
            print(f"  ?? {heading!r:48} probe failed: {type(exc).__name__}")
            time.sleep(PAUSE)
            continue
        status = "ok" if count > 0 else "DEAD"
        print(f"  {status:4} {heading!r:48} {count:>10}   <- {concept!r}")
        if count <= 0:
            dead.append((concept, heading))
        time.sleep(PAUSE)

    print()
    if dead:
        print(f"{len(dead)} heading(s) match nothing in PubMed — fix or drop:")
        for concept, heading in dead:
            print(f"  {concept!r} -> {heading!r}")
    else:
        print("Every heading resolves.")
    return len(dead)


if __name__ == "__main__":
    sys.exit(main())
