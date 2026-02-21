import httpx
from app.config import settings

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=settings.max_concurrency * 2,
                max_keepalive_connections=settings.max_concurrency,
            ),
        )
    return _client


async def close_client():
    global _client
    if _client:
        await _client.aclose()
        _client = None
