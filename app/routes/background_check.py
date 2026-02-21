import json
from typing import Optional, AsyncGenerator
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from app.models import BackgroundCheckRequest, BackgroundReport, LinkedInProviderName
from app.config import settings
from app.providers.factory import get_provider
from app.pipeline.aggregator import run_pipeline_streaming
from app.sources.resume import parse_resume_file, extract_resume_data
from app.sources.photo_search import upload_to_imgbb, reverse_photo_search

router = APIRouter()

MAX_RESUME_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB


@router.post("/check")
async def create_background_check(
    name: str = Form(...),
    company: Optional[str] = Form(None),
    location: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    linkedin_url: Optional[str] = Form(None),
    photo_url: Optional[str] = Form(None),
    provider: Optional[str] = Form(None),
    resume: Optional[UploadFile] = File(None),
    photo: Optional[UploadFile] = File(None),
):
    provider_enum = None
    if provider and provider in LinkedInProviderName.__members__:
        provider_enum = LinkedInProviderName(provider)

    req = BackgroundCheckRequest(
        name=name,
        company=company or None,
        location=location or None,
        title=title or None,
        linkedin_url=linkedin_url or None,
        provider=provider_enum,
    )

    # Read resume bytes
    resume_data = None
    resume_file_bytes = None
    resume_filename = None
    if resume and resume.filename:
        resume_file_bytes = await resume.read()
        resume_filename = resume.filename
        if len(resume_file_bytes) > MAX_RESUME_SIZE:
            raise HTTPException(status_code=413, detail="Resume file too large (max 10MB)")

    # Read photo bytes
    photo_bytes = None
    if photo and photo.filename:
        photo_bytes = await photo.read()
        if len(photo_bytes) > MAX_PHOTO_SIZE:
            raise HTTPException(status_code=413, detail="Photo too large (max 5MB)")

    # Resolve photo URL (upload if file provided, or use pasted URL)
    resolved_photo_url = (photo_url or "").strip() or None

    provider_name = req.provider.value if req.provider else settings.linkedin_provider
    linkedin_provider = get_provider(provider_name)

    async def event_stream() -> AsyncGenerator[str, None]:
        nonlocal req, resume_data, resolved_photo_url

        # Resume parsing phase
        if resume_file_bytes and resume_filename:
            yield _sse("status", {"step": "resume_parse", "label": "Parsing resume...", "state": "running"})
            raw_text = await parse_resume_file(resume_file_bytes, resume_filename)
            if raw_text.strip():
                resume_data = await extract_resume_data(raw_text)
                if resume_data.company and not req.company:
                    req = req.model_copy(update={"company": resume_data.company})
                if resume_data.title and not req.title:
                    req = req.model_copy(update={"title": resume_data.title})
                if resume_data.location and not req.location:
                    req = req.model_copy(update={"location": resume_data.location})
                if resume_data.linkedin_url and not req.linkedin_url:
                    req = req.model_copy(update={"linkedin_url": resume_data.linkedin_url})
                yield _sse("status", {"step": "resume_parse", "label": "Resume parsed", "state": "done",
                                      "detail": f"{len(resume_data.skills)} skills, {len(resume_data.experience)} roles extracted"})
            else:
                yield _sse("status", {"step": "resume_parse", "label": "Could not parse resume", "state": "error"})

        # Photo upload phase
        if photo_bytes and not resolved_photo_url:
            yield _sse("status", {"step": "photo_upload", "label": "Uploading photo...", "state": "running"})
            resolved_photo_url = await upload_to_imgbb(photo_bytes)
            if resolved_photo_url:
                yield _sse("status", {"step": "photo_upload", "label": "Photo uploaded", "state": "done", "detail": "Ready for reverse search"})
            else:
                yield _sse("status", {"step": "photo_upload", "label": "Photo upload failed (check IMGBB_API_KEY)", "state": "error"})

        # Pipeline with streaming
        async for event in run_pipeline_streaming(
            request=req,
            linkedin_provider=linkedin_provider,
            resume_data=resume_data,
            photo_url=resolved_photo_url,
        ):
            yield _sse(event["type"], event["data"])

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/health")
async def health():
    return {"status": "ok"}
