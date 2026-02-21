import re
import logging
from app.providers.base import LinkedInProvider
from app.models import LinkedInProfile, BackgroundCheckRequest
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)
SERPAPI_BASE = "https://serpapi.com/search.json"


class SerpAPIProvider(LinkedInProvider):

    async def fetch_profile(self, request: BackgroundCheckRequest) -> LinkedInProfile | None:
        if request.linkedin_url:
            return await self._fetch_profile_by_url(request.linkedin_url)

        # Skip premium linkedin engine — goes straight to Google search
        # (engine=linkedin and engine=linkedin_profile require paid SerpAPI plan)
        return await self._search_via_google(request)

    async def _search_via_linkedin_engine(self, request: BackgroundCheckRequest) -> LinkedInProfile | None:
        client = get_client()
        name_parts = request.name.strip().split(maxsplit=1)
        params = {
            "engine": "linkedin",
            "first_name": name_parts[0],
            "api_key": settings.serpapi_api_key,
        }
        if len(name_parts) > 1:
            params["last_name"] = name_parts[1]
        if request.company:
            params["company"] = request.company
        if request.location:
            params["location"] = request.location

        resp = await client.get(SERPAPI_BASE, params=params)
        if resp.status_code != 200:
            logger.warning("SerpAPI linkedin engine returned %d", resp.status_code)
            return None

        data = resp.json()
        profiles = data.get("profiles", [])
        if not profiles:
            return None

        best = profiles[0]
        profile_id = best.get("id") or best.get("link", "").rstrip("/").split("/")[-1]
        return await self._fetch_profile_by_id(profile_id, fallback_data=best)

    async def _fetch_profile_by_id(self, profile_id: str, fallback_data: dict | None = None) -> LinkedInProfile | None:
        client = get_client()
        params = {
            "engine": "linkedin_profile",
            "profile_id": profile_id,
            "api_key": settings.serpapi_api_key,
        }
        resp = await client.get(SERPAPI_BASE, params=params)
        if resp.status_code != 200:
            if fallback_data:
                return self._parse_search_result(fallback_data)
            return None

        data = resp.json()
        return self._parse_profile_response(data, profile_id)

    async def _fetch_profile_by_url(self, url: str) -> LinkedInProfile | None:
        """When given a direct URL, return a minimal profile. Playwright will scrape the full data."""
        match = re.search(r"linkedin\.com/in/([^/?#]+)", url)
        if not match:
            return None
        profile_id = match.group(1)
        # Return minimal profile — Playwright provider runs concurrently and will get full data
        return LinkedInProfile(
            url=url,
            name=profile_id.replace("-", " ").title(),
        )

    async def _search_via_google(self, request: BackgroundCheckRequest) -> LinkedInProfile | None:
        """Try progressively broader Google queries to find LinkedIn profile."""
        # Build queries from most specific to broadest
        queries = []
        full_query = self._build_search_query(request)
        queries.append(full_query)

        # Name + company only
        if request.company:
            queries.append(f"{request.name} {request.company}")

        # Just name (broadest)
        queries.append(f'"{request.name}"')

        client = get_client()
        for query in queries:
            params = {
                "engine": "google",
                "q": f"site:linkedin.com/in/ {query}",
                "num": 5,
                "api_key": settings.serpapi_api_key,
            }
            resp = await client.get(SERPAPI_BASE, params=params)
            if resp.status_code != 200:
                continue

            data = resp.json()
            organic = data.get("organic_results", [])

            for result in organic:
                url = result.get("link", "")
                if "linkedin.com/in/" not in url:
                    continue

                # Relevance check — first name should appear in title
                title = result.get("title", "")
                first_name = request.name.split()[0].lower()
                if first_name not in title.lower():
                    continue

                # Build profile from Google snippet (no premium API needed)
                name_from_title = title.split(" - ")[0].strip()
                headline_from_title = title.split(" - ")[1].strip() if " - " in title else ""
                snippet = result.get("snippet", "")
                return LinkedInProfile(
                    url=url,
                    name=name_from_title,
                    headline=headline_from_title or snippet[:200],
                    raw_text=snippet,
                )

        return None

    def _parse_profile_response(self, data: dict, profile_id: str) -> LinkedInProfile:
        return LinkedInProfile(
            url=f"https://linkedin.com/in/{profile_id}",
            name=data.get("full_name") or data.get("name"),
            headline=data.get("headline"),
            location=data.get("location"),
            summary=data.get("about") or data.get("summary"),
            experience=[
                {
                    "title": exp.get("title"),
                    "company": exp.get("company"),
                    "duration": exp.get("duration"),
                    "description": exp.get("description"),
                }
                for exp in data.get("experiences", data.get("experience", []))
            ],
            education=[
                {
                    "school": edu.get("school") or edu.get("name"),
                    "degree": edu.get("degree"),
                    "field": edu.get("field_of_study"),
                }
                for edu in data.get("education", [])
            ],
            skills=data.get("skills", []),
        )

    def _parse_search_result(self, result: dict) -> LinkedInProfile:
        return LinkedInProfile(
            url=result.get("link"),
            name=result.get("name"),
            headline=result.get("headline") or result.get("occupation"),
            location=result.get("location"),
            summary=result.get("about"),
        )
