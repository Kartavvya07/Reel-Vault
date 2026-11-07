"""Pydantic models and schemas for Reel Vault personal library."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.spike.schemas import CodeSnippet, ExtractedUrl, ReelAnalysisOutput, ToolResource


class VaultStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    SAVED = "saved (not analyzed)"


class VaultReel(BaseModel):
    """A single Instagram Reel saved in the user's personal vault."""

    shortcode: str
    original_url: str
    normalized_url: str
    path_type: str = "reel"
    status: str = VaultStatus.QUEUED.value
    created_at: str
    updated_at: str
    processed_at: Optional[str] = None
    retry_count: int = 0
    error_message: Optional[str] = None
    failure_category: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    takeaway: Optional[str] = None
    short_summary: Optional[str] = None
    key_points: List[str] = Field(default_factory=list)
    tools: List[ToolResource] = Field(default_factory=list)
    extracted_urls: List[ExtractedUrl] = Field(default_factory=list)
    code_snippets: List[CodeSnippet] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    language: str = "en"
    transcript: Optional[str] = None
    on_screen_text: Optional[str] = None
    confidence: float = 1.0
    is_bookmarked: bool = False
    analysis_mode: str = "audio"
    is_thin_extraction: bool = False
    relevance_score: Optional[float] = None
    match_snippet: Optional[str] = None
    match_source: Optional[str] = None


class AddReelRequest(BaseModel):
    url: str


class AddReelResponse(BaseModel):
    reel: VaultReel
    is_new: bool
    message: str


class RetryReelResponse(BaseModel):
    shortcode: str
    status: str
    message: str
    retry_count: int


class DeleteReelResponse(BaseModel):
    shortcode: str
    status: str
    message: str


class BookmarkResponse(BaseModel):
    shortcode: str
    is_bookmarked: bool
    message: str


class BackupRestoreResponse(BaseModel):
    status: str
    total_in_backup: int
    imported_count: int
    merged_count: int
    skipped_count: int
    errors: List[str] = Field(default_factory=list)
    message: str


class VaultStats(BaseModel):
    total_reels: int
    done_count: int
    processing_count: int
    queued_count: int
    failed_count: int
    unavailable_count: int
    daily_processed_count: int
    daily_cap: int
    categories: List[str] = Field(default_factory=list)


class VaultListResponse(BaseModel):
    reels: List[VaultReel]
    total_count: int
    stats: VaultStats
    is_degraded_search: bool = False
    embedding_model: Optional[str] = None
    is_mock_pipeline: bool = False

