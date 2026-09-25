import os
import httpx
from integrations.http_client import pooled_client
import logging
from dotenv import load_dotenv
from services.api_telemetry import track_call

load_dotenv()

logger = logging.getLogger(__name__)


def _bibliographic_fields(item: dict) -> dict:
    """Venue, entry type and page numbers for the bibliography (2.8).

    Only keys with real values are returned. Absent keys matter: the dedupe
    merge in ``paper_search._merge_into`` fills a gap in one record from
    another, so emitting ``"venue": ""`` here would let a bare OpenAlex hit
    mask the venue a Crossref record for the same paper actually knows.
    """
    out = {}

    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    venue = (source.get("display_name") or "").strip()
    if venue:
        out["venue"] = venue
    # "journal" | "conference" | "repository" | "book series" ... The entry-type
    # mapper prefers this over guessing from the venue's name.
    host_type = (source.get("type") or "").strip()
    if host_type:
        out["venue_type"] = host_type
    publisher = (source.get("host_organization_name") or "").strip()
    if publisher:
        out["publisher"] = publisher

    # type_crossref separates journal-article from proceedings-article; plain
    # `type` calls both "article". Prefer the former, keep the latter as the
    # fallback for records Crossref never indexed (preprints, datasets).
    work_type = (item.get("type_crossref") or item.get("type") or "").strip()
    if work_type:
        out["type"] = work_type

    biblio = item.get("biblio") or {}
    volume = (biblio.get("volume") or "").strip()
    if volume:
        out["volume"] = volume
    issue = (biblio.get("issue") or "").strip()
    if issue:
        out["issue"] = issue
    first, last = (biblio.get("first_page") or "").strip(), (biblio.get("last_page") or "").strip()
    if first:
        out["pages"] = f"{first}--{last}" if last and last != first else first

    return out


async def search_papers(query: str, limit: int = 5):
    """
    Searches the OpenAlex API for research papers matching the query.
    Extracts title, authors, year, citation count, and abstract.
    """
    email = os.getenv("CROSSREF_MAILTO", "")
    headers = {"User-Agent": f"ResearchAgent/1.0 (mailto:{email})" if email else "ResearchAgent/1.0"}
    
    url = "https://api.openalex.org/works"
    params = {
        "search": query,
        "per-page": limit,
        # `type_crossref` is selected alongside `type` on purpose: OpenAlex's own
        # `type` reports "article" for a conference paper as well as a journal
        # one, so it cannot tell @article from @inproceedings. The Crossref
        # vocabulary ("journal-article" / "proceedings-article") can, and 2.8
        # needs that distinction to emit a correct entry type.
        "select": (
            "id,doi,title,publication_year,cited_by_count,authorships,"
            "abstract_inverted_index,type,type_crossref,primary_location,biblio"
        ),
    }
    if email:
        params["mailto"] = email
    api_key = os.getenv("OPENALEX_API_KEY", "")
    if api_key:
        params["api_key"] = api_key

    async with track_call("OpenAlex", "search") as rec:
        try:
            async with pooled_client(headers=headers) as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                papers = []
                for item in data.get("results", []):
                    authorships = item.get("authorships", [])
                    authors = [a.get("author", {}).get("display_name", "") for a in authorships if a.get("author")]
                    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
                    if not author_str:
                        author_str = "Unknown Authors"

                    abstract_inverted = item.get("abstract_inverted_index")
                    abstract = "No abstract available"
                    if abstract_inverted:
                        word_index = []
                        for word, positions in abstract_inverted.items():
                            for pos in positions:
                                word_index.append((pos, word))
                        word_index.sort(key=lambda x: x[0])
                        abstract = " ".join([word for pos, word in word_index])

                    doi_url = item.get("doi") or ""
                    doi = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "") if doi_url else ""
                    paper = {
                        "id": item.get("id"),
                        "title": item.get("title", "Untitled"),
                        "authors": author_str,
                        "year": item.get("publication_year", "Unknown"),
                        "citations": item.get("cited_by_count", 0),
                        "abstract": abstract,
                        "url": doi_url or item.get("id"),
                        "doi": doi,
                        "source": "OpenAlex",
                    }
                    paper.update(_bibliographic_fields(item))
                    papers.append(paper)
                rec.succeed(http_status=response.status_code, items=len(papers))
                return papers
        except Exception as e:
            rec.fail(error=str(e))
            logger.error(f"OpenAlex Error: {e}")
            return []
