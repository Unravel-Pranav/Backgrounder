from abc import ABC, abstractmethod
from app.models import LinkedInProfile, BackgroundCheckRequest


class LinkedInProvider(ABC):

    @abstractmethod
    async def fetch_profile(self, request: BackgroundCheckRequest) -> LinkedInProfile | None: ...

    def _build_search_query(self, request: BackgroundCheckRequest) -> str:
        parts = [request.name]
        if request.company:
            parts.append(request.company)
        if request.title:
            parts.append(request.title)
        if request.location:
            parts.append(request.location)
        parts.append("LinkedIn")
        return " ".join(parts)
