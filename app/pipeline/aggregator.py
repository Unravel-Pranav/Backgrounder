import asyncio
import logging
from datetime import datetime, timezone

from app.models import (
    BackgroundCheckRequest, BackgroundReport, AggregatedData,
    LinkedInProfile, GitHubProfile, ResumeData, IdentityVerification,
    BackgroundVerdict, SearchResult, CompanyCheck, SocialProfile, PhotoMatch,
    ReferenceContact,
)
from app.providers.base import LinkedInProvider
from app.sources.google_search import search_google_query, search_news_query
from app.sources.github import search_github_query, fetch_github_user, extract_github_username
from app.sources.company_verify import verify_companies
from app.sources.social_media import scan_social_media
from app.sources.photo_search import reverse_photo_search
from app.sources.reference_discovery import discover_references
from app.llm.nvidia import generate_report

logger = logging.getLogger(__name__)


def _build_search_queries(request: BackgroundCheckRequest, resume: ResumeData | None) -> dict:
    """
    Build ALL search queries from the request + resume data.
    Returns a dict of named tasks to run concurrently.
    """
    name = request.name
    tasks = {}

    # --- Google searches ---
    # Base: name + current company
    base_query = name
    if request.company:
        base_query += f" {request.company}"
    if request.title:
        base_query += f" {request.title}"
    tasks["google:main"] = ("google", base_query)

    # News: name + current company
    news_query = name
    if request.company:
        news_query += f" {request.company}"
    tasks["news:main"] = ("news", news_query)

    # --- GitHub searches ---
    gh_query = name
    if request.location:
        gh_query += f" location:{request.location}"
    tasks["github:name"] = ("github_search", gh_query)

    if resume:
        # Google: name + each PAST company (not current, we already have that)
        past_companies = set()
        for exp in resume.experience:
            co = exp.get("company", "").strip()
            if co and co.lower() != (request.company or "").lower():
                past_companies.add(co)
        for co in list(past_companies)[:3]:
            tasks[f"google:company:{co}"] = ("google", f'"{name}" "{co}"')

        # Google: name + each school
        for edu in resume.education:
            school = edu.get("school", "").strip()
            if school:
                tasks[f"google:edu:{school}"] = ("google", f'"{name}" "{school}"')
                break  # just the first school to limit queries

        # Google: key search terms from LLM extraction
        for i, term in enumerate(resume.key_search_terms[:3]):
            tasks[f"google:term:{i}"] = ("google", f'"{name}" {term}')

        # GitHub: direct fetch if resume has GitHub URL
        if resume.github_url:
            username = extract_github_username(resume.github_url)
            if username:
                tasks["github:direct"] = ("github_direct", username)

        # GitHub: search by name + company from resume
        if resume.company:
            tasks["github:company"] = ("github_search", f"{name} {resume.company}")

        # News: name + past companies
        for co in list(past_companies)[:2]:
            tasks[f"news:company:{co}"] = ("news", f"{name} {co}")

    return tasks


async def _run_task(task_type: str, query: str) -> tuple[str, list]:
    """Run a single search task and return (type, results)."""
    if task_type == "google":
        results = await search_google_query(query, label="google")
        return ("google", results)
    elif task_type == "news":
        results = await search_news_query(query)
        return ("news", results)
    elif task_type == "github_search":
        results = await search_github_query(query)
        return ("github", results)
    elif task_type == "github_direct":
        profile = await fetch_github_user(query)
        return ("github", [profile] if profile else [])
    return ("unknown", [])


def _pick_best_linkedin(profiles: list[LinkedInProfile | None]) -> LinkedInProfile | None:
    """Pick the LinkedIn profile with the most data from multiple provider results."""
    best = None
    best_score = -1
    for p in profiles:
        if p is None:
            continue
        score = 0
        if p.name:
            score += 1
        if p.headline:
            score += 1
        if p.summary:
            score += 2
        if p.location:
            score += 1
        score += len(p.experience) * 3
        score += len(p.education) * 2
        score += len(p.skills)
        if score > best_score:
            best_score = score
            best = p
    return best


