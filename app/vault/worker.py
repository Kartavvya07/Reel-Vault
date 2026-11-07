"""Asynchronous worker loop for Reel Vault: single-threaded, paused, daily-capped."""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Optional

from app.spike.ai_processor import GeminiProcessor
from app.spike.config import settings
from app.spike.ingestion import CompositeIngestionPipeline
from app.spike.schemas import FailureCategory, FetchStatus, ReelAnalysisOutput, ToolResource
from app.spike.storage import cleanup_temp_media
from app.vault.database import (
    complete_reel,
    fail_reel,
    get_daily_processed_count,
    get_next_queued_reel,
    requeue_stale_processing,
    set_reel_processing,
)
from app.vault.models import VaultReel, VaultStatus

logger = logging.getLogger(__name__)

_worker_task: Optional[asyncio.Task] = None
_should_run: bool = True

pipeline = CompositeIngestionPipeline()
ai_engine = GeminiProcessor()


def find_precomputed_analysis(shortcode: str) -> Optional[ReelAnalysisOutput]:
    """Looks up cached analysis from server_export_checkpoint.json if already processed."""
    checkpoint_file = settings.data_dir / "server_export_checkpoint.json"
    if not checkpoint_file.exists():
        return None

    try:
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for r in data.get("results", []):
            if r.get("shortcode") == shortcode and r.get("analysis_output"):
                return ReelAnalysisOutput(**r["analysis_output"])
    except Exception as e:
        logger.debug("Error checking checkpoint for %s: %s", shortcode, e)
    return None


