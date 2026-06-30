import os
import re
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

REPOS = {
    "papers-we-love": {
        "url": "https://github.com/papers-we-love/papers-we-love.git",
        "dir": BASE_DIR / "data" / "papers-we-love",
        "topics": ["general", "algorithms", "distributed-systems", "machine-learning"],
    },
    "awesome-datascience": {
        "url": "https://github.com/academic/awesome-datascience.git",
        "dir": BASE_DIR / "data" / "awesome-datascience",
        "topics": ["data-science", "machine-learning", "deep-learning", "statistics"],
    },
    "awesome-ml-cybersecurity": {
        "url": "https://github.com/jivoi/awesome-ml-for-cybersecurity.git",
        "dir": BASE_DIR / "data" / "awesome-ml-cybersecurity",
        "topics": ["cybersecurity", "machine-learning", "threat-detection", "anomaly-detection"],
    },
}


def _sync_repo(name: str) -> bool:
    repo = REPOS[name]
    clone_dir = repo["dir"]
    url = repo["url"]
    try:
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        if not clone_dir.exists():
            print(f"Cloning {name}...")
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(clone_dir)],
                check=True, capture_output=True
            )
            print(f"Cloned {name}.")
        else:
            print(f"Pulling {name}...")
            subprocess.run(
                ["git", "pull"],
                cwd=str(clone_dir), check=True, capture_output=True
            )
            print(f"Pulled {name}.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error syncing {name}: {e}")
        return False


def sync_repository(name: str = "papers-we-love") -> bool:
    """Sync a single repo by name."""
    if name not in REPOS:
        return False
    return _sync_repo(name)


def sync_all_repositories() -> dict:
    """Sync all repos and return status per repo."""
    results = {}
    for name in REPOS:
        results[name] = _sync_repo(name)
    return results


def list_categories(name: str = "papers-we-love") -> list:
    """List top-level directories (categories) in a repo."""
    repo = REPOS.get(name)
    if not repo:
        return []
    clone_dir = repo["dir"]
    if not clone_dir.exists():
        return []
    return [
        item.name for item in sorted(clone_dir.iterdir())
        if item.is_dir() and not item.name.startswith(".")
    ]


def list_all_repos() -> dict:
    """Return all repo names with sync status and topic tags."""
    return {
        name: {
            "synced": REPOS[name]["dir"].exists(),
            "topics": REPOS[name]["topics"],
            "url": REPOS[name]["url"],
        }
        for name in REPOS
    }


def find_papers_by_category(category: str, name: str = "papers-we-love") -> list:
    """Find PDF or Markdown files in a specific category of a repo."""
    repo = REPOS.get(name)
    if not repo:
        return []
    category_dir = repo["dir"] / category
    if not category_dir.exists():
        return []
    papers = []
    for root, _, files in os.walk(category_dir):
        for file in files:
            if file.endswith((".pdf", ".md")):
                papers.append(os.path.join(root, file))
    return papers


def _extract_links_from_markdown(filepath: Path) -> list:
    """
    Parse a markdown file and extract all hyperlinks as paper references.
    Returns list of dicts with title and url.
    """
    try:
        text = filepath.read_text(encoding="utf-8", errors="ignore")
        # Match [Title](URL) pattern
        pattern = re.compile(r"\[([^\]]{5,120})\]\((https?://[^\)]+)\)")
        matches = pattern.findall(text)
        results = []
        for title, url in matches:
            title = title.strip()
            # Filter out nav/badge links
            if any(skip in title.lower() for skip in ["badge", "shield", "awesome", "click here", "back to top", "↑"]):
                continue
            results.append({"title": title, "url": url})
        return results[:50]
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return []


def search_github_knowledge(query: str) -> list:
    """
    Search across all synced repos for markdown links matching the query.
    Returns normalized paper-like dicts with source repo tag.
    """
    query_lower = query.lower()
    results = []

    for repo_name, repo_info in REPOS.items():
        clone_dir = repo_info["dir"]
        if not clone_dir.exists():
            continue

        for md_file in clone_dir.rglob("*.md"):
            # Skip hidden dirs
            if any(part.startswith(".") for part in md_file.parts):
                continue

            links = _extract_links_from_markdown(md_file)
            for link in links:
                if query_lower in link["title"].lower():
                    results.append({
                        "id": link["url"],
                        "title": link["title"],
                        "authors": f"via {repo_name}",
                        "year": "N/A",
                        "citations": 0,
                        "abstract": f"Reference from {repo_name} knowledge base — {md_file.name}",
                        "url": link["url"],
                        "source": f"GitHub/{repo_name}",
                    })

        if len(results) >= 20:
            break

    return results[:20]