async def run_pipeline(
    request: BackgroundCheckRequest,
    linkedin_provider: LinkedInProvider,
    resume_data: ResumeData | None = None,
) -> BackgroundReport:

    # Build all concurrent tasks
    search_tasks = _build_search_queries(request, resume_data)

    # --- LinkedIn: run ALL providers concurrently ---
    # Always run the user's chosen provider
    linkedin_coros = [
        _safe_fetch(linkedin_provider.fetch_profile(request), "linkedin:chosen"),
    ]
    linkedin_labels = [type(linkedin_provider).__name__]

    # Always also run Playwright if we have a direct URL (from resume or form)
    linkedin_url = request.linkedin_url or (resume_data.linkedin_url if resume_data else None)
    from app.providers.playwright_scraper import PlaywrightProvider
    from app.providers.serpapi import SerpAPIProvider

    if linkedin_url:
        # Make a request copy with the URL set
        li_req = request.model_copy(update={"linkedin_url": linkedin_url})

        # Run Playwright on the direct URL (if not already the chosen provider)
        if not isinstance(linkedin_provider, PlaywrightProvider):
            linkedin_coros.append(
                _safe_fetch(PlaywrightProvider().fetch_profile(li_req), "linkedin:playwright"),
            )
            linkedin_labels.append("PlaywrightProvider")

        # Run SerpAPI on the direct URL too (if not already the chosen provider)
        if not isinstance(linkedin_provider, SerpAPIProvider):
            linkedin_coros.append(
                _safe_fetch(SerpAPIProvider().fetch_profile(li_req), "linkedin:serpapi"),
            )
            linkedin_labels.append("SerpAPIProvider")
    else:
        # No URL — still try Playwright and SerpAPI by name search
        if not isinstance(linkedin_provider, PlaywrightProvider):
            linkedin_coros.append(
                _safe_fetch(PlaywrightProvider().fetch_profile(request), "linkedin:playwright"),
            )
            linkedin_labels.append("PlaywrightProvider")
        if not isinstance(linkedin_provider, SerpAPIProvider):
            linkedin_coros.append(
                _safe_fetch(SerpAPIProvider().fetch_profile(request), "linkedin:serpapi"),
            )
            linkedin_labels.append("SerpAPIProvider")

    # --- Prepare all other search coroutines ---
    other_coros = []
    other_names = []
    for name, (task_type, query) in search_tasks.items():
        other_coros.append(_safe_fetch(_run_task(task_type, query), name))
        other_names.append(name)

    # --- Company verification + Social media scan (concurrent) ---
    extra_coros = []
    extra_labels = []

    # Company verification (if resume provided)
    if resume_data:
        extra_coros.append(_safe_fetch(verify_companies(resume_data), "company_verify"))
        extra_labels.append("company_verify")

    # Social media scan (always)
    extra_coros.append(_safe_fetch(scan_social_media(request), "social_media"))
    extra_labels.append("social_media")

    total = len(linkedin_coros) + len(other_coros) + len(extra_coros)
    logger.info(
        "Running %d concurrent tasks: %d LinkedIn + %d searches + %d extras",
        total, len(linkedin_coros), len(other_coros), len(extra_coros),
    )

    # Run EVERYTHING concurrently
    all_results = await asyncio.gather(*(linkedin_coros + other_coros + extra_coros))

    # Split results
    li_count = len(linkedin_coros)
    other_count = len(other_coros)
    li_results = all_results[:li_count]
    other_results = all_results[li_count:li_count + other_count]
    extra_results = all_results[li_count + other_count:]

    # Parse extra results
    company_checks: list[CompanyCheck] = []
    social_profiles: list[SocialProfile] = []
    for i, result in enumerate(extra_results):
        label = extra_labels[i]
        if result is None:
            continue
        if label == "company_verify":
            company_checks = result
        elif label == "social_media":
            social_profiles = result

    # Pick best LinkedIn profile
    linkedin_profile = _pick_best_linkedin(li_results)
    li_providers_used = []
    for i, r in enumerate(li_results):
        if r is not None:
            li_providers_used.append(linkedin_labels[i])

    # Collect search results by type
    all_google: list[SearchResult] = []
    all_news: list[SearchResult] = []
    all_github: list[GitHubProfile] = []
    seen_urls = set()
    seen_github_usernames = set()

    for i, result in enumerate(other_results):
        if result is None:
            continue
        task_name = other_names[i]
        result_type, items = result if isinstance(result, tuple) else ("unknown", [])

        if result_type == "google":
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    item.source = f"google ({task_name.split(':', 1)[-1]})"
                    all_google.append(item)
        elif result_type == "news":
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_news.append(item)
        elif result_type == "github":
            for profile in items:
                if profile.username not in seen_github_usernames:
                    seen_github_usernames.add(profile.username)
                    all_github.append(profile)

    # Build raw context for LLM
    sources_used = []
    raw_parts = []

    if resume_data:
        sources_used.append("Resume (uploaded)")
        raw_parts.append(_resume_to_text(resume_data))

    if linkedin_profile:
        providers_str = " + ".join(li_providers_used)
        sources_used.append(f"LinkedIn ({providers_str})")
        raw_parts.append(_linkedin_to_text(linkedin_profile))

    if all_github:
        sources_used.append(f"GitHub ({len(all_github)} profiles found)")
        for i, gh in enumerate(all_github):
            raw_parts.append(_github_to_text(gh, i + 1))

    if all_google:
        sources_used.append(f"Google Search ({len(all_google)} results)")
        for r in all_google:
            raw_parts.append(f"[{r.source}] {r.title}: {r.snippet}")

    if all_news:
        sources_used.append(f"Google News ({len(all_news)} articles)")
        for r in all_news:
            raw_parts.append(f"[news] {r.title}: {r.snippet}")

    if company_checks:
        sources_used.append(f"Company Verification ({len(company_checks)} checked)")
        for cc in company_checks:
            status = "VERIFIED" if cc.verified else "NOT VERIFIED"
            raw_parts.append(f"[company check] {cc.name}: {status} — {cc.description}")

    if social_profiles:
        sources_used.append(f"Social Media ({len(social_profiles)} profiles found)")
        for sp in social_profiles:
            raw_parts.append(f"[social: {sp.platform}] {sp.url} — {sp.snippet}")

    aggregated = AggregatedData(
        linkedin=linkedin_profile,
        github_profiles=all_github,
        resume=resume_data,
        company_checks=company_checks,
        social_profiles=social_profiles,
        search_results=all_google,
        news_articles=all_news,
        raw_context="\n\n".join(raw_parts),
    )

    confidence = ""
    if not linkedin_profile:
        confidence = "No LinkedIn profile found. Report is based on web search results only."
    elif linkedin_profile.raw_text and not linkedin_profile.experience:
        confidence = "LinkedIn data was partially extracted. Some details may be missing."

    llm_result = await generate_report(request, aggregated)

    identity_raw = llm_result.get("identity_verification")
    identity = None
    if identity_raw and isinstance(identity_raw, dict):
        identity = IdentityVerification(
            confidence=identity_raw.get("confidence", ""),
            reasoning=identity_raw.get("reasoning", ""),
            multiple_people_detected=identity_raw.get("multiple_people_detected", False),
            profiles_found=identity_raw.get("profiles_found", []),
            cross_reference_notes=identity_raw.get("cross_reference_notes", []),
        )

    # Build verdict from LLM output
    verdict_raw = llm_result.get("verdict")
    verdict = None
    if verdict_raw and isinstance(verdict_raw, dict):
        verdict = BackgroundVerdict(
            rating=verdict_raw.get("rating", ""),
            score=verdict_raw.get("score", 0),
            summary=verdict_raw.get("summary", ""),
            resume_vs_online=verdict_raw.get("resume_vs_online", []),
            red_flags=verdict_raw.get("red_flags", []),
            green_flags=verdict_raw.get("green_flags", []),
            recommendations=verdict_raw.get("recommendations", []),
        )

    providers_str = " + ".join(li_providers_used) if li_providers_used else type(linkedin_provider).__name__

    return BackgroundReport(
        name=request.name,
        generated_at=datetime.now(timezone.utc),
        linkedin_profile=linkedin_profile,
        github_profiles=all_github,
        resume_data=resume_data,
        company_checks=company_checks,
        social_profiles=social_profiles,
        identity_verification=identity,
        verdict=verdict,
        summary=llm_result.get("summary", ""),
        professional_background=llm_result.get("professional_background", ""),
        key_highlights=llm_result.get("key_highlights", []),
        news_mentions=all_news,
        sources_used=sources_used,
        provider_used=providers_str,
        confidence_note=confidence,
    )


