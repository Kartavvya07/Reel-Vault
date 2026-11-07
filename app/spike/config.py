"""Configuration settings for Phase 0 Ingestion Feasibility Spike."""

import os
from pathlib import Path
from typing import List, Tuple
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load environment variables from .env file if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Persistent volume resolution (Railway attaches volumes to custom mount paths)
_volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.getenv("SPIKE_DATA_DIR")
_resolved_data_dir = Path(_volume_mount) if _volume_mount else (BASE_DIR / "spike_data")


def get_verified_model_pricing(model_name: str) -> Tuple[float, float]:
    """
    Returns verified per-1M-token pricing (input_cost_usd, output_cost_usd)
    for Google Gemini models based on published Google Developer API rates.
    """
    name = model_name.lower()
    if "flash-lite" in name or "2.0-flash" in name or "3.1-flash-lite" in name:
        # Gemini Flash-Lite tier: $0.10/1M input, $0.40/1M output
        return 0.10, 0.40
    elif "1.5-flash" in name:
        # Gemini 1.5 Flash: $0.075/1M input, $0.30/1M output
        return 0.075, 0.30
    elif "pro" in name:
        # Gemini Pro tier
        return 1.25, 5.00
    # Standard Flash tier (Gemini 2.5 Flash, 3.5 Flash, 3.8 Flash): $0.30/1M input, $2.50/1M output
    return 0.30, 2.50


class SpikeSettings(BaseModel):
    """Spike configuration with safe defaults and verified pricing."""

    environment: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    debug: bool = Field(default_factory=lambda: os.getenv("DEBUG", "false").lower() in ("true", "1", "yes"))
    service_version: str = "0.1.0-spike"

    # API Security: Mandatory in production (non-development)
    spike_api_key: str | None = Field(default_factory=lambda: os.getenv("SPIKE_API_KEY"))

    # Google Gemini Multimodal API Configuration
    gemini_api_key: str | None = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    )
    gemini_model: str = Field(
        default_factory=lambda: os.getenv("GEMINI_MULTIMODAL_MODEL", "gemini-3.5-flash")
    )
    gemini_embedding_model: str = Field(
        default_factory=lambda: os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    )
    gemini_timeout_seconds: int = Field(
        default_factory=lambda: int(os.getenv("GEMINI_TIMEOUT_SECONDS", "90"))
    )

    # Ingestion Controls
    enable_server_ytdlp: bool = Field(
        default_factory=lambda: os.getenv("ENABLE_SERVER_YTDLP_EXPERIMENT", "true").lower() in ("true", "1", "yes")
    )
    download_timeout_seconds: int = Field(default=60)
    max_file_size_bytes: int = Field(default=50 * 1024 * 1024)  # 50 MB
    allowed_hosts: List[str] = Field(default_factory=lambda: ["instagram.com", "www.instagram.com"])
    cookies_file: str | None = Field(default_factory=lambda: os.getenv("COOKIES_FILE"))
    analyze_posts: bool = Field(
        default_factory=lambda: os.getenv("ANALYZE_POSTS", "false").lower() in ("true", "1", "yes")
    )

    # Pricing parameters: Dynamically computed from verified model rates with env override
    @property
    def cost_per_million_video_tokens(self) -> float:
        override = os.getenv("COST_PER_MILLION_INPUT_TOKENS")
        if override:
            return float(override)
        in_rate, _ = get_verified_model_pricing(self.gemini_model)
        return in_rate

    @property
    def cost_per_million_output_tokens(self) -> float:
        override = os.getenv("COST_PER_MILLION_OUTPUT_TOKENS")
        if override:
            return float(override)
        _, out_rate = get_verified_model_pricing(self.gemini_model)
        return out_rate

    video_tokens_per_second: int = 258
    audio_tokens_per_second: int = 32

    # Directories (Persistent Volume aware)
    data_dir: Path = Field(default_factory=lambda: _resolved_data_dir)
    results_file: Path = Field(default_factory=lambda: _resolved_data_dir / "results.jsonl")
    sqlite_db_file: Path = Field(default_factory=lambda: _resolved_data_dir / "experiments.db")
    temp_media_dir: Path = Field(default_factory=lambda: _resolved_data_dir / "temp_media")

    # Vault Personal Library Settings
    vault_auth_token: str | None = Field(
        default_factory=lambda: os.getenv("VAULT_AUTH_TOKEN")
        or os.getenv("VAULT_PASSWORD")
        or os.getenv("SPIKE_API_KEY")
    )
    daily_processing_cap: int = Field(
        default_factory=lambda: int(os.getenv("DAILY_PROCESSING_CAP", "50"))
    )
    worker_pause_seconds: float = Field(
        default_factory=lambda: float(os.getenv("WORKER_PAUSE_SECONDS", "1.5"))
    )
    enable_real_pipeline: bool = Field(
        default_factory=lambda: os.getenv("ENABLE_REAL_PIPELINE", "true" if (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")) else "false").lower()
        in ("true", "1", "yes")
    )
    allow_mock: bool = Field(
        default_factory=lambda: os.getenv("ALLOW_MOCK", "false").lower() in ("true", "1", "yes")
    )

    @property
    def is_railway(self) -> bool:
        """Checks if running inside a Railway cloud deployment."""
        return bool(
            os.getenv("RAILWAY_ENVIRONMENT")
            or os.getenv("RAILWAY_PROJECT_ID")
            or os.getenv("RAILWAY_SERVICE_ID")
            or os.getenv("RAILWAY_PUBLIC_DOMAIN")
            or os.getenv("IS_RAILWAY", "").lower() in ("true", "1", "yes")
        )

    @property
    def is_production(self) -> bool:
        """Determines if the application is running in a live / cloud environment."""
        return self.is_railway or (self.environment.lower() not in ("development", "test", "testing"))


settings = SpikeSettings()

# Ensure directories exist
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.temp_media_dir.mkdir(parents=True, exist_ok=True)
