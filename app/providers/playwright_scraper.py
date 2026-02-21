import logging
from typing import Optional
from playwright.async_api import async_playwright, Browser, Page, BrowserContext

from app.providers.base import LinkedInProvider
from app.models import LinkedInProfile, BackgroundCheckRequest
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)

_browser: Browser | None = None
_context: BrowserContext | None = None


async def _get_browser() -> Browser:
    global _browser
    if _browser is None:
        pw = await async_playwright().start()
        _browser = await pw.chromium.launch(headless=settings.playwright_headless)
    return _browser


async def _get_context() -> BrowserContext:
    global _context
    if _context is None:
        browser = await _get_browser()
        _context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        if settings.linkedin_email and settings.linkedin_password:
            await _do_login(_context)
    return _context


async def _do_login(context: BrowserContext):
    page = await context.new_page()
    try:
        await page.goto("https://www.linkedin.com/login", wait_until="networkidle")
        await page.fill("#username", settings.linkedin_email)
        await page.fill("#password", settings.linkedin_password)
        await page.click("button[type='submit']")
        await page.wait_for_url("**/feed/**", timeout=15000)
        logger.info("LinkedIn login successful")
    except Exception as e:
        logger.warning("LinkedIn login failed: %s", e)
    finally:
        await page.close()


async def close_browser():
    global _browser, _context
    if _context:
        await _context.close()
        _context = None
    if _browser:
        await _browser.close()
        _browser = None


