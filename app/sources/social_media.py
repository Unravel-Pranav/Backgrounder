import asyncio
import logging
from app.models import SocialProfile, BackgroundCheckRequest
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)

SERPAPI_BASE = "https://serpapi.com/search.json"

# Grouped into batches — each batch becomes ONE Google search with OR operators
PLATFORM_BATCHES = [
    {
        "label": "Major Social",
        "platforms": {
            "Twitter/X": ["twitter.com", "x.com"],
            "Facebook": ["facebook.com"],
            "Instagram": ["instagram.com"],
            "Reddit": ["reddit.com/user"],
        },
    },
    {
        "label": "Dev Platforms",
        "platforms": {
            "Stack Overflow": ["stackoverflow.com/users"],
            "Medium": ["medium.com"],
            "Dev.to": ["dev.to"],
            "Hashnode": ["hashnode.dev"],
            "HackerNoon": ["hackernoon.com"],
        },
    },
    {
        "label": "Code Platforms",
        "platforms": {
            "GitLab": ["gitlab.com"],
            "Bitbucket": ["bitbucket.org"],
            "npm": ["npmjs.com/~"],
            "PyPI": ["pypi.org/user"],
            "HuggingFace": ["huggingface.co"],
        },
    },
    {
        "label": "Creative Platforms",
        "platforms": {
            "Behance": ["behance.net"],
            "Dribbble": ["dribbble.com"],
            "Figma": ["figma.com/@"],
            "CodePen": ["codepen.io"],
        },
    },
    {
        "label": "Research & Competitions",
        "platforms": {
            "Kaggle": ["kaggle.com"],
            "Google Scholar": ["scholar.google.com"],
            "ResearchGate": ["researchgate.net/profile"],
            "LeetCode": ["leetcode.com/u"],
            "HackerRank": ["hackerrank.com/profile"],
            "Codeforces": ["codeforces.com/profile"],
        },
    },
    {
        "label": "Content Platforms",
        "platforms": {
            "YouTube": ["youtube.com"],
            "Substack": ["substack.com"],
            "Quora": ["quora.com/profile"],
            "Speakerdeck": ["speakerdeck.com"],
            "SlideShare": ["slideshare.net"],
        },
    },
]


async def scan_social_media(request: BackgroundCheckRequest) -> list[SocialProfile]:
    """Search for the person across 29 platforms using batched Google queries."""
    if not settings.serpapi_api_key:
        return []

    name = request.name

    # Run all batches concurrently — NO company in social queries (people don't use it)
    coros = [_search_batch(name, batch) for batch in PLATFORM_BATCHES]
    results = await asyncio.gather(*coros, return_exceptions=True)

    profiles = []
    seen_urls = set()
    for r in results:
        if isinstance(r, list):
            for p in r:
                if p.url not in seen_urls:
                    seen_urls.add(p.url)
                    profiles.append(p)

    # If first pass found very little, retry key platforms with relaxed (unquoted) name
    if len(profiles) < 2:
        retry_profiles = await _retry_key_platforms(name)
        for p in retry_profiles:
            if p.url not in seen_urls:
                seen_urls.add(p.url)
                profiles.append(p)

    return profiles


