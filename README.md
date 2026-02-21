# Backgrounder

AI-powered background check agent. Give it a name, and it concurrently searches LinkedIn, GitHub, 29 social media platforms, Google, and news sources — then uses an LLM to generate a structured report with a verdict on whether the person's background checks out.

## Features

- **LinkedIn** — runs multiple providers (SerpAPI + Playwright) concurrently, picks the best result
- **GitHub** — searches for matching profiles, fetches repos/bio/stats
- **29 Social Media Platforms** — Twitter/X, Instagram, Facebook, Reddit, Medium, Stack Overflow, Dev.to, LeetCode, HackerRank, Kaggle, YouTube, and more
- **Resume Parsing** — upload PDF/DOCX, LLM extracts skills/experience/education, uses them to enrich all searches
- **Company Verification** — checks if each company from the resume actually exists
- **Reverse Photo Search** — upload a photo or paste a URL, Google Lens finds where it appears online
- **Reference Discovery** — finds HR, managers, and colleagues at each company for employment verification
- **Identity Verification** — cross-references data across all sources, detects multiple people with the same name
- **Background Verdict** — score 0–100 (Clean / Caution / Red Flags), resume vs online comparison, red/green flags
- **Live Streaming UI** — real-time activity feed shows each search completing as it happens (SSE)
- **All searches run concurrently** — 15–20+ tasks via `asyncio.gather`

## How It Works

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────┐
│                        USER INPUT                           │
│         name + optional company / resume / photo            │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
       ┌───────▼────────┐            ┌────────▼────────┐
       │  Resume Upload  │            │  Photo Upload   │
       │  (PDF / DOCX)   │            │  (JPG / PNG)    │
       └───────┬─────────┘            └────────┬────────┘
               │                               │
       Parse with pdfplumber /          Upload to ImgBB
       python-docx, then LLM           (temporary URL)
       extracts structured data                │
               │                               │
               ▼                               ▼
┌─────────────────────────────────────────────────────────────┐
│              CONCURRENT PIPELINE (asyncio.gather)           │
│                    15–20+ tasks in parallel                 │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │   LinkedIn    │  │    GitHub    │  │   Google Search   │ │
│  │ SerpAPI +     │  │ Name search  │  │ Name + company    │ │
│  │ Playwright    │  │ + direct URL │  │ + school + terms  │ │
│  └──────────────┘  └──────────────┘  └───────────────────┘ │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │  Google News  │  │ Social Media │  │    Company        │ │
│  │ Name +        │  │ 29 platforms │  │    Verification   │ │
│  │ companies     │  │ in 6 batches │  │    (each Googled) │ │
│  └──────────────┘  └──────────────┘  └───────────────────┘ │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │ Reverse Photo│  │  Reference   │                        │
│  │ Search       │  │  Discovery   │                        │
│  │ (Google Lens)│  │  (HR/mgrs)   │                        │
│  └──────────────┘  └──────────────┘                        │
│                                                             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                  Deduplicate + merge
                           │
                           ▼
              ┌────────────────────────┐
              │     LLM Analysis       │
              │ Generates structured   │
              │ report + verdict       │
              └───────────┬────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    BACKGROUND REPORT                        │
│                                                             │
│  ├── Verdict (score 0–100, Clean / Caution / Red Flags)    │
│  ├── Resume vs Online (VERIFIED / UNVERIFIED / CONTRADICTED│
│  │   per claim)                                            │
│  ├── Identity Verification (confidence + cross-references) │
│  ├── LinkedIn Profile                                      │
│  ├── GitHub Profiles                                       │
│  ├── Social Media Profiles                                 │
│  ├── Company Verification                                  │
│  ├── Reference Contacts (HR, managers, colleagues)         │
│  ├── Photo Matches                                         │
│  ├── News Mentions                                         │
│  └── Key Highlights + Professional Background              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Request Lifecycle

1. **User submits** a name via the web UI (plus optional company, title, location, LinkedIn URL, resume, and photo)
2. **`POST /api/v1/check`** receives the multipart form and opens an SSE stream
3. **Resume parsing** — if a PDF/DOCX was uploaded, `pdfplumber` / `python-docx` extracts raw text, then the LLM extracts structured data (skills, experience, education, URLs, key search terms). This enriches all downstream searches.
4. **Photo upload** — if a photo was provided, it's uploaded to ImgBB to get a temporary public URL for reverse search
5. **Concurrent pipeline** — the aggregator builds 15–20+ async tasks and runs them with `asyncio.as_completed`, streaming each result to the UI as it finishes:
   - **LinkedIn**: multiple providers race concurrently; the profile with the most data wins
   - **GitHub**: name-based search + direct profile fetch if a URL was found in the resume
   - **Google Search**: multiple queries (name + company, name + school, name + key terms from resume)
   - **Google News**: name + each company mentioned
   - **Social Media**: 29 platforms searched in 6 batched Google queries using `site:` operators
   - **Company Verification**: each company from the resume is Googled to confirm it exists
   - **Reverse Photo Search**: Google Lens via SerpAPI finds where the photo appears online
   - **Reference Discovery**: finds HR, managers, and colleagues at each company via LinkedIn search
