"""Tests for Instagram Photo and Carousel Posts (/p/ and /tv/) Ingestion and Multimodal Analysis."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch
from PIL import Image
import pytest

from app.spike.ai_processor import GeminiProcessor
from app.spike.config import settings
from app.spike.ingestion import CompositeIngestionPipeline, GalleryDlAdapter, IngestionResult
from app.spike.schemas import FailureCategory, FetchStatus, ReelAnalysisOutput, ToolResource
from app.spike.url_parser import parse_and_normalize_url
from app.vault.database import get_db_connection, get_reel_by_shortcode, init_vault_db
from app.vault.models import VaultReel, VaultStatus
from app.vault.pwa import extract_instagram_url_from_share
from app.vault.worker import process_single_vault_reel


@pytest.fixture
def test_env(tmp_path):
    """Sets up an isolated test database and temp media directory."""
    db_file = tmp_path / "test_vault.db"
    media_dir = tmp_path / "temp_media"
    media_dir.mkdir(parents=True, exist_ok=True)

    old_db = settings.sqlite_db_file
    old_media = settings.temp_media_dir
    settings.sqlite_db_file = db_file
    settings.temp_media_dir = media_dir

    init_vault_db()

    yield {
        "db_file": db_file,
        "media_dir": media_dir,
    }

    settings.sqlite_db_file = old_db
    settings.temp_media_dir = old_media


def test_url_parser_p_and_tv_formats():
    """Verifies that /p/ and /tv/ URLs are correctly parsed and normalized."""
    u1 = "https://www.instagram.com/p/TEST_POST_01/?dlrf=MzRlODBiNWFlZA==&srtk=MzRlODBiNWFlZA=="
    norm1, sc1, path1 = parse_and_normalize_url(u1)
    assert sc1 == "TEST_POST_01"
    assert path1 == "p"
    assert norm1 == "https://www.instagram.com/p/TEST_POST_01/"

    u2 = "https://www.instagram.com/tv/C1abcXYZ999/?igsh=MWxyz"
    norm2, sc2, path2 = parse_and_normalize_url(u2)
    assert sc2 == "C1abcXYZ999"
    assert path2 == "tv"
    assert norm2 == "https://www.instagram.com/tv/C1abcXYZ999/"


def test_share_target_extracts_p_and_tv():
    """Ensures Android share sheet extractor accepts /p/ and /tv/ posts."""
    post_text = "Check out this visual carousel: https://www.instagram.com/p/TEST_POST_03/?srtk=123"
    assert extract_instagram_url_from_share(text=post_text) == "https://www.instagram.com/p/TEST_POST_03/?srtk=123"

    tv_text = "IGTV tutorial: https://www.instagram.com/tv/TEST_POST_02/ on design tools."
    assert extract_instagram_url_from_share(text=tv_text) == "https://www.instagram.com/tv/TEST_POST_02/"


def test_gallery_dl_adapter_login_wall_detection(test_env):
    """Tests that login redirects are mapped to FetchStatus.LOGIN_WALL with clear message."""
    adapter = GalleryDlAdapter()

    fake_res = MagicMock()
    fake_res.returncode = 4
    fake_res.stderr = "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)"
    fake_res.stdout = ""

    with patch("subprocess.run", return_value=fake_res):
        res = adapter._sync_download(
            url="https://www.instagram.com/p/TEST_POST_01/",
            shortcode="TEST_POST_01",
            session_id="test1",
            max_images=10,
        )

    assert res.fetch_status == FetchStatus.LOGIN_WALL
    assert res.failure_category == FailureCategory.LOGIN_WALL
    assert res.error_message == "unavailable: login required"


def test_gallery_dl_adapter_image_resizing_and_limiting(test_env):
    """Tests that gallery-dl downloads are limited to 10 images and resized to max 1024px."""
    adapter = GalleryDlAdapter()
    target_dir = test_env["media_dir"] / "gdl_TESTIMG_sess1"
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create 12 large mock images (2000x1500)
    for i in range(12):
        img = Image.new("RGB", (2000, 1500), color=(i * 20, 100, 150))
        img.save(target_dir / f"img_{i:02d}.jpg", "JPEG")

    # Metadata sidecar
    meta = {"description": "Great new design tools for developers: Tailwind CSS and Lucide Icons"}
    with open(target_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)

    fake_res = MagicMock()
    fake_res.returncode = 0
    fake_res.stderr = ""
    fake_res.stdout = ""

    with patch("subprocess.run", return_value=fake_res):
        res = adapter._sync_download(
            url="https://www.instagram.com/p/TESTIMG/",
            shortcode="TESTIMG",
            session_id="sess1",
            max_images=10,
        )

    assert res.fetch_status == FetchStatus.SUCCESS_FULL
    assert len(res.image_paths) == 10
    assert "Tailwind CSS" in res.caption

    # Check resizing of the first image: longest dimension must be <= 1024px
    with Image.open(res.image_paths[0]) as im:
        w, h = im.size
        assert max(w, h) <= 1024
        assert max(w, h) == 1024


def test_gallery_dl_adapter_detects_video_in_carousel(test_env):
    """Tests that if gallery-dl extracts a video from a post/carousel, it uses the video path."""
    adapter = GalleryDlAdapter()
    target_dir = test_env["media_dir"] / "gdl_TESTVID_sess2"
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create a dummy video file
    dummy_video = target_dir / "carousel_video.mp4"
    dummy_video.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 200)

    # Create an image file
    img = Image.new("RGB", (500, 500), color="blue")
    img.save(target_dir / "slide1.jpg", "JPEG")

    fake_res = MagicMock()
    fake_res.returncode = 0
    fake_res.stderr = ""
    fake_res.stdout = ""

    with patch("subprocess.run", return_value=fake_res):
        res = adapter._sync_download(
            url="https://www.instagram.com/p/TESTVID/",
            shortcode="TESTVID",
            session_id="sess2",
            max_images=10,
        )

    assert res.fetch_status == FetchStatus.SUCCESS_FULL
    assert res.media_path is not None
    assert res.media_path.endswith(".mp4")
    assert res.image_paths == []


def test_worker_processes_image_post_successfully(test_env):
    """Tests full worker execution for an image post with multimodal Gemini extraction."""
    shortcode = "P_PHOTO_123"
    norm_url = f"https://www.instagram.com/p/{shortcode}/"
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO library_reels (shortcode, original_url, normalized_url, path_type, status, created_at, updated_at)
        VALUES (?, ?, ?, 'p', 'queued', ?, ?)
        """,
        (shortcode, norm_url, norm_url, now_iso, now_iso),
    )
    conn.commit()
    conn.close()

    reel = VaultReel(
        shortcode=shortcode,
        original_url=norm_url,
        normalized_url=norm_url,
        path_type="p",
        status=VaultStatus.QUEUED.value,
        created_at=now_iso,
        updated_at=now_iso,
    )

    mock_ingest = IngestionResult(
        provider="gallery_dl",
        fetch_status=FetchStatus.SUCCESS_FULL,
        image_paths=["/tmp/fake_img1.jpg", "/tmp/fake_img2.jpg"],
        caption="Top 3 tools for frontend design",
    )

    mock_analysis_output = ReelAnalysisOutput(
        title="Top Frontend Design Tools",
        category="Front-End, UI/UX & Web Development",
        tags=["design", "frontend", "tools"],
        short_summary="A carousel showcasing the top 3 modern frontend libraries.",
        takeaway="Use modern lightweight UI component libraries.",
        key_points=["Point 1", "Point 2"],
        tools_and_resources=[
            ToolResource(
                name="Lucide Icons",
                type="library",
                url="https://lucide.dev",
                source="on_screen_text",
                verification_status="verified",
            )
        ],
        transcript="",
        on_screen_text="Lucide Icons: Beautiful & consistent icon toolkit",
        original_language="en",
        english_summary="Top 3 frontend design tools summary.",
        confidence=0.95,
    )

    from app.spike.ai_processor import AIAnalysisResult
    mock_ai_res = AIAnalysisResult(
        success=True,
        output=mock_analysis_output,
        input_tokens=150,
        output_tokens=300,
        estimated_cost_usd=0.0001,
    )

    with patch.object(settings, "enable_real_pipeline", True), \
         patch.object(settings, "gemini_api_key", "fake-key"), \
         patch.object(settings, "analyze_posts", True), \
         patch("app.vault.worker.pipeline.ingest", return_value=(mock_ingest, False)), \
         patch("app.vault.worker.ai_engine.analyze", return_value=mock_ai_res), \
         patch("app.vault.worker.cleanup_temp_media") as mock_cleanup:
        asyncio.run(process_single_vault_reel(reel))

    saved = get_reel_by_shortcode(shortcode)
    assert saved.status == VaultStatus.DONE.value
    assert saved.analysis_mode == "images"
    assert saved.is_thin_extraction is False
    assert len(saved.tools) == 1
    assert saved.tools[0].verification_status == "unverified"
    assert mock_cleanup.called


