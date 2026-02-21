import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.routes import background_check
from app.utils.http import close_client

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    yield
    await close_client()
    try:
        from app.providers.playwright_scraper import close_browser
        await close_browser()
    except Exception:
        pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="Backgrounder Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(
        background_check.router,
        prefix="/api/v1",
        tags=["Background Check"],
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def root():
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app


app = create_app()