async def process_single_vault_reel(reel: VaultReel, force_video: bool = False) -> None:
    """Executes the ingestion and multimodal extraction for one reel."""
    shortcode = reel.shortcode
    logger.info("Worker starting processing for reel: %s (%s, force_video=%s)", shortcode, reel.normalized_url, force_video)

    # 0. Skip manual post without analysis
    is_post = reel.path_type in ("p", "tv", "post") or "/p/" in reel.normalized_url or "/tv/" in reel.normalized_url
    if not settings.analyze_posts and is_post:
        logger.info("Skipping worker analysis for manual post %s (analyze_posts=false).", shortcode)
        return

    # 1. Enforce Daily Processing Cap
    daily_count = get_daily_processed_count()
    if daily_count >= settings.daily_processing_cap:
        logger.warning("Daily processing cap reached (%d/%d). Failing %s", daily_count, settings.daily_processing_cap, shortcode)
        fail_reel(
            shortcode=shortcode,
            status=VaultStatus.FAILED.value,
            error_message=f"Daily processing cap of {settings.daily_processing_cap} reels reached. Try again tomorrow.",
            failure_category="DAILY_CAP_EXCEEDED",
        )
        return

    # Mark state: processing
    set_reel_processing(shortcode)

    # 2. Check for pre-computed analysis (only if not forced video re-analysis)
    if not force_video:
        cached_analysis = find_precomputed_analysis(shortcode)
        if cached_analysis:
            logger.info("Found verified pre-computed analysis for %s. Completing immediately.", shortcode)
            complete_reel(shortcode, cached_analysis, analysis_mode="audio", is_thin_extraction=False)
            return

    # 3. Check if live pipeline is permitted
    if settings.enable_real_pipeline and settings.gemini_api_key:
        session_id = f"vault_{shortcode[:6]}"
        try:
            initial_mode = "video_lowres" if force_video else "audio"
            ingest_result, is_caption_fallback = await pipeline.ingest(
                url=reel.normalized_url,
                shortcode=shortcode,
                force_caption_only=False,
                session_id=session_id,
                media_mode=initial_mode,
            )
            analysis_mode = "video" if force_video else "audio"

            # Check if post hit login wall
            if ingest_result.fetch_status == FetchStatus.LOGIN_WALL:
                fail_reel(
                    shortcode=shortcode,
                    status=VaultStatus.UNAVAILABLE.value,
                    error_message="unavailable: login required",
                    failure_category=FailureCategory.LOGIN_WALL.value,
                )
                return

            # Check if post was removed or private
            if ingest_result.fetch_status in (
                FetchStatus.POST_NOT_FOUND,
                FetchStatus.PRIVATE_ACCOUNT,
            ) and not ingest_result.caption:
                fail_reel(
                    shortcode=shortcode,
                    status=VaultStatus.UNAVAILABLE.value,
                    error_message=ingest_result.error_message or "Instagram Reel/Post is private or no longer available.",
                    failure_category=ingest_result.failure_category.value,
                )
                return

            if not ingest_result.media_path and not ingest_result.image_paths and not ingest_result.caption:
                fail_reel(
                    shortcode=shortcode,
                    status=VaultStatus.FAILED.value,
                    error_message=ingest_result.error_message or "Could not retrieve media or caption.",
                    failure_category=ingest_result.failure_category.value,
                )
                return

            if ingest_result.image_paths:
                analysis_mode = "images"
                ai_res = await ai_engine.analyze(
                    image_paths=ingest_result.image_paths,
                    caption=ingest_result.caption,
                    shortcode=shortcode,
                )
            elif is_caption_fallback or not ingest_result.media_path:
                analysis_mode = "caption_only"
                ai_res = await ai_engine.analyze(
                    caption=ingest_result.caption,
                    shortcode=shortcode,
                )
            else:
                if ingest_result.media_path.lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
                    analysis_mode = "video"

                # Primary AI Analysis
                ai_res = await ai_engine.analyze(
                    media_path=ingest_result.media_path,
                    caption=ingest_result.caption,
                    shortcode=shortcode,
                    media_duration_seconds=ingest_result.metadata.get("duration"),
                )

                # Step B: If speech is missing or very short, download low-res video (<=480p)
                transcript_text = (ai_res.output.transcript or "") if (ai_res.success and ai_res.output) else ""
                transcript_words = len(transcript_text.split())
                needs_video = (transcript_words < 15) and (analysis_mode != "video")

                if ai_res.success and ai_res.output and needs_video and not is_caption_fallback:
                    logger.info(
                        "Speech is missing or very short (%d words) for %s. Downloading low-res video (<=480p) for visual extraction...",
                        transcript_words,
                        shortcode,
                    )
                    # Cleanup audio first
                    cleanup_temp_media(f"{shortcode}_{session_id}")
                    video_session = f"vault_vid_{shortcode[:6]}"
                    try:
                        video_ingest, _ = await pipeline.ingest(
                            url=reel.normalized_url,
                            shortcode=shortcode,
                            session_id=video_session,
                            media_mode="video_lowres",
                        )
                        if video_ingest.fetch_status == FetchStatus.SUCCESS_FULL and video_ingest.media_path:
                            video_ai_res = await ai_engine.analyze(
                                media_path=video_ingest.media_path,
                                caption=ingest_result.caption or video_ingest.caption,
                                shortcode=shortcode,
                                media_duration_seconds=video_ingest.metadata.get("duration"),
                            )
                            if video_ai_res.success and video_ai_res.output:
                                ai_res = video_ai_res
                                analysis_mode = "video"
                    finally:
                        cleanup_temp_media(f"{shortcode}_{video_session}")

            if ai_res.success and ai_res.output:
                output = ai_res.output

                # Mark on-screen extracted links as unverified
                for t in output.tools_and_resources:
                    if analysis_mode in ("video", "images") or t.source in ("on_screen_text", "visual"):
                        t.verification_status = "unverified"
                        if not t.source or t.source == "speech":
                            t.source = "on_screen_text"

                # Check if extraction is thin
                has_tools = bool(output.tools_and_resources and len(output.tools_and_resources) > 0)
                has_pts = bool(output.key_points and len(output.key_points) > 0)
                has_onscreen = bool(output.on_screen_text and len(output.on_screen_text.strip()) > 5)
                words = len((output.transcript or "").split())
                is_thin = not has_tools and not has_pts and not has_onscreen and words < 12

                complete_reel(
                    shortcode,
                    output,
                    analysis_mode=analysis_mode,
                    is_thin_extraction=is_thin,
                )
                logger.info("Successfully completed real %s analysis for reel: %s (thin: %s)", analysis_mode, shortcode, is_thin)
            else:
                fail_reel(
                    shortcode=shortcode,
                    status=VaultStatus.FAILED.value,
                    error_message=ai_res.error_message or "AI multimodal extraction failed.",
                    failure_category=ai_res.failure_category.value,
                )
        except Exception as exc:
            logger.exception("Unexpected error analyzing %s: %s", shortcode, exc)
            fail_reel(
                shortcode=shortcode,
                status=VaultStatus.FAILED.value,
                error_message=str(exc),
                failure_category=FailureCategory.UNEXPECTED_EXCEPTION.value,
            )
        finally:
            # Delete temporary media immediately after analysis
            cleanup_temp_media(f"{shortcode}_{session_id}")
            cleanup_temp_media(f"gdl_{shortcode}_{session_id}")
    else:
        # Mock / Test mode for zero-cost safe testing
        logger.info("Mocking analysis for test reel: %s", shortcode)
        mock_analysis = ReelAnalysisOutput(
            title=f"Knowledge Insights: {shortcode}",
            category="Developer Tools & Workflow",
            tags=["developer", "ai", "tools", "workflow"],
            short_summary=f"Automated knowledge extraction for Instagram Reel {shortcode}.",
            takeaway="Prioritize minimal, high-leverage workflows and modular architecture.",
            key_points=[
                "Extracted via Reel Vault asynchronous worker.",
                "Original URL and identity preserved.",
                "Text-only storage with instant cleanup of temporary media.",
            ],
            tools_and_resources=[
                ToolResource(
                    name="FastAPI",
                    type="python_package",
                    url="https://fastapi.tiangolo.com",
                    source="ai_interpretation",
                    verification_status="verified",
                ),
                ToolResource(
                    name="SQLite FTS5",
                    type="cli_tool",
                    source="ai_interpretation",
                    verification_status="verified",
                ),
            ],
            transcript="Spoken transcript extracted from audio stream.",
            on_screen_text=f"Reel shortcode {shortcode} analyzed successfully.",
            original_language="en",
            english_summary=f"Automated knowledge extraction for Instagram Reel {shortcode}.",
            confidence=0.98,
        )
        complete_reel(shortcode, mock_analysis, analysis_mode="mock", is_thin_extraction=False)


async def run_vault_worker_loop() -> None:
    """Continuous background loop processing one queued reel at a time."""
    global _should_run
    logger.info("Vault background worker loop started.")

    # 1. On startup, re-queue any stale 'processing' records
    requeued = requeue_stale_processing()
    if requeued > 0:
        logger.info("Re-queued %d stale processing reel(s) on startup.", requeued)

    while _should_run:
        try:
            reel = get_next_queued_reel()
            if reel:
                await process_single_vault_reel(reel)
                # Pause between reels as specified in locked decisions
                await asyncio.sleep(settings.worker_pause_seconds)
            else:
                # No pending items, poll gracefully every 2 seconds
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            logger.info("Vault worker loop cancelled.")
            break
        except Exception as e:
            logger.error("Error in vault worker loop: %s", e)
            await asyncio.sleep(3.0)


def start_vault_worker() -> None:
    """Launches the background worker task."""
    global _worker_task, _should_run
    _should_run = True
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(run_vault_worker_loop())


def stop_vault_worker() -> None:
    """Stops the background worker task."""
    global _worker_task, _should_run
    _should_run = False
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()


async def trigger_immediate_reanalysis(shortcode: str, force_video: bool = True) -> None:
    """Triggers immediate re-analysis for a specific reel in background."""
    from app.vault.database import reanalyze_reel, get_reel_by_shortcode
    reel = reanalyze_reel(shortcode, force_video=force_video)
    if reel:
        asyncio.create_task(process_single_vault_reel(reel, force_video=force_video))