# === Text serializers for LLM context ===

def _resume_to_text(resume: ResumeData) -> str:
    parts = ["[SOURCE: Uploaded Resume]"]
    if resume.name:
        parts.append(f"Name: {resume.name}")
    if resume.title:
        parts.append(f"Current Title: {resume.title}")
    if resume.company:
        parts.append(f"Current Company: {resume.company}")
    if resume.location:
        parts.append(f"Location: {resume.location}")
    if resume.email:
        parts.append(f"Email: {resume.email}")
    if resume.linkedin_url:
        parts.append(f"LinkedIn: {resume.linkedin_url}")
    if resume.github_url:
        parts.append(f"GitHub: {resume.github_url}")
    if resume.website:
        parts.append(f"Website: {resume.website}")
    if resume.skills:
        parts.append(f"Skills: {', '.join(resume.skills[:20])}")
    for exp in resume.experience:
        parts.append(
            f"Experience: {exp.get('title', '')} at {exp.get('company', '')} ({exp.get('duration', '')})"
        )
        if exp.get("description"):
            parts.append(f"  Details: {exp['description'][:200]}")
    for edu in resume.education:
        parts.append(f"Education: {edu.get('degree', '')} in {edu.get('field', '')} from {edu.get('school', '')}")
    if resume.certifications:
        parts.append(f"Certifications: {', '.join(resume.certifications)}")
    if resume.key_search_terms:
        parts.append(f"Key identifiers from resume: {', '.join(resume.key_search_terms)}")
    return "\n".join(parts)


