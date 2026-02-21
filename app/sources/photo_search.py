import base64
import logging
from app.models import SocialProfile
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)

SERPAPI_BASE = "https://serpapi.com/search.json"
IMGBB_UPLOAD = "https://api.imgbb.com/1/upload"

# Map domains to platform names
DOMAIN_PLATFORM_MAP = {
    "linkedin.com": "LinkedIn",
    "twitter.com": "Twitter/X",
    "x.com": "Twitter/X",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "github.com": "GitHub",
    "youtube.com": "YouTube",
    "reddit.com": "Reddit",
    "medium.com": "Medium",
    "dev.to": "Dev.to",
    "stackoverflow.com": "Stack Overflow",
    "quora.com": "Quora",
    "kaggle.com": "Kaggle",
    "behance.net": "Behance",
    "dribbble.com": "Dribbble",
    "flickr.com": "Flickr",
    "pinterest.com": "Pinterest",
    "tumblr.com": "Tumblr",
    "vimeo.com": "Vimeo",
    "tiktok.com": "TikTok",
    "researchgate.net": "ResearchGate",
    "scholar.google.com": "Google Scholar",
    "leetcode.com": "LeetCode",
    "hackerrank.com": "HackerRank",
    "gitlab.com": "GitLab",
    "huggingface.co": "HuggingFace",
    "substack.com": "Substack",
}


async def upload_to_imgbb(file_bytes: bytes) -> str | None:
    """Upload image to ImgBB and return the public URL."""
    if not settings.imgbb_api_key:
        logger.warning("No IMGBB_API_KEY configured for photo upload")
        return None

    client = get_client()
    b64 = base64.b64encode(file_bytes).decode("utf-8")

    resp = await client.post(
        IMGBB_UPLOAD,
        data={"key": settings.imgbb_api_key, "image": b64, "expiration": 600},
        timeout=30,
    )
    if resp.status_code != 200:
        logger.error("ImgBB upload failed: %d %s", resp.status_code, resp.text[:200])
        return None

    data = resp.json()
    url = data.get("data", {}).get("url")
    logger.info("Photo uploaded to ImgBB: %s", url)
    return url


async def reverse_photo_search(image_url: str) -> dict:
    """
    Run Google Lens reverse image search via SerpAPI.
    Returns {"visual_matches": [...], "profiles": [...]}
    """
    if not settings.serpapi_api_key:
        return {"visual_matches": [], "profiles": []}

    client = get_client()

    # Google Lens engine
    params = {
        "engine": "google_lens",
        "url": image_url,
        "api_key": settings.serpapi_api_key,
    }
    resp = await client.get(SERPAPI_BASE, params=params, timeout=30)
    if resp.status_code != 200:
        logger.error("Google Lens search failed: %d %s", resp.status_code, resp.text[:300])
        return {"visual_matches": [], "profiles": []}

    data = resp.json()

    visual_matches = []
    profiles = []
    seen_urls = set()

    # Parse visual matches (pages where this image or similar appears)
    for match in data.get("visual_matches", []):
        url = match.get("link", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = match.get("title", "")
        source = match.get("source", "")
        thumbnail = match.get("thumbnail", "")

        # Detect platform from URL
        platform = _detect_platform(url)

        entry = {
            "url": url,
            "title": title,
            "source": source,
            "thumbnail": thumbnail,
            "platform": platform,
        }
        visual_matches.append(entry)

        # If it's a social media profile, also add to profiles list
        if platform:
            profiles.append(SocialProfile(
                platform=f"{platform} (photo match)",
                url=url,
                username=_extract_username_from_url(url),
                snippet=f"Photo found on {platform}: {title[:150]}",
            ))

    # Also check knowledge graph if Google identified the person
    kg = data.get("knowledge_graph", [])
    if isinstance(kg, list):
        for item in kg:
            name = item.get("title", "")
            link = item.get("link", "")
            if name and link and link not in seen_urls:
                seen_urls.add(link)
                visual_matches.append({
                    "url": link,
                    "title": f"Google identified: {name}",
                    "source": "Google Knowledge Graph",
                    "thumbnail": "",
                    "platform": _detect_platform(link),
                })

    # Check for exact matches too
    for match in data.get("exact_matches", []):
        url = match.get("link", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            platform = _detect_platform(url)
            visual_matches.append({
                "url": url,
                "title": match.get("title", "Exact image match"),
                "source": match.get("source", ""),
                "thumbnail": match.get("thumbnail", ""),
                "platform": platform,
            })
            if platform:
                profiles.append(SocialProfile(
                    platform=f"{platform} (exact match)",
                    url=url,
                    username=_extract_username_from_url(url),
                    snippet=f"Exact photo match on {platform}",
                ))

    return {"visual_matches": visual_matches, "profiles": profiles}


def _detect_platform(url: str) -> str | None:
    url_lower = url.lower()
    for domain, platform in DOMAIN_PLATFORM_MAP.items():
        if domain in url_lower:
            return platform
    return None


def _extract_username_from_url(url: str) -> str | None:
    try:
        parts = url.rstrip("/").split("/")
        parts = [p for p in parts if p]
        last = parts[-1] if parts else None
        if last and "." not in last and last not in ("profile", "users", "user", "u", "in"):
            return last
        return None
    except (ValueError, IndexError):
        return None