class PlaywrightProvider(LinkedInProvider):

    async def fetch_profile(self, request: BackgroundCheckRequest) -> LinkedInProfile | None:
        url = request.linkedin_url
        if not url:
            url = await self._find_profile_url(request)
        if not url:
            return None
        return await self._scrape_profile(url)

    async def _find_profile_url(self, request: BackgroundCheckRequest) -> Optional[str]:
        if not settings.serpapi_api_key:
            return None

        client = get_client()
        first_name = request.name.split()[0].lower()

        queries = [self._build_search_query(request)]
        if request.company:
            queries.append(f"{request.name} {request.company}")
        queries.append(f'"{request.name}"')

        for query in queries:
            params = {
                "engine": "google",
                "q": f"site:linkedin.com/in/ {query}",
                "num": 3,
                "api_key": settings.serpapi_api_key,
            }
            resp = await client.get("https://serpapi.com/search.json", params=params)
            if resp.status_code != 200:
                continue
            data = resp.json()
            for result in data.get("organic_results", []):
                link = result.get("link", "")
                title = result.get("title", "")
                if "linkedin.com/in/" in link and first_name in title.lower():
                    return link
        return None

    async def _scrape_profile(self, url: str) -> LinkedInProfile | None:
        # Strip tracking params that cause redirects
        clean_url = url.split("?")[0]

        context = await _get_context()
        page = await context.new_page()
        try:
            await page.goto(clean_url, wait_until="domcontentloaded", timeout=25000)
            # Wait for page to render (don't use networkidle — LinkedIn never stops polling)
            await page.wait_for_timeout(3000)

            # Check if we got redirected to login page
            current_url = page.url
            if "/login" in current_url or "/authwall" in current_url:
                logger.warning("LinkedIn redirected to login for %s — trying public view", clean_url)
                # Try the public profile URL format
                public_url = clean_url.replace("www.linkedin.com", "linkedin.com")
                if "linkedin.com/in/" in public_url:
                    await page.goto(public_url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(2000)

            # Scroll down to load lazy sections
            for _ in range(5):
                try:
                    await page.evaluate("window.scrollBy(0, 800)")
                    await page.wait_for_timeout(400)
                except Exception:
                    break
            try:
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(500)
            except Exception:
                pass

            return await self._extract_profile(page, clean_url)
        except Exception as e:
            logger.error("Playwright scraping failed for %s: %s", clean_url, e)
            return None
        finally:
            await page.close()

    async def _extract_profile(self, page: Page, url: str) -> LinkedInProfile:
        """Extract profile using multiple selector strategies + full text fallback."""

        # Strategy 1: Try known selectors (may break as LinkedIn updates)
        name = (
            await self._safe_text(page, "h1.text-heading-xlarge") or
            await self._safe_text(page, "h1.top-card-layout__title") or
            await self._safe_text(page, "h1")
        )
        headline = (
            await self._safe_text(page, "div.text-body-medium.break-words") or
            await self._safe_text(page, ".top-card-layout__headline") or
            await self._safe_text(page, "div.text-body-medium")
        )
        location = (
            await self._safe_text(page, "span.text-body-small.inline.t-black--light.break-words") or
            await self._safe_text(page, ".top-card-layout__first-subline")
        )
        about = await self._safe_text(page, "section.pv-about-section div.inline-show-more-text") or \
                await self._safe_text(page, "#about ~ div span[aria-hidden='true']")

        # Strategy 2: Extract experience section
        experience = await self._extract_section_items(page, "#experience")
        education = await self._extract_section_items(page, "#education")

        # Strategy 3: Extract skills
        skills = []
        skill_elements = await page.query_selector_all(
            "#skills ~ div .pvs-list__paged-list-item span[aria-hidden='true']"
        )
        if not skill_elements:
            skill_elements = await page.query_selector_all(
                "section:has(#skills) span.mr1 span[aria-hidden='true']"
            )
        for el in skill_elements[:20]:
            txt = (await el.inner_text()).strip()
            if txt and len(txt) < 50:
                skills.append(txt)

        # Strategy 4: Always capture full page text as fallback for LLM
        try:
            main_el = await page.query_selector("main") or await page.query_selector("body")
            full_text = await main_el.inner_text() if main_el else ""
        except Exception:
            full_text = ""

        return LinkedInProfile(
            url=url,
            name=name,
            headline=headline,
            location=location,
            summary=about,
            experience=experience,
            education=education,
            skills=skills,
            raw_text=full_text[:6000],
        )

    async def _extract_section_items(self, page: Page, section_id: str) -> list[dict]:
        """Extract items from an experience/education section by its anchor ID."""
        items = []
        # LinkedIn uses #experience, #education as anchor IDs
        # The actual list is in a sibling/parent container
        section = await page.query_selector(f"section:has({section_id})")
        if not section:
            # Try alternative: the ID might be on a div inside a section
            section = await page.query_selector(f"{section_id}")
            if section:
                # Walk up to the section parent
                section = await section.evaluate_handle("el => el.closest('section')")
                section = section.as_element()

        if not section:
            return items

        list_items = await section.query_selector_all("li.artdeco-list__item")
        if not list_items:
            list_items = await section.query_selector_all("li.pvs-list__paged-list-item")
        if not list_items:
            list_items = await section.query_selector_all("li")

        for li in list_items[:10]:
            try:
                text = await li.inner_text()
                lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
                # Filter out very short lines (icons, dots) and duplicates
                lines = [l for l in lines if len(l) > 2]
                if not lines:
                    continue

                item = {
                    "title": lines[0] if lines else "",
                    "company": lines[1] if len(lines) > 1 else "",
                    "duration": "",
                    "description": "",
                }
                # Look for duration patterns (e.g., "Jan 2020 - Present", "2 yrs 3 mos")
                for l in lines[1:]:
                    if any(kw in l.lower() for kw in ["present", "mos", "yrs", "yr", "mo", " - ", "–"]):
                        item["duration"] = l
                        break
                # Description: remaining long lines
                for l in lines[2:]:
                    if len(l) > 40 and l != item["duration"]:
                        item["description"] = l[:300]
                        break

                items.append(item)
            except Exception:
                continue

        return items

    async def _safe_text(self, page: Page, selector: str) -> Optional[str]:
        try:
            el = await page.query_selector(selector)
            if el:
                text = (await el.inner_text()).strip()
                return text if text else None
        except Exception:
            pass
        return None
