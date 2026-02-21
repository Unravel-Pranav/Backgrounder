import json
import logging
import tempfile
from pathlib import Path

import pdfplumber
from docx import Document

from app.models import ResumeData
from app.config import settings
from app.utils.http import get_client

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """\
You are a resume parsing expert. Extract structured information from the following resume text.

You MUST respond with valid JSON containing these keys:
- "name": Full name of the person (string, or null if not found)
- "email": Email address (string, or null)
- "phone": Phone number (string, or null)
- "location": City/state/country (string, or null)
- "title": Current or most recent job title (string, or null)
- "company": Current or most recent company (string, or null)
- "linkedin_url": LinkedIn profile URL if mentioned (string, or null)
- "github_url": GitHub profile URL if mentioned (string, or null)
- "website": Personal website if mentioned (string, or null)
- "skills": List of technical and professional skills (list of strings)
- "experience": List of objects with "title", "company", "duration", "description" keys
- "education": List of objects with "school", "degree", "field" keys
- "certifications": List of strings
- "key_search_terms": List of 5-10 unique search terms that would help verify this person's background \
(e.g. specific project names, publication titles, unique company+role combos, conference talks, awards). \
These should be specific enough to distinguish this person from others with the same name.

Extract ONLY what is explicitly stated. Do not invent information."""


async def parse_resume_file(file_bytes: bytes, filename: str) -> str:
    """Extract raw text from a PDF or DOCX file."""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        return _parse_pdf(file_bytes)
    elif suffix in (".docx", ".doc"):
        return _parse_docx(file_bytes)
    else:
        # Try as plain text
        return file_bytes.decode("utf-8", errors="ignore")


def _parse_pdf(file_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        text_parts = []
        with pdfplumber.open(tmp.name) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)


def _parse_docx(file_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=True) as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        doc = Document(tmp.name)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


async def extract_resume_data(raw_text: str) -> ResumeData:
    """Use NVIDIA LLM to extract structured data from resume text."""
    client = get_client()

    payload = {
        "model": settings.nvidia_model,
        "messages": [
            {"role": "system", "content": EXTRACT_PROMPT},
            {"role": "user", "content": f"Resume text:\n\n{raw_text[:8000]}"},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }

    resp = await client.post(
        f"{settings.nvidia_base_url}/chat/completions",
        json=payload,
        headers={
            "Authorization": f"Bearer {settings.nvidia_api_key}",
            "Content-Type": "application/json",
        },
        timeout=45,
    )

    if resp.status_code != 200:
        logger.error("Resume extraction LLM error %d: %s", resp.status_code, resp.text[:300])
        return ResumeData(raw_text=raw_text[:5000])

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    try:
        parsed = json.loads(content)
        return ResumeData(
            name=parsed.get("name"),
            email=parsed.get("email"),
            phone=parsed.get("phone"),
            location=parsed.get("location"),
            title=parsed.get("title"),
            company=parsed.get("company"),
            linkedin_url=parsed.get("linkedin_url"),
            github_url=parsed.get("github_url"),
            website=parsed.get("website"),
            skills=parsed.get("skills", []),
            experience=parsed.get("experience", []),
            education=parsed.get("education", []),
            certifications=parsed.get("certifications", []),
            key_search_terms=parsed.get("key_search_terms", []),
            raw_text=raw_text[:5000],
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to parse resume LLM output: %s", e)
        return ResumeData(raw_text=raw_text[:5000])
