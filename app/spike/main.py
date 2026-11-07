"""FastAPI Service for Reel Vault Phase 0 Ingestion Feasibility Spike."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import shutil
import sys
import logging
import secrets
import time
from typing import Optional
import uuid

from fastapi import FastAPI, HTTPException, Request, Response, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.spike.ai_processor import GeminiProcessor
from app.spike.config import settings
from app.spike.ingestion import CompositeIngestionPipeline
from app.spike.metrics import get_current_metrics
from app.spike.schemas import (
    ErrorDetail,
    ErrorResponse,
    ExperimentResult,
    ExperimentState,
    FailureCategory,
    FetchStatus,
    HealthResponse,
    MetricsSummary,
    ProcessReelRequest,
    ProcessReelResponse,
    RetryRequest,
    RetryResponse,
    ValidateUrlRequest,
    ValidateUrlResponse,
)
from app.spike.storage import (
    cleanup_temp_media,
    get_all_experiment_results,
    get_experiment_result,
    save_experiment_result,
)
from app.spike.url_parser import (
    InvalidInstagramURLError,
    SSRFValidationError,
    parse_and_normalize_url,
)
from app.vault.database import init_vault_db
from app.vault.importer import import_legacy_reels
from app.vault.pwa import STATIC_DIR, ensure_pwa_icons
from app.vault.router import vault_router
from app.vault.worker import start_vault_worker, stop_vault_worker
from fastapi.staticfiles import StaticFiles

# Configure logging without sensitive headers or tokens
logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("reel_vault_spike")


def validate_startup_pipeline_safety(settings_obj=None):
    """Enforces safety: refuses to start on Railway with mock pipeline unless ALLOW_MOCK=true."""
    cfg = settings_obj or settings
    if cfg.is_railway and not cfg.enable_real_pipeline and not cfg.allow_mock:
        error_msg = (
            "Refusing to start on Railway: ENABLE_REAL_PIPELINE is false and ALLOW_MOCK=true is not set. "
            "Real Gemini multimodal pipeline is strictly required in production."
        )
        logger.critical(error_msg)
        raise RuntimeError(error_msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes vault database, PWA assets, imports legacy reels, indexes embeddings, and manages worker lifecycle."""
    validate_startup_pipeline_safety()
    ensure_pwa_icons()
    init_vault_db()
    import_legacy_reels()
    try:
        from app.vault.semantic import index_all_reels
        index_all_reels()
    except Exception as e:
        logger.warning("Could not auto-index embeddings on startup: %s", e)
    start_vault_worker()
    yield
    stop_vault_worker()


app = FastAPI(
    title="Reel Vault Ingestion Feasibility Spike & Personal Library",
    description="Measurement harness and personal knowledge vault for Instagram Reels.",
    version=settings.service_version,
    lifespan=lifespan,
)

# Enable CORS for local testing / dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure PWA icons directory exists on import
ensure_pwa_icons()

# Mount static files for PWA icons
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Mount Reel Vault Personal Knowledge Library Router
app.include_router(vault_router)


pipeline = CompositeIngestionPipeline()
ai_engine = GeminiProcessor()
api_key_header = APIKeyHeader(name="X-Spike-Key", auto_error=False)


# ==============================================================================
# Security & Authentication Middleware
# ==============================================================================


