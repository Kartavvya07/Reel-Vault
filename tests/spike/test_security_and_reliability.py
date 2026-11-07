"""Unit and integration tests for security, reliability, authentication, SSRF, and cleanup."""

import asyncio
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from app.spike.ai_processor import GeminiProcessor
from app.spike.config import settings
from app.spike.ingestion import IngestionResult, ServerYtDlpAdapter
from app.spike.main import app
from app.spike.metadata import safe_http_get
from app.spike.metrics import compute_metrics
from app.spike.schemas import ExperimentResult, FailureCategory, FetchStatus
from app.spike.storage import cleanup_temp_media, get_experiment_result, save_experiment_result
from app.spike.url_parser import SSRFValidationError, parse_and_normalize_url, validate_redirect_url


@pytest.fixture
def client():
    return TestClient(app)


# ==============================================================================
# Security & Authentication Tests
# ==============================================================================


def test_missing_api_key_fails_closed_in_production(client):
    """In production mode, if SPIKE_API_KEY is unset, requests fail closed with 503."""
    with patch.object(settings, "environment", "production"):
        with patch.object(settings, "spike_api_key", None):
            res = client.post("/spike/validate", json={"url": "https://www.instagram.com/reel/123/"})
            assert res.status_code == 503
            assert res.json()["error"]["code"] == "SERVICE_UNCONFIGURED"


