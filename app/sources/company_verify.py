import asyncio
import logging
from app.models import CompanyCheck, ResumeData
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)

SERPAPI_BASE = "https://serpapi.com/search.json"


async def verify_companies(resume: ResumeData) -> list[CompanyCheck]:
    """Verify each company from the resume actually exists."""
    companies = set()
    if resume.company:
        companies.add(resume.company.strip())
    for exp in resume.experience:
        co = exp.get("company", "").strip()
        if co:
            companies.add(co)

    if not companies or not settings.serpapi_api_key:
        return []

    # Verify all companies concurrently
    coros = [_check_company(co) for co in companies]
    results = await asyncio.gather(*coros, return_exceptions=True)

    checks = []
    for r in results:
        if isinstance(r, CompanyCheck):
            checks.append(r)
    return checks


async def _check_company(company_name: str) -> CompanyCheck:
    """Search Google for a company to verify it exists."""
    client = get_client()
    params = {
        "engine": "google",
        "q": f'"{company_name}" company',
        "num": 5,
        "api_key": settings.serpapi_api_key,
    }
    resp = await client.get(SERPAPI_BASE, params=params)
    if resp.status_code != 200:
        return CompanyCheck(name=company_name, verified=False, description="Search failed")

    data = resp.json()
    organic = data.get("organic_results", [])
    knowledge = data.get("knowledge_graph", {})

    # Check knowledge graph first (Google's own verification)
    if knowledge:
        kg_title = knowledge.get("title", "").lower()
        if company_name.lower() in kg_title or kg_title in company_name.lower():
            desc = knowledge.get("description", "")
            website = knowledge.get("website") or ""
            return CompanyCheck(
                name=company_name,
                verified=True,
                evidence_url=website,
                description=f"Google Knowledge Graph: {desc[:150]}" if desc else "Found in Google Knowledge Graph",
            )

    # Check organic results for company website or LinkedIn
    for result in organic[:5]:
        url = result.get("link", "")
        title = result.get("title", "").lower()
        snippet = result.get("snippet", "")

        # Company has its own website or LinkedIn page
        if (
            "linkedin.com/company/" in url
            or company_name.lower() in title
        ):
            return CompanyCheck(
                name=company_name,
                verified=True,
                evidence_url=url,
                description=snippet[:150] if snippet else f"Found at {url}",
            )

    # If we got some results but none matched strongly
    if organic:
        return CompanyCheck(
            name=company_name,
            verified=False,
            description=f"Search returned results but no strong match for '{company_name}' as a company",
        )

    return CompanyCheck(
        name=company_name,
        verified=False,
        description="No search results found for this company",
    )
