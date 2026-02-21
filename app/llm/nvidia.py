import json
import logging
from app.config import settings
from app.models import BackgroundCheckRequest, AggregatedData
from app.utils.http import get_client

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a professional background research analyst and due-diligence investigator. \
Given data about a person collected from their resume, LinkedIn, GitHub, Google search, and news articles, \
produce a structured background report WITH a verdict on whether their background checks out.

IMPORTANT: The data may contain information about MULTIPLE different people with the same name. \
You must carefully analyze whether all the data points refer to the same individual or different people.

You MUST respond with valid JSON containing exactly these keys:

- "summary": A 2-4 sentence executive summary of who this person is.
- "professional_background": A 2-3 paragraph narrative of their career trajectory, expertise, and notable positions.
- "key_highlights": A list of 3-7 bullet points (strings) covering the most important facts.

- "identity_verification": An object with these keys:
  - "confidence": One of "high", "medium", or "low".
  - "reasoning": 1-3 sentences explaining why.
  - "multiple_people_detected": boolean.
  - "profiles_found": List of objects with "source", "name", "description".
  - "cross_reference_notes": List of strings noting matches or mismatches across sources.

- "verdict": An object with these keys:
  - "rating": One of "clean", "caution", or "red_flags".
    - "clean" = background looks solid, claims match online presence, no concerns.
    - "caution" = some inconsistencies or missing data, but nothing alarming. Needs more verification.
    - "red_flags" = significant mismatches, false claims, or concerning findings.
  - "score": Integer 0-100. 100 = perfect background, 0 = completely fraudulent.
    - 80-100: Clean — everything checks out.
    - 50-79: Caution — some gaps or minor inconsistencies.
    - 0-49: Red flags — serious concerns.
  - "summary": 2-3 sentence overall verdict explaining the rating.
  - "resume_vs_online": List of strings comparing resume claims to what was found online. \
For each claim, note whether it was VERIFIED, UNVERIFIED, or CONTRADICTED. Examples:
    - "VERIFIED: Resume says 'Senior Developer at CVS Health' — LinkedIn confirms this role."
    - "UNVERIFIED: Resume claims 'Led team of 15 engineers' — no online evidence found to confirm or deny."
    - "CONTRADICTED: Resume says 'Worked at Google 2020-2023' — LinkedIn shows different dates (2021-2022)."
  - "red_flags": List of strings describing any red flags found. Examples:
    - "Employment gap of 2 years not explained in resume."
    - "Resume claims a degree from MIT but no education records found online."
    - "Negative news articles found about this person."
    - "Resume title says 'CTO' but LinkedIn says 'Junior Developer' at the same company."
    If none, return empty list.
  - "green_flags": List of strings describing positive signals. Examples:
    - "LinkedIn profile is well-established with 400+ connections."
    - "GitHub shows active open-source contributions matching claimed skills."
    - "Education credentials confirmed across multiple sources."
    - "Consistent career progression across all sources."
    If none, return empty list.
  - "recommendations": List of strings suggesting next steps for verification. Examples:
    - "Verify employment dates with CVS Health HR department."
    - "Request university transcripts to confirm degree."
    - "Check professional references for the Walmart role."

Be factual and objective. Do not invent information. If data is sparse, note it as a limitation. \
Base the verdict ONLY on what the data shows — do not assume the worst or best."""


async def generate_report(
    request: BackgroundCheckRequest,
    aggregated: AggregatedData,
) -> dict:
    user_message = f"Generate a background report and verdict for: {request.name}\n"
    if request.company:
        user_message += f"Company context: {request.company}\n"
    if request.title:
        user_message += f"Title context: {request.title}\n"
    if request.location:
        user_message += f"Location context: {request.location}\n"
    user_message += f"\n--- Collected Data ---\n{aggregated.raw_context}\n--- End Data ---"

    payload = {
        "model": settings.nvidia_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }

    client = get_client()
    resp = await client.post(
        f"{settings.nvidia_base_url}/chat/completions",
        json=payload,
        headers={
            "Authorization": f"Bearer {settings.nvidia_api_key}",
            "Content-Type": "application/json",
        },
        timeout=60,
    )

    if resp.status_code != 200:
        logger.error("NVIDIA API error %d: %s", resp.status_code, resp.text[:500])
        return _fallback_report(request, aggregated)

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    try:
        parsed = json.loads(content)
        return {
            "summary": parsed.get("summary", ""),
            "professional_background": parsed.get("professional_background", ""),
            "key_highlights": parsed.get("key_highlights", []),
            "identity_verification": parsed.get("identity_verification"),
            "verdict": parsed.get("verdict"),
        }
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("Failed to parse LLM JSON: %s. Raw: %s", e, content[:300])
        return {
            "summary": content[:1000],
            "professional_background": "",
            "key_highlights": [],
            "identity_verification": None,
            "verdict": None,
        }


def _fallback_report(request: BackgroundCheckRequest, aggregated: AggregatedData) -> dict:
    return {
        "summary": f"Background data collected for {request.name} but LLM summarization failed.",
        "professional_background": aggregated.raw_context[:2000] if aggregated.raw_context else "",
        "key_highlights": [
            f"LinkedIn profile: {'found' if aggregated.linkedin else 'not found'}",
            f"GitHub profiles: {len(aggregated.github_profiles)} found",
            f"Google results: {len(aggregated.search_results)} found",
            f"News articles: {len(aggregated.news_articles)} found",
        ],
        "identity_verification": None,
        "verdict": None,
    }