def _linkedin_to_text(profile: LinkedInProfile) -> str:
    parts = ["[SOURCE: LinkedIn]", f"Name: {profile.name}"]
    if profile.headline:
        parts.append(f"Headline: {profile.headline}")
    if profile.location:
        parts.append(f"Location: {profile.location}")
    if profile.summary:
        parts.append(f"About: {profile.summary}")
    for exp in profile.experience:
        parts.append(
            f"Experience: {exp.get('title', '')} at {exp.get('company', '')} ({exp.get('duration', '')})"
        )
    for edu in profile.education:
        parts.append(f"Education: {edu.get('degree', '')} from {edu.get('school', '')}")
    if profile.skills:
        parts.append(f"Skills: {', '.join(profile.skills[:15])}")
    if profile.raw_text and not profile.experience:
        parts.append(f"Raw profile text:\n{profile.raw_text[:3000]}")
    return "\n".join(parts)


def _github_to_text(profile: GitHubProfile, index: int) -> str:
    parts = [f"[SOURCE: GitHub Profile #{index}]"]
    parts.append(f"Username: {profile.username}")
    if profile.name:
        parts.append(f"Display Name: {profile.name}")
    if profile.bio:
        parts.append(f"Bio: {profile.bio}")
    if profile.company:
        parts.append(f"Company: {profile.company}")
    if profile.location:
        parts.append(f"Location: {profile.location}")
    if profile.blog:
        parts.append(f"Website: {profile.blog}")
    parts.append(f"Public Repos: {profile.public_repos}, Followers: {profile.followers}")
    if profile.top_repos:
        repo_strs = []
        for r in profile.top_repos:
            repo_strs.append(
                f"  - {r.get('name', '')} ({r.get('language', 'N/A')}, "
                f"{r.get('stars', 0)} stars): {r.get('description', '')}"
            )
        parts.append("Top Repositories:\n" + "\n".join(repo_strs))
    return "\n".join(parts)


