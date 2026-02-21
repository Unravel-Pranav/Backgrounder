import logging
from app.models import SearchResult
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)

SERPAPI_BASE = "https://serpapi.com/search.json"


async def search_google_query(query: str, label: str = "google") -> list[SearchResult]:
    """Run a single Google search query via SerpAPI."""
    if not settings.serpapi_api_key:
        return []

    client = get_client()
    params = {
        "engine": "google",
        "q": query,
        "num": 10,
        "api_key": settings.serpapi_api_key,
    }
    resp = await client.get(SERPAPI_BASE, params=params)
    if resp.status_code != 200:
        logger.warning("Google search failed for '%s': %d", query, resp.status_code)
        return []

    data = resp.json()
    results = []
    for item in data.get("organic_results", []):
        if "linkedin.com" in item.get("link", ""):
            continue
        results.append(SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
            source=label,
        ))
    return results[:8]


async def search_news_query(query: str) -> list[SearchResult]:
    """Run a single Google News search query via SerpAPI."""
    if not settings.serpapi_api_key:
        return []

    client = get_client()
    params = {
        "engine": "google",
        "q": query,
        "tbm": "nws",
        "num": 10,
        "api_key": settings.serpapi_api_key,
    }
    resp = await client.get(SERPAPI_BASE, params=params)
    if resp.status_code != 200:
        logger.warning("News search failed for '%s': %d", query, resp.status_code)
        return []

    data = resp.json()
    results = []
    for item in data.get("news_results", []):
        results.append(SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
            source="news",
        ))
    return results[:8]
