import re
import logging
from app.models import GitHubProfile
from app.utils.http import get_client

logger = logging.getLogger(__name__)

GITHUB_SEARCH_API = "https://api.github.com/search/users"
GITHUB_USER_API = "https://api.github.com/users"
HEADERS = {"Accept": "application/vnd.github+json"}


async def search_github_query(query: str) -> list[GitHubProfile]:
    """Search GitHub for users matching a query string."""
    client = get_client()
    resp = await client.get(
        GITHUB_SEARCH_API,
        params={"q": query, "per_page": 5},
        headers=HEADERS,
    )
    if resp.status_code != 200:
        logger.warning("GitHub search failed for '%s': %d", query, resp.status_code)
        return []

    data = resp.json()
    items = data.get("items", [])
    if not items:
        return []

    profiles = []
    for item in items[:5]:
        profile = await fetch_github_user(item["login"])
        if profile:
            profiles.append(profile)
    return profiles


async def fetch_github_user(username: str) -> GitHubProfile | None:
    """Fetch a single GitHub user by exact username."""
    client = get_client()
    resp = await client.get(f"{GITHUB_USER_API}/{username}", headers=HEADERS)
    if resp.status_code != 200:
        return None

    u = resp.json()

    repos = []
    repos_resp = await client.get(
        f"{GITHUB_USER_API}/{username}/repos",
        params={"sort": "stars", "per_page": 5},
        headers=HEADERS,
    )
    if repos_resp.status_code == 200:
        for r in repos_resp.json()[:5]:
            repos.append({
                "name": r.get("name", ""),
                "description": r.get("description") or "",
                "stars": r.get("stargazers_count", 0),
                "language": r.get("language") or "",
                "url": r.get("html_url", ""),
            })

    return GitHubProfile(
        username=u.get("login", ""),
        url=u.get("html_url", ""),
        name=u.get("name"),
        bio=u.get("bio"),
        company=u.get("company"),
        location=u.get("location"),
        blog=u.get("blog") or None,
        public_repos=u.get("public_repos", 0),
        followers=u.get("followers", 0),
        following=u.get("following", 0),
        top_repos=repos,
    )


def extract_github_username(url: str) -> str | None:
    """Extract username from a GitHub URL."""
    match = re.search(r"github\.com/([a-zA-Z0-9_-]+)/?$", url.rstrip("/"))
    return match.group(1) if match else None