@app.middleware("http")
async def verify_api_key_middleware(request: Request, call_next):
    """
    Enforces authentication across routes:
    - Allows public unauthenticated access to /health, /login, /logout, /manifest.json, /sw.js, and static assets.
    - Hands /, /reels/*, /share-target, and /api/vault/* to the vault router (which enforces vault token/cookie).
    - Protects all /spike/* operational routes with SPIKE_API_KEY.
    """
    path = request.url.path

    # Public endpoints, PWA assets, and Vault endpoints
    if (
        path in ("/health", "/login", "/logout", "/manifest.json", "/manifest.webmanifest", "/sw.js")
        or path == "/"
        or path.startswith("/share-target")
        or path.startswith("/static/")
        or path.startswith("/reels/")
        or path.startswith("/api/vault/")
    ):
        return await call_next(request)

    # In development mode only, if SPIKE_API_KEY is not set, allow open access for testing
    if not settings.is_production and not settings.spike_api_key:
        return await call_next(request)

    # In production, if SPIKE_API_KEY is not configured, fail closed
    if settings.is_production and not settings.spike_api_key:
        error_resp = ErrorResponse(
            error=ErrorDetail(
                code="SERVICE_UNCONFIGURED",
                message="Server authentication credential is not configured in production environment.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_resp.model_dump(),
        )

    # Extract key from X-Spike-Key header or Authorization: Bearer
    provided_key = request.headers.get("X-Spike-Key")
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        provided_key = auth_header.split(" ", 1)[1].strip()

    expected_key = settings.spike_api_key or ""
    if not provided_key or not secrets.compare_digest(provided_key, expected_key):
        error_resp = ErrorResponse(
            error=ErrorDetail(
                code="UNAUTHORIZED",
                message="Invalid or missing authentication credential.",
                status_code=status.HTTP_401_UNAUTHORIZED,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=error_resp.model_dump(),
        )

    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """RFC 7807 compliant error handler for HTTP exceptions."""
    error_payload = ErrorResponse(
        error=ErrorDetail(
            code="HTTP_ERROR",
            message=str(exc.detail),
            status_code=exc.status_code,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload.model_dump(),
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """RFC 7807 compliant error handler for unhandled exceptions (redacts internal details in production)."""
    logger.exception("Unhandled server exception: %s", exc)
    details = {"type": type(exc).__name__, "detail": str(exc)} if not settings.is_production else None

    error_payload = ErrorResponse(
        error=ErrorDetail(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected server error occurred during processing.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details=details,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_payload.model_dump(),
    )


# ==============================================================================
# Endpoints
# ==============================================================================


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint indicating service liveness and runtime diagnostics."""
    ffmpeg_found = shutil.which("ffmpeg") is not None
    ytdlp_found = False
    try:
        import yt_dlp
        ytdlp_found = True
    except ImportError:
        pass

    storage_persistent = bool(os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.getenv("SPIKE_DATA_DIR"))

    return HealthResponse(
        status="healthy",
        version=settings.service_version,
        environment=settings.environment if not settings.is_production else "production",
        timestamp=datetime.now(timezone.utc),
        python_version=sys.version.split()[0],
        ffmpeg_available=ffmpeg_found,
        ytdlp_available=ytdlp_found,
        storage_persistent=storage_persistent,
        gemini_configured=bool(settings.gemini_api_key),
        gemini_model=settings.gemini_model,
        pricing_input_usd_per_m=settings.cost_per_million_video_tokens,
        pricing_output_usd_per_m=settings.cost_per_million_output_tokens,
        server_ytdlp_enabled=settings.enable_server_ytdlp,
    )


@app.get("/spike/export", tags=["Spike"])
async def export_results():
    """
    Exports all recorded experiment telemetry for persistent off-host backup.
    Guarantees that telemetry is retrievable even if container disk is ephemeral.
    """
    results = get_all_experiment_results()
    is_persistent = bool(os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.getenv("SPIKE_DATA_DIR"))
    return {
        "total_records": len(results),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "storage_mode": "persistent_volume" if is_persistent else "ephemeral_local",
        "results": [r.model_dump() for r in results],
    }


@app.post("/spike/validate", response_model=ValidateUrlResponse, tags=["Spike"])
async def validate_url(payload: ValidateUrlRequest):
    """Validates and normalizes an Instagram URL, stripping tracking parameters."""
    try:
        norm_url, shortcode, path_type = parse_and_normalize_url(payload.url)
        return ValidateUrlResponse(
            original_url=payload.url,
            normalized_url=norm_url,
            shortcode=shortcode,
            path_type=path_type,
            is_valid=True,
            error_detail=None,
        )
    except (InvalidInstagramURLError, SSRFValidationError) as e:
        return ValidateUrlResponse(
            original_url=payload.url,
            normalized_url=None,
            shortcode=None,
            path_type=None,
            is_valid=False,
            error_detail=str(e),
        )


@app.post("/spike/process", response_model=ProcessReelResponse, tags=["Spike"])
async def process_reel(payload: ProcessReelRequest, retry_count: int = 0):
    """
    Executes the ingestion and AI analysis pipeline on a single Instagram Reel.
    """
    start_time = time.perf_counter()
    timestamp_str = datetime.now(timezone.utc).isoformat()
    session_id = uuid.uuid4().hex[:8]

    # 1. URL validation and SSRF check
    try:
        norm_url, shortcode, path_type = parse_and_normalize_url(payload.url)
    except SSRFValidationError as ssrf_err:
        exp_id = f"exp_ssrf_{session_id}"
        res = ExperimentResult(
            experiment_id=exp_id,
            shortcode="unknown",
            original_url=payload.url,
            normalized_url=payload.url,
            path_type="unknown",
            attempt_timestamp=timestamp_str,
            ingestion_provider="none",
            fetch_status=FetchStatus.INVALID_URL,
            failure_category=FailureCategory.SSRF_ATTEMPT,
            error_message=str(ssrf_err),
            processing_duration_seconds=round(time.perf_counter() - start_time, 3),
            retry_count=retry_count,
        )
        save_experiment_result(res)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ssrf_err))
    except InvalidInstagramURLError as inv_err:
        exp_id = f"exp_invalid_{session_id}"
        res = ExperimentResult(
            experiment_id=exp_id,
            shortcode="unknown",
            original_url=payload.url,
            normalized_url=payload.url,
            path_type="unknown",
            attempt_timestamp=timestamp_str,
            ingestion_provider="none",
            fetch_status=FetchStatus.INVALID_URL,
            failure_category=FailureCategory.INVALID_URL,
            error_message=str(inv_err),
            processing_duration_seconds=round(time.perf_counter() - start_time, 3),
            retry_count=retry_count,
        )
        save_experiment_result(res)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(inv_err))

    experiment_id = f"exp_{shortcode}_{session_id}"

    # 2. Run ingestion
    ingest_result, caption_only_fallback_used = await pipeline.ingest(
        url=norm_url,
        shortcode=shortcode,
        force_caption_only=payload.force_caption_only,
        session_id=session_id,
    )

    metadata_ok = bool(ingest_result.metadata)
    caption_ok = bool(ingest_result.caption and len(ingest_result.caption.strip()) > 0)
    media_ok = bool(ingest_result.media_path and not ingest_result.caption_only)

    # 3. AI Analysis if any content was retrieved
    ai_ok = False
    validation_ok = False
    e2e_ok = False
    analysis_output = None
    input_tokens = 0
    output_tokens = 0
    estimated_cost = 0.0
    failure_cat = ingest_result.failure_category
    error_msg = ingest_result.error_message

    try:
        if media_ok or caption_ok:
            ai_res = await ai_engine.analyze(
                media_path=ingest_result.media_path,
                caption=ingest_result.caption,
                shortcode=shortcode,
                media_duration_seconds=ingest_result.metadata.get("duration"),
            )
            ai_ok = ai_res.success
            input_tokens = ai_res.input_tokens
            output_tokens = ai_res.output_tokens
            estimated_cost = ai_res.estimated_cost_usd

            if ai_res.success and ai_res.output:
                validation_ok = True
                analysis_output = ai_res.output
                e2e_ok = True
                failure_cat = FailureCategory.NONE
                error_msg = None
            else:
                failure_cat = ai_res.failure_category
                error_msg = ai_res.error_message
    finally:
        # Guarantee cleanup of temporary local media files
        if ingest_result.media_path:
            cleanup_temp_media(ingest_result.media_path)
        cleanup_temp_media(f"{shortcode}_{session_id}")

    total_duration = round(time.perf_counter() - start_time, 3)

    # 4. Construct and save experiment result
    experiment_result = ExperimentResult(
        experiment_id=experiment_id,
        shortcode=shortcode,
        original_url=payload.url,
        normalized_url=norm_url,
        path_type=path_type,
        attempt_timestamp=timestamp_str,
        ingestion_provider=ingest_result.provider,
        fetch_status=ingest_result.fetch_status,
        metadata_success=metadata_ok,
        caption_success=caption_ok,
        media_retrieval_success=media_ok,
        ai_processing_success=ai_ok,
        output_validation_success=validation_ok,
        end_to_end_success=e2e_ok,
        caption_only_fallback_used=caption_only_fallback_used,
        failure_category=failure_cat,
        error_message=error_msg,
        processing_duration_seconds=total_duration,
        input_token_count=input_tokens,
        output_token_count=output_tokens,
        estimated_cost_usd=estimated_cost,
        analysis_output=analysis_output,
        raw_metadata=ingest_result.metadata,
        retry_count=retry_count,
    )
    save_experiment_result(experiment_result)

    final_state = ExperimentState.COMPLETED if e2e_ok else ExperimentState.FAILED
    msg = "Reel processed successfully." if e2e_ok else f"Processing failed: {error_msg or failure_cat.value}"

    return ProcessReelResponse(
        experiment_id=experiment_id,
        shortcode=shortcode,
        original_url=payload.url,
        status=final_state,
        message=msg,
        created_at=datetime.now(timezone.utc),
    )


@app.get("/spike/results/{experiment_id}", response_model=ExperimentResult, tags=["Spike"])
async def get_result(experiment_id: str):
    """Retrieves full telemetry and extracted structured data for a specific experiment run."""
    result = get_experiment_result(experiment_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Experiment '{experiment_id}' was not found.",
        )
    return result


@app.get("/spike/metrics", response_model=MetricsSummary, tags=["Spike"])
async def get_metrics():
    """Computes and returns aggregated statistical metrics across all recorded experiment runs."""
    return get_current_metrics()


@app.post("/spike/retry/{experiment_id}", response_model=RetryResponse, tags=["Spike"])
async def retry_experiment(experiment_id: str, payload: RetryRequest = RetryRequest()):
    """Re-attempts ingestion and analysis on a previously recorded experiment with accurate retry tracking."""
    existing = get_experiment_result(experiment_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Original experiment '{experiment_id}' not found.",
        )

    force_caption = (
        payload.force_caption_only
        if payload.force_caption_only is not None
        else existing.caption_only_fallback_used
    )

    next_retry = existing.retry_count + 1
    proc_req = ProcessReelRequest(
        url=existing.original_url,
        force_caption_only=force_caption,
    )
    res = await process_reel(proc_req, retry_count=next_retry)

    return RetryResponse(
        experiment_id=res.experiment_id,
        status=res.status,
        message=res.message,
        retry_count=next_retry,
    )