6. **Deduplication** — results are merged, duplicate URLs and GitHub usernames are removed
7. **LLM analysis** — all gathered data is serialized into text and sent to the LLM, which returns a structured JSON report with verdict, identity verification, resume comparison, and highlights
8. **Report delivered** — the final `BackgroundReport` is streamed to the client as the last SSE event

### Concurrency Model

```
                    asyncio.as_completed()
                           │
     ┌─────────┬───────────┼───────────┬──────────┐
     │         │           │           │          │
  LinkedIn  GitHub    Google ×N    Social     Company
  (2-3      (1-2      (5-8         Media      Verify
  providers) queries)  queries)    (6 batches) (N cos)
     │         │           │           │          │
     └─────────┴───────────┴───────────┴──────────┘
                           │
                    SSE stream ──→ UI updates in real time
```

Each task is wrapped with `_labeled_task()` which catches errors per-source — if one provider fails, the rest continue. Results stream to the frontend as they arrive via Server-Sent Events.

### LinkedIn Multi-Provider Strategy

```
Request ──→ ┌─ SerpAPI Provider ──────→ ┐
            ├─ Playwright Provider ───→ ├──→ _pick_best_linkedin()
            └─ (Proxycurl / RapidAPI) → ┘         │
                                            Best profile
                                           (scored by data
                                            completeness)
```

Providers are defined behind an ABC (`LinkedInProvider`) and registered in a factory. Multiple run in parallel; the one returning the richest data (scored by fields present: experience ×3, education ×2, skills ×1, etc.) is selected.

### Social Media Batching

29 platforms are grouped into 6 batches. Each batch becomes a single Google search using `OR`'d `site:` operators:

| Batch | Platforms |
|-------|-----------|
| Major Social | Twitter/X, Facebook, Instagram, Reddit |
| Dev Platforms | Stack Overflow, Medium, Dev.to, Hashnode, HackerNoon |
| Code Platforms | GitLab, Bitbucket, npm, PyPI, HuggingFace |
| Creative | Behance, Dribbble, Figma, CodePen |
| Research & Competitions | Kaggle, Google Scholar, ResearchGate, LeetCode, HackerRank, Codeforces |
| Content | YouTube, Substack, Quora, Speakerdeck, SlideShare |

If the first pass finds fewer than 2 profiles, a retry runs relaxed (unquoted) queries on key platforms.

## Project Structure

```
backgrounder/
├── app/
│   ├── main.py                        # FastAPI app, lifespan, static files
│   ├── cli.py                         # CLI entry point (`backgrounder` command)
│   ├── config.py                      # Pydantic Settings loaded from .env
│   ├── models.py                      # All Pydantic schemas (request, report, profiles, verdict)
│   │
│   ├── routes/
│   │   └── background_check.py        # POST /api/v1/check — form handling + SSE streaming
│   │
│   ├── pipeline/
│   │   └── aggregator.py              # Orchestrator — builds tasks, runs concurrently, merges, calls LLM
│   │
│   ├── providers/                     # LinkedIn data providers (pluggable)
│   │   ├── base.py                    #   LinkedInProvider ABC
│   │   ├── factory.py                 #   Provider registry + get_provider()
│   │   ├── serpapi.py                 #   SerpAPI (cascading Google search for LinkedIn)
│   │   ├── playwright_scraper.py      #   Playwright browser automation
│   │   ├── proxycurl.py              #   Proxycurl API
│   │   └── rapidapi.py              #   RapidAPI
│   │
│   ├── sources/                       # Individual data source modules
│   │   ├── google_search.py           #   Google Search + News via SerpAPI
│   │   ├── github.py                 #   GitHub API (search + user profile + repos)
│   │   ├── social_media.py           #   29 platforms in 6 batched Google queries
│   │   ├── company_verify.py         #   Company existence check via Google
│   │   ├── photo_search.py           #   ImgBB upload + Google Lens reverse search
│   │   ├── resume.py                 #   PDF/DOCX parsing + LLM extraction
│   │   └── reference_discovery.py    #   Find HR/managers/colleagues for verification
│   │
│   ├── llm/
│   │   └── nvidia.py                  # LLM integration (report generation + verdict)
│   │
│   └── utils/
│       └── http.py                    # Shared httpx.AsyncClient singleton
│
├── static/
│   ├── index.html                     # Frontend SPA
│   ├── style.css                      # Dark theme with glassmorphism + animations
│   └── script.js                      # SSE streaming + report rendering
│
├── pyproject.toml                     # Dependencies, scripts, build config
├── uv.lock                            # Locked dependency versions
├── .env.example                       # Template for API keys
└── README.md
```

### Key Module Responsibilities

