from app.providers.base import LinkedInProvider
from app.providers.serpapi import SerpAPIProvider
from app.providers.playwright_scraper import PlaywrightProvider
from app.providers.proxycurl import ProxycurlProvider
from app.providers.rapidapi import RapidAPIProvider

_REGISTRY: dict[str, type[LinkedInProvider]] = {
    "serpapi": SerpAPIProvider,
    "playwright": PlaywrightProvider,
    "proxycurl": ProxycurlProvider,
    "rapidapi": RapidAPIProvider,
}


def get_provider(name: str) -> LinkedInProvider:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown LinkedIn provider '{name}'. "
            f"Choose from: {', '.join(_REGISTRY)}"
        )
    return cls()