async def _labeled_task(coro, label: str):
    """Wrap a coroutine so it returns (label, result) and catches errors."""
    try:
        result = await coro
        return (label, result)
    except Exception as e:
        logger.error("Source '%s' failed: %s", label, e)
        return (label, None)


# Human-friendly labels for the activity feed
_FRIENDLY_LABELS = {
    "linkedin:chosen": "LinkedIn (primary provider)",
    "linkedin:playwright": "LinkedIn (Playwright scraper)",
    "linkedin:serpapi": "LinkedIn (SerpAPI)",
    "google:main": "Google Search",
    "news:main": "News Search",
    "github:name": "GitHub (name search)",
    "github:direct": "GitHub (direct profile)",
    "github:company": "GitHub (company search)",
    "company_verify": "Company Verification",
    "social_media": "Social Media Scan",
    "photo_search": "Reverse Photo Search",
    "references": "Reference Discovery",
}


def _friendly(label: str) -> str:
    if label in _FRIENDLY_LABELS:
        return _FRIENDLY_LABELS[label]
    if label.startswith("google:company:"):
        return f"Google: {label.split(':',2)[2]}"
    if label.startswith("google:edu:"):
        return f"Google: {label.split(':',2)[2]}"
    if label.startswith("google:term:"):
        return f"Google: key term #{int(label.split(':')[2])+1}"
    if label.startswith("news:company:"):
        return f"News: {label.split(':',2)[2]}"
    return label


