import logging
from app.providers.base import LinkedInProvider
from app.models import LinkedInProfile, BackgroundCheckRequest
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)

PROXYCURL_ENDPOINT = "https://nubela.co/proxycurl/api/v2/linkedin"
PROXYCURL_SEARCH = "https://nubela.co/proxycurl/api/search/person"


class ProxycurlProvider(LinkedInProvider):

    async def fetch_profile(self, request: BackgroundCheckRequest) -> LinkedInProfile | None:
        url = request.linkedin_url
        if not url:
            url = await self._resolve_url(request)
        if not url:
            return None

        client = get_client()
        headers = {"Authorization": f"Bearer {settings.proxycurl_api_key}"}
        resp = await client.get(
            PROXYCURL_ENDPOINT,
            params={"url": url},
            headers=headers,
        )
        if resp.status_code != 200:
            logger.warning("Proxycurl returned %d: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        return LinkedInProfile(
            url=url,
            name=data.get("full_name"),
            headline=data.get("headline"),
            location=data.get("city") or data.get("country_full_name"),
            summary=data.get("summary"),
            experience=[
                {
                    "title": exp.get("title"),
                    "company": exp.get("company"),
                    "duration": f"{exp.get('starts_at', {}).get('year', '')} - {exp.get('ends_at', {}).get('year', 'Present')}",
                    "description": exp.get("description"),
                }
                for exp in data.get("experiences", [])
            ],
            education=[
                {
                    "school": edu.get("school"),
                    "degree": edu.get("degree_name"),
                    "field": edu.get("field_of_study"),
                }
                for edu in data.get("education", [])
            ],
            skills=data.get("skills", []),
        )

    async def _resolve_url(self, request: BackgroundCheckRequest) -> str | None:
        client = get_client()
        headers = {"Authorization": f"Bearer {settings.proxycurl_api_key}"}
        params = {
            "first_name": request.name.split()[0],
            "last_name": " ".join(request.name.split()[1:]),
        }
        if request.company:
            params["current_company_name"] = request.company
        if request.location:
            params["country"] = request.location

        resp = await client.get(PROXYCURL_SEARCH, params=params, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("results", [])
        if results:
            return results[0].get("linkedin_profile_url")
        return None
