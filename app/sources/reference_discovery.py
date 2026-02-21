import asyncio
import re
import logging
from app.models import ReferenceContact, BackgroundCheckRequest, ResumeData
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)

SERPAPI_BASE = "https://serpapi.com/search.json"


async def discover_references(
    request: BackgroundCheckRequest,
    resume: ResumeData | None = None,
) -> list[ReferenceContact]:
    """
    For each company in the person's background, find real people
    (HR, managers, colleagues) who could verify employment.
    """
    if not settings.serpapi_api_key:
        return []

    person_name = request.name

    # Collect all companies + roles
    companies = []
    if request.company:
        companies.append({"company": request.company, "title": request.title or ""})
    if resume:
        for exp in resume.experience:
            co = exp.get("company", "").strip()
            title = exp.get("title", "").strip()
            if co and co not in [c["company"] for c in companies]:
                companies.append({"company": co, "title": title})

    if not companies:
        return []

    # Search for references at each company concurrently
    coros = []
    for entry in companies[:4]:  # cap at 4 companies
        coros.append(_find_contacts_at_company(
            person_name=person_name,
            company=entry["company"],
            person_title=entry["title"],
        ))

    results = await asyncio.gather(*coros, return_exceptions=True)

    all_contacts = []
    seen = set()
    for r in results:
        if isinstance(r, list):
            for contact in r:
                key = contact.linkedin_url or contact.name
                if key not in seen:
                    seen.add(key)
                    all_contacts.append(contact)

    return all_contacts


async def _find_contacts_at_company(
    person_name: str,
    company: str,
    person_title: str,
) -> list[ReferenceContact]:
    """Find HR, managers, and colleagues at a specific company."""
    client = get_client()
    contacts = []

    # Build multiple targeted queries
    queries = [
        # HR / Talent people who can verify employment
        (f'site:linkedin.com/in/ "{company}" (HR OR "Human Resources" OR "Talent Acquisition" OR "People Operations")',
         "HR / People Ops"),
        # Managers and directors
        (f'site:linkedin.com/in/ "{company}" (Manager OR Director OR "Team Lead" OR VP OR Founder OR CEO OR CTO)',
         "Management"),
        # Anyone at the company (broadest — catches small companies)
        (f'site:linkedin.com/in/ "{company}"',
         "Colleague"),
    ]

    # If we know the person's role, search for peers in similar function
    if person_title:
        dept_keywords = _extract_department(person_title)
        if dept_keywords:
            queries.append((
                f'site:linkedin.com/in/ "{company}" ({dept_keywords})',
                "Same Department",
            ))

    person_first = person_name.split()[0].lower()

    for query, category in queries:
        params = {
            "engine": "google",
            "q": query,
            "num": 5,
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

            if "linkedin.com/in/" not in url:
                continue

            # Skip the person themselves
            if person_first in title.lower().split(" - ")[0].lower():
                continue

            # Extract name and role from LinkedIn title format: "Name - Title - Company"
            name, role = _parse_linkedin_title(title)
            if not name:
                continue

            contacts.append(ReferenceContact(
                name=name,
                title=role,
                company=company,
                linkedin_url=url,
                category=category,
                snippet=snippet[:200] if snippet else "",
            ))

    return contacts


def _parse_linkedin_title(title: str) -> tuple[str, str]:
    """Parse 'John Doe - Senior Manager - Company | LinkedIn' into (name, role)."""
    # Remove " | LinkedIn" suffix
    title = re.sub(r'\s*[\|–\-]\s*LinkedIn.*$', '', title, flags=re.IGNORECASE)
    parts = [p.strip() for p in re.split(r'\s*[\|–\-]\s*', title) if p.strip()]
    if not parts:
        return ("", "")
    name = parts[0]
    role = parts[1] if len(parts) > 1 else ""
    return (name, role)


def _extract_department(title: str) -> str | None:
    """Extract department/function keywords from a job title."""
    title_lower = title.lower()

    dept_map = {
        "engineer": "Engineer OR Developer OR Software",
        "developer": "Engineer OR Developer OR Software",
        "software": "Engineer OR Developer OR Software",
        "data": "Data OR Analytics OR ML",
        "design": "Design OR UX OR UI",
        "product": "Product OR PM",
        "market": "Marketing OR Growth",
        "sales": "Sales OR Business Development",
        "finance": "Finance OR Accounting",
        "legal": "Legal OR Compliance",
        "ops": "Operations OR DevOps OR SRE",
        "devops": "Operations OR DevOps OR SRE",
        "security": "Security OR InfoSec OR Cybersecurity",
        "research": "Research OR Scientist OR R&D",
        "machine learning": "ML OR AI OR Machine Learning",
        "frontend": "Frontend OR React OR UI",
        "backend": "Backend OR API OR Server",
        "fullstack": "Full Stack OR Fullstack OR Developer",
        "full stack": "Full Stack OR Fullstack OR Developer",
        "python": "Python OR Backend OR Developer",
    }

    for keyword, dept_query in dept_map.items():
        if keyword in title_lower:
            return dept_query

    return None
