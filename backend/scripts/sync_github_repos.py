"""Clone/refresh the GitHub paper-list repos into ``backend/data/``.

Run this at **deploy time**, never from a request handler (audit API-GH):

    python -m scripts.sync_github_repos            # all repos
    python -m scripts.sync_github_repos papers-we-love

On Render, append it to the build command:

    pip install -r requirements.txt && python -m scripts.sync_github_repos

This used to be ``POST /api/github/sync``, which let any signed-in user make the
server shell out to ``git clone`` three times — minutes of a held worker and
hundreds of megabytes of container disk per call, serialised behind a single
in-process lock that did nothing about the cost of the first caller. The repos
change roughly never, so a build step is both cheaper and more predictable than
a runtime one.

Exit code is 0 only if every requested repo synced.
"""

import logging
import subprocess
import sys

from integrations.github_knowledge import REPOS

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("sync_github_repos")

# A hung clone must not hang the whole build.
_GIT_TIMEOUT_SECONDS = 300


def sync_repository(name: str) -> bool:
    """Clone *name* if absent, otherwise fast-forward it. Never raises."""
    repo = REPOS.get(name)
    if not repo:
        logger.error("Unknown repo: %s (known: %s)", name, ", ".join(REPOS))
        return False

    clone_dir = repo["dir"]
    url = repo["url"]
    try:
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        if not clone_dir.exists():
            logger.info("Cloning %s...", name)
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(clone_dir)],
                check=True, capture_output=True, timeout=_GIT_TIMEOUT_SECONDS,
            )
            logger.info("Cloned %s.", name)
        else:
            logger.info("Pulling %s...", name)
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(clone_dir), check=True, capture_output=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
            logger.info("Pulled %s.", name)
        return True
    except subprocess.TimeoutExpired:
        logger.error("Timed out syncing %s after %ss", name, _GIT_TIMEOUT_SECONDS)
        return False
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", "replace").strip()
        logger.error("Error syncing %s: %s", name, stderr or e)
        return False


def sync_all_repositories() -> dict:
    return {name: sync_repository(name) for name in REPOS}


def main(argv: list[str]) -> int:
    names = argv[1:] or list(REPOS)
    results = {name: sync_repository(name) for name in names}
    for name, ok in results.items():
        logger.info("%s: %s", name, "ok" if ok else "FAILED")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