async def _search_batch(name: str, batch: dict) -> list[SocialProfile]:
    """Search one batch of platforms — uses just the person's name, no company."""
    all_sites = []
    site_to_platform = {}
    for platform, sites in batch["platforms"].items():
        for site in sites:
            all_sites.append(site)
            site_to_platform[site] = platform

    site_query = " OR ".join(f"site:{s}" for s in all_sites)

    # Try exact name first, then parts
    name_parts = name.strip().split()
    queries = [
        f'({site_query}) "{name}"',  # exact full name
    ]
    # If name has 2+ parts, also try first+last without quotes
    if len(name_parts) >= 2:
        queries.append(f'({site_query}) {name_parts[0]} {name_parts[-1]}')

    client = get_client()
    profiles = []
    name_lower_parts = [p.lower() for p in name_parts]

    for query in queries:
        params = {
            "engine": "google",
            "q": query,
            "num": 10,
            "api_key": settings.serpapi_api_key,
        }
        resp = await client.get(SERPAPI_BASE, params=params)
        if resp.status_code != 200:
            continue

        data = resp.json()
        for item in data.get("organic_results", []):
            url = item.get("link", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            text = f"{title} {snippet}".lower()

            # Relevance: at least first name OR last name must appear
            if not any(part in text for part in name_lower_parts):
                continue

            platform = _match_platform(url, site_to_platform, all_sites)
            if not platform:
                continue

            username = _extract_username(url, platform)
            profiles.append(SocialProfile(
                platform=platform,
                url=url,
                username=username,
                snippet=snippet[:200] if snippet else title[:200],
            ))

        # If first query (exact name) got results, skip the relaxed query
        if profiles:
            break

    return profiles


async def _retry_key_platforms(name: str) -> list[SocialProfile]:
    """Retry individual searches on the most important platforms with relaxed queries."""
    key_platforms = [
        ("Twitter/X", "twitter.com"),
        ("Instagram", "instagram.com"),
        ("YouTube", "youtube.com"),
        ("LeetCode", "leetcode.com"),
        ("Medium", "medium.com"),
    ]

    async def _search_single(platform: str, site: str) -> list[SocialProfile]:
        client = get_client()
        name_parts = name.strip().split()
        # Very broad: just first + last name, no quotes
        q = f"site:{site} {name_parts[0]}"
        if len(name_parts) > 1:
            q += f" {name_parts[-1]}"

        params = {"engine": "google", "q": q, "num": 5, "api_key": settings.serpapi_api_key}
        resp = await client.get(SERPAPI_BASE, params=params)
        if resp.status_code != 200:
            return []

        results = []
        name_lower = [p.lower() for p in name_parts]
        for item in resp.json().get("organic_results", []):
            url = item.get("link", "")
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            text = f"{title} {snippet}".lower()

            if not any(p in text for p in name_lower):
                continue
            if site not in url:
                continue

            username = _extract_username(url, platform)
            results.append(SocialProfile(
                platform=platform, url=url, username=username,
                snippet=snippet[:200] if snippet else title[:200],
            ))
        return results

    coros = [_search_single(p, s) for p, s in key_platforms]
    results = await asyncio.gather(*coros, return_exceptions=True)

    profiles = []
    for r in results:
        if isinstance(r, list):
            profiles.extend(r)
    return profiles


def _match_platform(url: str, site_to_platform: dict, all_sites: list) -> str | None:
    url_lower = url.lower()
    for site in all_sites:
        clean_site = site.replace("*.", "")
        if clean_site in url_lower:
            return site_to_platform.get(site)
    return None


def _extract_username(url: str, platform: str) -> str | None:
    try:
        parts = url.rstrip("/").split("/")
        parts = [p for p in parts if p]

        if "stackoverflow" in url:
            idx = parts.index("users")
            return parts[idx + 2] if len(parts) > idx + 2 else parts[idx + 1]
        elif "medium.com" in url:
            for p in parts:
                if p.startswith("@"):
                    return p
            return parts[-1] if parts[-1] not in ("medium.com", "") else None
        elif "reddit.com" in url:
            if "user" in parts:
                idx = parts.index("user")
                return parts[idx + 1] if len(parts) > idx + 1 else None
        elif "scholar.google" in url:
            return None
        elif "leetcode.com" in url or "hackerrank.com" in url or "codeforces.com" in url:
            return parts[-1]
        elif "huggingface.co" in url:
            return parts[-1]
        elif "figma.com" in url or "youtube.com" in url:
            for p in parts:
                if p.startswith("@"):
                    return p
            if "youtube.com" in url and ("channel" in parts or "c" in parts):
                return parts[-1]
        elif "codepen.io" in url:
            return parts[-1]

        last = parts[-1] if parts else None
        if last and "." not in last and last not in ("profile", "users", "user", "u"):
            return last
        return None
    except (ValueError, IndexError):
        return None
