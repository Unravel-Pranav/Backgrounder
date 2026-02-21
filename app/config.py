from typing import Literal
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    linkedin_provider: Literal["serpapi", "playwright", "proxycurl", "rapidapi"] = "playwright"

    # SerpAPI
    serpapi_api_key: str = ""

    # Playwright
    linkedin_email: str = ""
    linkedin_password: str = ""
    playwright_headless: bool = True

    # Proxycurl
    proxycurl_api_key: str = ""

    # RapidAPI
    rapidapi_key: str = ""
    rapidapi_host: str = "linkedin-data-api.p.rapidapi.com"

    # NVIDIA
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "meta/llama-3.1-70b-instruct"

    # ImgBB (for reverse image search)
    imgbb_api_key: str = ""

    # General
    max_concurrency: int = 5
    request_timeout: int = 30

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