def test_worker_login_wall_marks_unavailable(test_env):
    """Tests that if Instagram requires login, reel is marked unavailable with clear message."""
    shortcode = "P_LOGIN_WALL"
    norm_url = f"https://www.instagram.com/p/{shortcode}/"
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO library_reels (shortcode, original_url, normalized_url, path_type, status, created_at, updated_at)
        VALUES (?, ?, ?, 'p', 'queued', ?, ?)
        """,
        (shortcode, norm_url, norm_url, now_iso, now_iso),
    )
    conn.commit()
    conn.close()

    reel = VaultReel(
        shortcode=shortcode,
        original_url=norm_url,
        normalized_url=norm_url,
        path_type="p",
        status=VaultStatus.QUEUED.value,
        created_at=now_iso,
        updated_at=now_iso,
    )

    mock_ingest = IngestionResult(
        provider="gallery_dl",
        fetch_status=FetchStatus.LOGIN_WALL,
        failure_category=FailureCategory.LOGIN_WALL,
        error_message="unavailable: login required",
    )

    with patch.object(settings, "enable_real_pipeline", True), \
         patch.object(settings, "gemini_api_key", "fake-key"), \
         patch.object(settings, "analyze_posts", True), \
         patch("app.vault.worker.pipeline.ingest", return_value=(mock_ingest, False)):
        asyncio.run(process_single_vault_reel(reel))

    saved = get_reel_by_shortcode(shortcode)
    assert saved.status == VaultStatus.UNAVAILABLE.value
    assert saved.error_message == "unavailable: login required"
    assert saved.failure_category == "LOGIN_WALL"


def test_ui_contains_images_badge():
    """Verifies that ui.py includes the 🖼️ Images badge template."""
    ui_code = Path("app/vault/ui.py").read_text(encoding="utf-8")
    assert "reel.analysis_mode === 'images'" in ui_code
    assert "🖼️ Images" in ui_code
    assert "reel.analysis_mode == \"images\"" in ui_code