| Module | Role |
|--------|------|
| `routes/background_check.py` | Parses multipart form, handles resume/photo uploads, opens SSE stream |
| `pipeline/aggregator.py` | Builds search queries from input + resume, orchestrates all concurrent tasks, deduplicates, calls LLM |
| `providers/*` | Pluggable LinkedIn backends behind an ABC + factory |
| `sources/*` | Standalone modules for each data source (Google, GitHub, social media, etc.) |
| `llm/nvidia.py` | Sends aggregated context to the LLM, parses structured JSON response |
| `models.py` | 14 Pydantic models defining the entire data flow from request to report |
| `config.py` | Single `Settings` class loading all env vars via `pydantic-settings` |
| `utils/http.py` | Shared `httpx.AsyncClient` with connection pooling, cleaned up on shutdown |

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serves the frontend |
| `/api/v1/check` | POST | Runs a background check (multipart form → SSE stream) |
| `/api/v1/health` | GET | Health check (`{"status": "ok"}`) |

### `POST /api/v1/check`

**Content-Type:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Full name of the person |
| `company` | string | No | Current company |
| `title` | string | No | Current job title |
| `location` | string | No | Location (city, state) |
| `linkedin_url` | string | No | Direct LinkedIn profile URL |
| `photo_url` | string | No | URL to a photo for reverse search |
| `provider` | string | No | LinkedIn provider override (`serpapi`, `playwright`, `proxycurl`, `rapidapi`) |
| `resume` | file | No | PDF or DOCX resume (max 10 MB) |
| `photo` | file | No | Photo for reverse search (max 5 MB) |

**Response:** `text/event-stream` with two event types:

- `event: status` — progress updates (task started, task completed, errors)
- `event: result` — final `BackgroundReport` JSON

## Quick Start

### Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Python ≥ 3.11
- API keys (see below)

### Setup

```bash
git clone <your-repo-url>
cd backgrounder

# Install dependencies (creates .venv automatically)
uv sync

# Install Playwright browser
uv run playwright install chromium

# Configure API keys
cp .env.example .env
# Edit .env — fill in at least SERPAPI_API_KEY and your LLM API key
```

### Run

```bash
uv run uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000**

### Or use the CLI shortcut

```bash
uv run backgrounder
```

## API Keys

| Key | Required | Free Tier | Where to get it |
|-----|----------|-----------|-----------------|
| `SERPAPI_API_KEY` | Yes | 100 searches/month | [serpapi.com](https://serpapi.com) |
| `NVIDIA_API_KEY` | Yes | Yes | [build.nvidia.com](https://build.nvidia.com) |
| `IMGBB_API_KEY` | For photo search | Yes (unlimited) | [api.imgbb.com](https://api.imgbb.com) |
| `LINKEDIN_EMAIL` + `LINKEDIN_PASSWORD` | For Playwright scraping | N/A | Your LinkedIn account |
| `PROXYCURL_API_KEY` | For Proxycurl provider | 10 free credits | [proxycurl.com](https://proxycurl.com) |
| `RAPIDAPI_KEY` | For RapidAPI provider | Varies | [rapidapi.com](https://rapidapi.com) |

**Minimum to get started:** `SERPAPI_API_KEY` + an LLM API key.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SERPAPI_API_KEY` | Yes | — | SerpAPI key for all Google/LinkedIn/News searches |
| `NVIDIA_API_KEY` | Yes | — | LLM API key for report generation |
| `NVIDIA_BASE_URL` | No | `https://integrate.api.nvidia.com/v1` | LLM API base URL |
| `NVIDIA_MODEL` | No | `meta/llama-3.1-70b-instruct` | LLM model identifier |
| `LINKEDIN_PROVIDER` | No | `playwright` | Default LinkedIn provider |
| `LINKEDIN_EMAIL` | No | — | For Playwright LinkedIn login |
| `LINKEDIN_PASSWORD` | No | — | For Playwright LinkedIn login |
| `PLAYWRIGHT_HEADLESS` | No | `true` | Run Playwright in headless mode |
| `IMGBB_API_KEY` | No | — | For reverse photo search |
| `PROXYCURL_API_KEY` | No | — | For Proxycurl provider |
| `RAPIDAPI_KEY` | No | — | For RapidAPI provider |
| `RAPIDAPI_HOST` | No | `linkedin-data-api.p.rapidapi.com` | RapidAPI host |
| `MAX_CONCURRENCY` | No | `5` | Max concurrent tasks |
| `REQUEST_TIMEOUT` | No | `30` | HTTP request timeout (seconds) |

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI + Uvicorn |
| HTTP Client | httpx (async, connection pooled) |
| Validation | Pydantic v2 + pydantic-settings |
| Browser Automation | Playwright |
| Document Parsing | pdfplumber (PDF), python-docx (DOCX) |
| Image Processing | Pillow |
| Frontend | Vanilla HTML/CSS/JS |
| Package Manager | uv |

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run linter
uv run ruff check app/

# Run type checker
uv run mypy app/

# Run tests
uv run pytest
```

## License

Apache-2.0