async def run_pipeline_streaming(
    request: BackgroundCheckRequest,
    linkedin_provider: LinkedInProvider,
    resume_data: ResumeData | None = None,
    photo_url: str | None = None,
):
    """
    Streaming version of run_pipeline.
    Yields dicts: {"type": "status"|"result", "data": {...}}
    """
    search_tasks = _build_search_queries(request, resume_data)

    # --- Build all task coroutines with labels ---
    all_tasks = []

    # LinkedIn providers
    linkedin_labels = []
    all_tasks.append(_labeled_task(linkedin_provider.fetch_profile(request), "linkedin:chosen"))
    linkedin_labels.append(("linkedin:chosen", type(linkedin_provider).__name__))

    linkedin_url = request.linkedin_url or (resume_data.linkedin_url if resume_data else None)
    from app.providers.playwright_scraper import PlaywrightProvider
    from app.providers.serpapi import SerpAPIProvider

    li_req = request.model_copy(update={"linkedin_url": linkedin_url}) if linkedin_url else request

    if not isinstance(linkedin_provider, PlaywrightProvider):
        all_tasks.append(_labeled_task(PlaywrightProvider().fetch_profile(li_req), "linkedin:playwright"))
        linkedin_labels.append(("linkedin:playwright", "PlaywrightProvider"))
    if not isinstance(linkedin_provider, SerpAPIProvider):
        all_tasks.append(_labeled_task(SerpAPIProvider().fetch_profile(li_req), "linkedin:serpapi"))
        linkedin_labels.append(("linkedin:serpapi", "SerpAPIProvider"))

    # Search tasks
    search_labels = []
    for name, (task_type, query) in search_tasks.items():
        all_tasks.append(_labeled_task(_run_task(task_type, query), name))
        search_labels.append(name)

    # Extra tasks
    if resume_data:
        all_tasks.append(_labeled_task(verify_companies(resume_data), "company_verify"))
    all_tasks.append(_labeled_task(scan_social_media(request), "social_media"))
    all_tasks.append(_labeled_task(discover_references(request, resume_data), "references"))
    if photo_url:
        all_tasks.append(_labeled_task(reverse_photo_search(photo_url), "photo_search"))

    total = len(all_tasks)

    # Emit initial status for all tasks
    all_labels = []
    for t_label, _ in linkedin_labels:
        all_labels.append(t_label)
    all_labels.extend(search_labels)
    if resume_data:
        all_labels.append("company_verify")
    all_labels.append("social_media")
    all_labels.append("references")
    if photo_url:
        all_labels.append("photo_search")

    yield {"type": "status", "data": {
        "step": "search_start", "label": f"Launching {total} concurrent searches...",
        "state": "running", "total": total, "completed": 0,
        "tasks": [{"id": lb, "label": _friendly(lb), "state": "running"} for lb in all_labels],
    }}

    # Run with as_completed for streaming
    completed = 0
    results_map = {}

    for future in asyncio.as_completed(all_tasks):
        label, result = await future
        completed += 1
        results_map[label] = result

        state = "done" if result is not None else "error"
        detail = ""
        if result is not None:
            if label.startswith("linkedin:") and result:
                detail = result.name or "Profile found"
            elif isinstance(result, tuple):
                rtype, items = result
                detail = f"{len(items)} results" if items else "No results"
            elif isinstance(result, list):
                detail = f"{len(result)} found"

        yield {"type": "status", "data": {
            "step": "task_done", "task_id": label, "label": _friendly(label),
            "state": state, "detail": detail,
            "completed": completed, "total": total,
        }}

    # --- All tasks done, assemble the report ---
    yield {"type": "status", "data": {
        "step": "analyzing", "label": "AI analyzing all data...", "state": "running",
        "completed": total, "total": total,
    }}

    # Collect LinkedIn results
    li_results = []
    li_providers_used = []
    for t_label, provider_name in linkedin_labels:
        r = results_map.get(t_label)
        li_results.append(r)
        if r is not None:
            li_providers_used.append(provider_name)
    linkedin_profile = _pick_best_linkedin(li_results)

    # Collect search results
    all_google, all_news, all_github = [], [], []
    seen_urls, seen_gh = set(), set()
    for label in search_labels:
        result = results_map.get(label)
        if result is None:
            continue
        result_type, items = result if isinstance(result, tuple) else ("unknown", [])
        if result_type == "google":
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    item.source = f"google ({label.split(':', 1)[-1]})"
                    all_google.append(item)
        elif result_type == "news":
            for item in items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_news.append(item)
        elif result_type == "github":
            for profile in items:
                if profile.username not in seen_gh:
                    seen_gh.add(profile.username)
                    all_github.append(profile)

    company_checks = results_map.get("company_verify") or []
    social_profiles = results_map.get("social_media") or []
    reference_contacts: list[ReferenceContact] = results_map.get("references") or []

    # Photo search results
    photo_matches = []
    photo_result = results_map.get("photo_search")
    if photo_result and isinstance(photo_result, dict):
        for vm in photo_result.get("visual_matches", []):
            photo_matches.append(PhotoMatch(
                url=vm.get("url", ""),
                title=vm.get("title", ""),
                source=vm.get("source", ""),
                thumbnail=vm.get("thumbnail", ""),
                platform=vm.get("platform"),
            ))
        # Add photo-discovered social profiles
        for sp in photo_result.get("profiles", []):
            if sp.url not in {p.url for p in social_profiles}:
                social_profiles.append(sp)

    # Build context
    sources_used, raw_parts = [], []
    if resume_data:
        sources_used.append("Resume (uploaded)")
        raw_parts.append(_resume_to_text(resume_data))
    if linkedin_profile:
        sources_used.append(f"LinkedIn ({' + '.join(li_providers_used)})")
        raw_parts.append(_linkedin_to_text(linkedin_profile))
    if all_github:
        sources_used.append(f"GitHub ({len(all_github)} profiles)")
        for i, gh in enumerate(all_github):
            raw_parts.append(_github_to_text(gh, i + 1))
    if all_google:
        sources_used.append(f"Google ({len(all_google)} results)")
        for r in all_google:
            raw_parts.append(f"[{r.source}] {r.title}: {r.snippet}")
    if all_news:
        sources_used.append(f"News ({len(all_news)} articles)")
        for r in all_news:
            raw_parts.append(f"[news] {r.title}: {r.snippet}")
    if company_checks:
        sources_used.append(f"Company Verify ({len(company_checks)})")
        for cc in company_checks:
            raw_parts.append(f"[company] {cc.name}: {'VERIFIED' if cc.verified else 'NOT VERIFIED'} — {cc.description}")
    if social_profiles:
        sources_used.append(f"Social Media ({len(social_profiles)})")
        for sp in social_profiles:
            raw_parts.append(f"[social: {sp.platform}] {sp.url} — {sp.snippet}")
    if photo_matches:
        sources_used.append(f"Reverse Photo ({len(photo_matches)} matches)")
        for pm in photo_matches:
            platform_tag = f" [{pm.platform}]" if pm.platform else ""
            raw_parts.append(f"[photo match{platform_tag}] {pm.url} — {pm.title}")
    if reference_contacts:
        sources_used.append(f"References ({len(reference_contacts)} contacts found)")
        for rc in reference_contacts:
            raw_parts.append(
                f"[reference: {rc.category}] {rc.name} — {rc.title} at {rc.company} ({rc.linkedin_url})"
            )

    aggregated = AggregatedData(
        linkedin=linkedin_profile, github_profiles=all_github, resume=resume_data,
        company_checks=company_checks, social_profiles=social_profiles,
        photo_matches=photo_matches, reference_contacts=reference_contacts,
        search_results=all_google, news_articles=all_news,
        raw_context="\n\n".join(raw_parts),
    )

    llm_result = await generate_report(request, aggregated)

    identity_raw = llm_result.get("identity_verification")
    identity = None
    if identity_raw and isinstance(identity_raw, dict):
        identity = IdentityVerification(**{k: identity_raw.get(k, d) for k, d in [
            ("confidence", ""), ("reasoning", ""), ("multiple_people_detected", False),
            ("profiles_found", []), ("cross_reference_notes", []),
        ]})

    verdict_raw = llm_result.get("verdict")
    verdict = None
    if verdict_raw and isinstance(verdict_raw, dict):
        verdict = BackgroundVerdict(**{k: verdict_raw.get(k, d) for k, d in [
            ("rating", ""), ("score", 0), ("summary", ""),
            ("resume_vs_online", []), ("red_flags", []), ("green_flags", []), ("recommendations", []),
        ]})

    providers_str = " + ".join(li_providers_used) if li_providers_used else type(linkedin_provider).__name__
    confidence = ""
    if not linkedin_profile:
        confidence = "No LinkedIn profile found."
    elif linkedin_profile.raw_text and not linkedin_profile.experience:
        confidence = "LinkedIn data was partially extracted."

    report = BackgroundReport(
        name=request.name, generated_at=datetime.now(timezone.utc),
        linkedin_profile=linkedin_profile, github_profiles=all_github,
        resume_data=resume_data, company_checks=company_checks,
        social_profiles=social_profiles, photo_matches=photo_matches,
        reference_contacts=reference_contacts, identity_verification=identity,
        verdict=verdict, summary=llm_result.get("summary", ""),
        professional_background=llm_result.get("professional_background", ""),
        key_highlights=llm_result.get("key_highlights", []),
        news_mentions=all_news, sources_used=sources_used,
        provider_used=providers_str, confidence_note=confidence,
    )

    yield {"type": "result", "data": report.model_dump()}


async def _safe_fetch(coro, label: str):
    try:
        return await coro
    except Exception as e:
        logger.error("Source '%s' failed: %s", label, e)
        return None
