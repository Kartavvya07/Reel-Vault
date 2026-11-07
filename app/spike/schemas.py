"""Pydantic schemas and data contracts for Phase 0 Ingestion Feasibility Spike."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator


class FetchStatus(str, Enum):
    """Granular fetch status emitted by ingestion adapters."""

    SUCCESS_FULL = "SUCCESS_FULL"
    SUCCESS_CAPTION_ONLY = "SUCCESS_CAPTION_ONLY"
    PRIVATE_ACCOUNT = "PRIVATE_ACCOUNT"
    POST_NOT_FOUND = "POST_NOT_FOUND"
    LOGIN_WALL = "LOGIN_WALL"
    RATE_LIMITED = "RATE_LIMITED"
    MEDIA_UNAVAILABLE = "MEDIA_UNAVAILABLE"
    FAILED_TEMPORARY = "FAILED_TEMPORARY"
    INVALID_URL = "INVALID_URL"


class FailureCategory(str, Enum):
    """Categorized root-cause classification for experiment telemetry."""

    NONE = "NONE"
    INVALID_URL = "INVALID_URL"
    SSRF_ATTEMPT = "SSRF_ATTEMPT"
    PRIVATE_ACCOUNT = "PRIVATE_ACCOUNT"
    POST_NOT_FOUND = "POST_NOT_FOUND"
    LOGIN_WALL = "LOGIN_WALL"
    RATE_LIMITED = "RATE_LIMITED"
    MEDIA_DOWNLOAD_FAILED = "MEDIA_DOWNLOAD_FAILED"
    AI_AUTH_ERROR = "AI_AUTH_ERROR"
    AI_SCHEMA_ERROR = "AI_SCHEMA_ERROR"
    AI_API_ERROR = "AI_API_ERROR"
    TIMEOUT = "TIMEOUT"
    UNEXPECTED_EXCEPTION = "UNEXPECTED_EXCEPTION"


class ExperimentState(str, Enum):
    """Lifecycle state of an individual experiment run."""

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    FAILED = "FAILED"


# ==============================================================================
# AI Extraction Structured Schemas (Pydantic Output Model for Gemini)
# ==============================================================================


class ToolResource(BaseModel):
    """An individual tool, library, website, or resource extracted from content."""

    name: str = Field(..., description="Exact name of tool, library, or software.")
    type: str = Field(
        default="other",
        description="Category of tool: python_package, npm_package, github_repo, web_app, ui_library, book, podcast, creator, cli_tool, other",
    )
    url: Optional[str] = Field(default=None, description="Explicitly mentioned or shown URL.")
    source: str = Field(
        default="speech",
        description="Source of claim: speech, on_screen_text, caption, or ai_interpretation",
    )
    verification_status: str = Field(default="unverified", description="verified or unverified")


class ExtractedUrl(BaseModel):
    """An external link discovered in the Reel."""

    url: str
    context: str = ""
    source: str = "caption"


class CodeSnippet(BaseModel):
    """A command line or code snippet demonstrated in the Reel."""

    language_or_shell: str = "bash"
    code: str
    description: str = ""


class ReelAnalysisOutput(BaseModel):
    """Strict structured JSON extraction schema for Reel Vault multimodal analysis."""

    title: str = Field(..., description="Concise, descriptive title for the Reel.")
    category: str = Field(..., description="Primary domain category.")
    tags: List[str] = Field(default_factory=list, description="3-8 lowercase keyword tags.")
    short_summary: str = Field(..., description="2-3 sentence comprehensive summary.")
    takeaway: str = Field(..., description="Single bold actionable tip or core insight.")
    key_points: List[str] = Field(default_factory=list, description="Ordered list of key points.")
    tools_and_resources: List[ToolResource] = Field(default_factory=list)
    extracted_urls: List[ExtractedUrl] = Field(default_factory=list)
    commands_or_code_snippets: List[CodeSnippet] = Field(default_factory=list)
    transcript: Optional[str] = Field(default=None, description="Verbatim speech transcript in original language.")
    on_screen_text: Optional[str] = Field(default=None, description="Verbatim text overlays from visual frames.")
    original_language: str = Field(default="en", description="Detected language code (en, hi, kn, mixed).")
    english_summary: str = Field(..., description="Clear summary in English.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    processing_warnings: List[str] = Field(default_factory=list)


# ==============================================================================
# API Request & Response Schemas
# ==============================================================================


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    timestamp: datetime
    python_version: str
    ffmpeg_available: bool
    ytdlp_available: bool
    storage_persistent: bool
    gemini_configured: bool
    gemini_model: str
    pricing_input_usd_per_m: float
    pricing_output_usd_per_m: float
    server_ytdlp_enabled: bool


class ValidateUrlRequest(BaseModel):
    url: str


class ValidateUrlResponse(BaseModel):
    original_url: str
    normalized_url: Optional[str] = None
    shortcode: Optional[str] = None
    path_type: Optional[str] = None
    is_valid: bool
    error_detail: Optional[str] = None


class ProcessReelRequest(BaseModel):
    url: str
    force_caption_only: bool = False
    notes: Optional[str] = None


class ProcessReelResponse(BaseModel):
    experiment_id: str
    shortcode: str
    original_url: str
    status: ExperimentState
    message: str
    created_at: datetime


class RetryRequest(BaseModel):
    force_caption_only: Optional[bool] = None


class RetryResponse(BaseModel):
    experiment_id: str
    status: ExperimentState
    message: str
    retry_count: int


# ==============================================================================
# Storage & Telemetry Log Schemas
# ==============================================================================


class ExperimentResult(BaseModel):
    """Complete traceable log record for an individual experiment run."""

    experiment_id: str
    shortcode: str
    original_url: str
    normalized_url: str
    path_type: str  # "reel" or "p"
    attempt_timestamp: str
    ingestion_provider: str
    fetch_status: FetchStatus
    metadata_success: bool = False
    caption_success: bool = False
    media_retrieval_success: bool = False
    ai_processing_success: bool = False
    output_validation_success: bool = False
    end_to_end_success: bool = False
    caption_only_fallback_used: bool = False
    failure_category: FailureCategory = FailureCategory.NONE
    error_message: Optional[str] = None
    processing_duration_seconds: float = 0.0
    input_token_count: Optional[int] = None
    output_token_count: Optional[int] = None
    estimated_cost_usd: float = 0.0
    analysis_output: Optional[ReelAnalysisOutput] = None
    raw_metadata: Optional[Dict[str, Any]] = None
    retry_count: int = 0


class MetricsSummary(BaseModel):
    """Aggregated experiment statistics across all attempts."""

    total_attempts: int
    unique_reels_processed: int
    url_resolution_success_rate: float
    metadata_retrieval_success_rate: float
    caption_retrieval_success_rate: float
    media_retrieval_success_rate: float
    ai_analysis_success_rate: float
    structured_output_validation_success_rate: float
    end_to_end_processing_success_rate: float
    caption_only_fallback_rate: float
    median_processing_duration_seconds: float
    p95_processing_duration_seconds: float
    total_estimated_cost_usd: float
    cost_per_successful_reel_usd: float
    failure_distribution: Dict[str, int]
    provider_distribution: Dict[str, int]
    generated_at: datetime


class ErrorDetail(BaseModel):
    code: str
    message: str
    status_code: int
    details: Optional[Dict[str, Any]] = None
    timestamp: str
    request_id: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail

