"""Unit tests for ingestion adapters and fallback pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.spike.ingestion import (
    CompositeIngestionPipeline,
    IngestionResult,
    OEmbedAdapter,
    ServerYtDlpAdapter,
)
from app.spike.schemas import FailureCategory, FetchStatus


def test_ytdlp_error_classification():
    adapter = ServerYtDlpAdapter()
    
    # Login wall
    s, c = adapter._classify_error("Login required to view this post")
    assert s == FetchStatus.LOGIN_WALL
    assert c == FailureCategory.LOGIN_WALL

    # Private account
    s, c = adapter._classify_error("This account is private. Follow to view.")
    assert s == FetchStatus.PRIVATE_ACCOUNT
    assert c == FailureCategory.PRIVATE_ACCOUNT

    # 404 / removed
    s, c = adapter._classify_error("HTTP Error 404: Not Found")
    assert s == FetchStatus.POST_NOT_FOUND
    assert c == FailureCategory.POST_NOT_FOUND

    # Rate limit
    s, c = adapter._classify_error("HTTP Error 429: Too Many Requests")
    assert s == FetchStatus.RATE_LIMITED
    assert c == FailureCategory.RATE_LIMITED

    # Generic download error
    s, c = adapter._classify_error("Extractor error: unknown video format")
    assert s == FetchStatus.MEDIA_UNAVAILABLE
    assert c == FailureCategory.MEDIA_DOWNLOAD_FAILED


def test_composite_pipeline_fallback_to_oembed():
    import asyncio

    async def _test():
        pipeline = CompositeIngestionPipeline()

        # Mock yt-dlp failure (login wall)
        mock_ytdlp_res = IngestionResult(
            provider="server_ytdlp",
            fetch_status=FetchStatus.LOGIN_WALL,
            failure_category=FailureCategory.LOGIN_WALL,
            error_message="Login required",
        )
        pipeline.ytdlp_adapter.fetch = AsyncMock(return_value=mock_ytdlp_res)

        # Mock oEmbed success (caption retrieved)
        mock_oembed_res = IngestionResult(
            provider="oembed",
            fetch_status=FetchStatus.SUCCESS_CAPTION_ONLY,
            failure_category=FailureCategory.NONE,
            caption="Sample recovered caption text",
            metadata={"title": "Sample title"},
            caption_only=True,
        )
        pipeline.oembed_adapter.fetch = AsyncMock(return_value=mock_oembed_res)

        res, fallback_used = await pipeline.ingest(
            url="https://www.instagram.com/reel/TestCode/",
            shortcode="TestCode",
            force_caption_only=False,
        )

        assert fallback_used is True
        assert res.caption == "Sample recovered caption text"
        assert res.fetch_status == FetchStatus.SUCCESS_CAPTION_ONLY
        assert res.metadata.get("ytdlp_failure_category") == "LOGIN_WALL"

    asyncio.run(_test())


def test_composite_pipeline_full_success_no_fallback():
    import asyncio

    async def _test():
        pipeline = CompositeIngestionPipeline()

        mock_ytdlp_res = IngestionResult(
            provider="server_ytdlp",
            fetch_status=FetchStatus.SUCCESS_FULL,
            failure_category=FailureCategory.NONE,
            media_path="/tmp/fake.mp4",
            caption="Full video caption",
        )
        pipeline.ytdlp_adapter.fetch = AsyncMock(return_value=mock_ytdlp_res)
        pipeline.oembed_adapter.fetch = AsyncMock()

        res, fallback_used = await pipeline.ingest(
            url="https://www.instagram.com/reel/SuccessCode/",
            shortcode="SuccessCode",
        )

        assert fallback_used is False
        assert res.fetch_status == FetchStatus.SUCCESS_FULL
        assert res.media_path == "/tmp/fake.mp4"
        pipeline.oembed_adapter.fetch.assert_not_called()

    asyncio.run(_test())
