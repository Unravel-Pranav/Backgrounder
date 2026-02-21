from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class LinkedInProviderName(str, Enum):
    serpapi = "serpapi"
    playwright = "playwright"
    proxycurl = "proxycurl"
    rapidapi = "rapidapi"


class BackgroundCheckRequest(BaseModel):
    name: str = Field(..., min_length=1, examples=["Satya Nadella"])
    company: Optional[str] = Field(None, examples=["Microsoft"])
    location: Optional[str] = Field(None, examples=["Redmond, WA"])
    title: Optional[str] = Field(None, examples=["CEO"])
    linkedin_url: Optional[str] = Field(None, examples=["https://linkedin.com/in/satyanadella"])
    provider: Optional[LinkedInProviderName] = Field(
        None,
        description="Override the default LinkedIn provider for this request",
    )


class ResumeData(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    website: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    experience: list[dict] = Field(default_factory=list)
    education: list[dict] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    key_search_terms: list[str] = Field(default_factory=list)
    raw_text: Optional[str] = None


class LinkedInProfile(BaseModel):
    url: Optional[str] = None
    name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    summary: Optional[str] = None
    experience: list[dict] = Field(default_factory=list)
    education: list[dict] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[dict] = Field(default_factory=list)
    raw_text: Optional[str] = None


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str


class GitHubProfile(BaseModel):
    username: str
    url: str
    name: Optional[str] = None
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    blog: Optional[str] = None
    public_repos: int = 0
    followers: int = 0
    following: int = 0
    top_repos: list[dict] = Field(default_factory=list)


class IdentityVerification(BaseModel):
    confidence: str = ""  # "high", "medium", "low"
    reasoning: str = ""
    multiple_people_detected: bool = False
    profiles_found: list[dict] = Field(default_factory=list)
    cross_reference_notes: list[str] = Field(default_factory=list)


class BackgroundVerdict(BaseModel):
    rating: str = ""  # "clean", "caution", "red_flags"
    score: int = 0  # 0-100
    summary: str = ""
    resume_vs_online: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class CompanyCheck(BaseModel):
    name: str
    verified: bool = False
    evidence_url: Optional[str] = None
    description: str = ""


class SocialProfile(BaseModel):
    platform: str
    url: str
    username: Optional[str] = None
    snippet: str = ""


class ReferenceContact(BaseModel):
    name: str
    title: str = ""
    company: str = ""
    linkedin_url: Optional[str] = None
    category: str = ""  # "HR / People Ops", "Management", "Same Department"
    snippet: str = ""


class PhotoMatch(BaseModel):
    url: str
    title: str = ""
    source: str = ""
    thumbnail: str = ""
    platform: Optional[str] = None


class AggregatedData(BaseModel):
    linkedin: Optional[LinkedInProfile] = None
    github_profiles: list[GitHubProfile] = Field(default_factory=list)
    resume: Optional[ResumeData] = None
    company_checks: list[CompanyCheck] = Field(default_factory=list)
    social_profiles: list[SocialProfile] = Field(default_factory=list)
    photo_matches: list[PhotoMatch] = Field(default_factory=list)
    reference_contacts: list[ReferenceContact] = Field(default_factory=list)
    search_results: list[SearchResult] = Field(default_factory=list)
    news_articles: list[SearchResult] = Field(default_factory=list)
    raw_context: str = ""


class BackgroundReport(BaseModel):
    name: str
    generated_at: datetime
    linkedin_profile: Optional[LinkedInProfile] = None
    github_profiles: list[GitHubProfile] = Field(default_factory=list)
    resume_data: Optional[ResumeData] = None
    company_checks: list[CompanyCheck] = Field(default_factory=list)
    social_profiles: list[SocialProfile] = Field(default_factory=list)
    photo_matches: list[PhotoMatch] = Field(default_factory=list)
    reference_contacts: list[ReferenceContact] = Field(default_factory=list)
    identity_verification: Optional[IdentityVerification] = None
    verdict: Optional[BackgroundVerdict] = None
    summary: str
    professional_background: str
    key_highlights: list[str]
    news_mentions: list[SearchResult]
    sources_used: list[str]
    provider_used: str
    confidence_note: str = ""
