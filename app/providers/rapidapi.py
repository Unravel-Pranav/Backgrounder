import logging
from app.providers.base import LinkedInProvider
from app.models import LinkedInProfile, BackgroundCheckRequest
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)


class RapidAPIProvider(LinkedInProvider):

    async def fetch_profile(self, request: BackgroundCheckRequest) -> LinkedInProfile | None:
        url = request.linkedin_url
        if not url:
            logger.warning("RapidAPI provider requires a linkedin_url in the request")
            return None

        username = url.rstrip("/").split("/")[-1]
        client = get_client()
        headers = {
            "X-RapidAPI-Key": settings.rapidapi_key,
            "X-RapidAPI-Host": settings.rapidapi_host,
        }

        resp = await client.get(
            f"https://{settings.rapidapi_host}/",
            params={"username": username},
            headers=headers,
        )
        if resp.status_code != 200:
            logger.warning("RapidAPI returned %d", resp.status_code)
            return None

        data = resp.json()
        return LinkedInProfile(
            url=url,
            name=data.get("full_name") or data.get("fullName"),
            headline=data.get("headline"),
            location=data.get("location") or data.get("geo", {}).get("full", ""),
            summary=data.get("summary") or data.get("about"),
            experience=[
                {
                    "title": exp.get("title"),
                    "company": exp.get("companyName") or exp.get("company"),
                    "duration": exp.get("duration") or exp.get("dateRange"),
                    "description": exp.get("description"),
                }
                for exp in data.get("position", data.get("experiences", []))
            ],
            education=[
                {
                    "school": edu.get("schoolName") or edu.get("school"),
                    "degree": edu.get("degreeName") or edu.get("degree"),
                    "field": edu.get("fieldOfStudy"),
                }
                for edu in data.get("educations", data.get("education", []))
            ],
            skills=[
                s.get("name", s) if isinstance(s, dict) else s
                for s in data.get("skills", [])
            ],
        )