def test_invalid_api_key_rejected(client):
    """Requests with incorrect API key return 401."""
    with patch.object(settings, "spike_api_key", "correct-secret-token"):
        res = client.post(
            "/spike/validate",
            headers={"X-Spike-Key": "wrong-token"},
            json={"url": "https://www.instagram.com/reel/123/"},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "UNAUTHORIZED"


def test_valid_api_key_accepted(client):
    """Requests with correct API key succeed."""
    with patch.object(settings, "spike_api_key", "correct-secret-token"):
        res = client.post(
            "/spike/validate",
            headers={"X-Spike-Key": "correct-secret-token"},
            json={"url": "https://www.instagram.com/reel/TEST_SAMPLE_01/"},
        )
        assert res.status_code == 200
        assert res.json()["is_valid"] is True


def test_health_check_publicly_accessible(client):
    """Health check remains open even when authentication is enforced."""
    with patch.object(settings, "spike_api_key", "secret-key"):
        with patch.object(settings, "environment", "production"):
            res = client.get("/health")
            assert res.status_code == 200
            assert res.json()["status"] == "healthy"


# ==============================================================================
# SSRF & Redirect Defense Tests
# ==============================================================================


def test_redirect_to_non_instagram_host_rejected():
    """Validating redirect hops rejects redirects to unauthorized domains."""
    with pytest.raises(SSRFValidationError):
        validate_redirect_url("https://malicious.com/steal-data")

    with pytest.raises(SSRFValidationError):
        validate_redirect_url("http://169.254.169.254/latest/meta-data/")


def test_redirect_to_private_ip_rejected():
    """Validating redirect hops rejects private network IP addresses."""
    with pytest.raises(SSRFValidationError):
        validate_redirect_url("http://127.0.0.1:8000/internal")

    with pytest.raises(SSRFValidationError):
        validate_redirect_url("http://192.168.1.1/admin")


# ==============================================================================
# Ingestion & File Security Tests
# ==============================================================================


def test_oversized_media_rejected():
    """Media exceeding max_file_size_bytes is rejected and purged."""
    adapter = ServerYtDlpAdapter()
    dummy_file = settings.temp_media_dir / "testcode_sess1.mp4"
    dummy_file.write_bytes(b"0" * 2000)

    try:
        with patch.object(settings, "max_file_size_bytes", 1000):
            # Simulate locating a file that exceeds the 1000 bytes limit
            with patch("yt_dlp.YoutubeDL") as mock_ydl:
                mock_inst = MagicMock()
                mock_inst.extract_info.return_value = {"id": "testcode", "title": "test"}
                mock_ydl.return_value.__enter__.return_value = mock_inst

                res = adapter._sync_download(
                    "https://www.instagram.com/reel/testcode/",
                    "testcode",
                    session_id="sess1",
                )
                assert res.fetch_status == FetchStatus.MEDIA_UNAVAILABLE
                assert res.failure_category == FailureCategory.MEDIA_DOWNLOAD_FAILED
                assert "exceeds limit" in res.error_message
                # File must be deleted
                assert not dummy_file.exists()
    finally:
        if dummy_file.exists():
            dummy_file.unlink()


def test_cleanup_temp_media_removes_target_files():
    """Temporary file cleanup reliably removes files matching target prefix."""
    file1 = settings.temp_media_dir / "prefix123_abc.mp4"
    file2 = settings.temp_media_dir / "other456_xyz.mp4"
    file1.write_text("sample1")
    file2.write_text("sample2")

    try:
        cleanup_temp_media("prefix123")
        assert not file1.exists()
        assert file2.exists()
    finally:
        cleanup_temp_media("other456")


# ==============================================================================
# Gemini AI Timeout & Output Tests
# ==============================================================================


def test_gemini_timeout_handling():
    """When Gemini call exceeds deadline, it returns TIMEOUT failure without crashing."""
    processor = GeminiProcessor(api_key="mock-key")

    async def _test():
        with patch.object(settings, "gemini_timeout_seconds", 1):
            with patch.object(processor, "_sync_analyze") as mock_sync:
                import time

                def _slow_analyze(*args, **kwargs):
                    time.sleep(2)
                    return None

                mock_sync.side_effect = _slow_analyze
                res = await processor.analyze(None, "Sample caption", "shortcode123")
                assert res.success is False
                assert res.failure_category == FailureCategory.TIMEOUT
                assert res.estimated_cost_usd == 0.0

    asyncio.run(_test())


# ==============================================================================
# Metrics & Retry Integrity Tests
# ==============================================================================


def test_retries_do_not_inflate_unique_reels():
    """Retrying the same reel increases total attempts but preserves unique reel count."""
    r1 = ExperimentResult(
        experiment_id="exp_short1_1",
        shortcode="short1",
        original_url="https://www.instagram.com/reel/short1/",
        normalized_url="https://www.instagram.com/reel/short1/",
        path_type="reel",
        attempt_timestamp="2026-11-03T10:00:00Z",
        ingestion_provider="server_ytdlp",
        fetch_status=FetchStatus.LOGIN_WALL,
        failure_category=FailureCategory.LOGIN_WALL,
        retry_count=0,
    )
    r2 = ExperimentResult(
        experiment_id="exp_short1_2",
        shortcode="short1",
        original_url="https://www.instagram.com/reel/short1/",
        normalized_url="https://www.instagram.com/reel/short1/",
        path_type="reel",
        attempt_timestamp="2026-11-03T10:05:00Z",
        ingestion_provider="oembed",
        fetch_status=FetchStatus.SUCCESS_CAPTION_ONLY,
        caption_only_fallback_used=True,
        caption_success=True,
        ai_processing_success=True,
        output_validation_success=True,
        end_to_end_success=True,
        retry_count=1,
    )

    metrics = compute_metrics([r1, r2])
    assert metrics.total_attempts == 2
    assert metrics.unique_reels_processed == 1  # Not inflated to 2!
    assert metrics.end_to_end_processing_success_rate == 0.5
    assert metrics.media_retrieval_success_rate == 0.0  # Caption fallback is NOT media success!



def test_export_endpoint_requires_auth_in_production(client):
    """Export endpoint requires API key authentication in production."""
    with patch.object(settings, "environment", "production"):
        with patch.object(settings, "spike_api_key", "prod-secret-key"):
            # Unauthenticated fails
            res = client.get("/spike/export")
            assert res.status_code == 401

            # Authenticated succeeds
            res_auth = client.get("/spike/export", headers={"X-Spike-Key": "prod-secret-key"})
            assert res_auth.status_code == 200
            assert "total_records" in res_auth.json()


def test_synthetic_negative_control_never_counted_as_unique_reel():
    """Synthetic negative control records are strictly excluded from unique Reel count."""
    r_synthetic = ExperimentResult(
        experiment_id="exp_synth_1",
        shortcode="C12345Sample",
        original_url="https://www.instagram.com/reel/C12345Sample/",
        normalized_url="https://www.instagram.com/reel/C12345Sample/",
        path_type="reel",
        attempt_timestamp="2026-11-03T10:00:00Z",
        ingestion_provider="server_ytdlp",
        fetch_status=FetchStatus.POST_NOT_FOUND,
        failure_category=FailureCategory.POST_NOT_FOUND,
        retry_count=0,
    )
    metrics = compute_metrics([r_synthetic])
    assert metrics.total_attempts == 1
    # Must NOT be counted as a unique real Reel!
    assert metrics.unique_reels_processed == 0
    assert metrics.end_to_end_processing_success_rate == 0.0
