import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.spike.config import settings
from app.spike.main import app
from app.spike.schemas import ReelAnalysisOutput
from app.vault.models import VaultReel, VaultStatus
from app.vault.database import (
    queue_reel,
    get_reel_by_shortcode,
    complete_reel,
    reanalyze_reel,
    reanalyze_unavailable_reels,
    init_vault_db,
    fail_reel,
)
from app.vault.semantic import init_embeddings_db
from app.vault.ui import render_reel_detail_page


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_adaptive_vault.db"
    monkeypatch.setenv("VAULT_AUTH_TOKEN", "test-secret-token")
    monkeypatch.setenv("ENABLE_HISTORICAL_SEED", "false")
    with patch.object(settings, "sqlite_db_file", test_db):
        init_vault_db()
        init_embeddings_db()
        yield test_db


def test_models_adaptive_fields():
    reel = VaultReel(
        shortcode="TEST1",
        original_url="https://www.instagram.com/reel/TEST1/",
        normalized_url="https://www.instagram.com/reel/TEST1/",
        created_at="2026-11-07T10:00:00Z",
        updated_at="2026-11-07T10:00:00Z",
        analysis_mode="video",
        is_thin_extraction=True,
    )
    assert reel.analysis_mode == "video"
    assert reel.is_thin_extraction is True


def test_reanalyze_single_reel():
    sc = "TEST_REANALYZE"
    url = f"https://www.instagram.com/reel/{sc}/"
    queue_reel(url, url, sc)

    analysis = ReelAnalysisOutput(
        title="Old Title",
        category="Tech",
        tags=["tech"],
        short_summary="Old summary",
        takeaway="Old takeaway",
        key_points=["Point 1"],
        tools_and_resources=[],
        extracted_urls=[],
        commands_or_code_snippets=[],
        transcript="Speech transcript here",
        on_screen_text="",
        original_language="en",
        english_summary="Old summary",
        confidence=1.0,
    )
    complete_reel(
        shortcode=sc,
        analysis=analysis,
        analysis_mode="audio",
        is_thin_extraction=False,
    )

    updated = reanalyze_reel(sc, force_video=True)
    assert updated is not None
    assert updated.status == VaultStatus.QUEUED.value
    assert updated.analysis_mode == "video"


def test_reanalyze_unavailable_reels():
    u1 = "https://www.instagram.com/reel/UNAVAIL_1/"
    queue_reel(u1, u1, "UNAVAIL_1")
    fail_reel("UNAVAIL_1", VaultStatus.UNAVAILABLE.value, "Could not fetch", "download")

    u2 = "https://www.instagram.com/reel/UNAVAIL_2/"
    queue_reel(u2, u2, "UNAVAIL_2")
    fail_reel("UNAVAIL_2", VaultStatus.FAILED.value, "Error occurred", "analysis")

    d1 = "https://www.instagram.com/reel/DONE_1/"
    queue_reel(d1, d1, "DONE_1")
    analysis = ReelAnalysisOutput(
        title="Done 1",
        category="Tech",
        tags=["done"],
        short_summary="Great summary",
        takeaway="Great takeaway",
        key_points=["P1"],
        tools_and_resources=[],
        extracted_urls=[],
        commands_or_code_snippets=[],
        transcript="Words here",
        on_screen_text="",
        original_language="en",
        english_summary="Great summary",
        confidence=1.0,
    )
    complete_reel(
        shortcode="DONE_1",
        analysis=analysis,
        analysis_mode="audio",
        is_thin_extraction=False,
    )

    res = reanalyze_unavailable_reels()
    assert res["count"] == 2
    assert "UNAVAIL_1" in res["shortcodes"]
    assert "UNAVAIL_2" in res["shortcodes"]

    updated_r1 = get_reel_by_shortcode("UNAVAIL_1")
    assert updated_r1.status == VaultStatus.QUEUED.value
    assert updated_r1.analysis_mode == "video"

    done_r = get_reel_by_shortcode("DONE_1")
    assert done_r.status == VaultStatus.DONE.value


def test_complete_reel_with_video_mode_and_thin():
    sc = "VIDEO_THIN"
    url = f"https://www.instagram.com/reel/{sc}/"
    queue_reel(url, url, sc)

    analysis = ReelAnalysisOutput(
        title="Video Extracted Reel",
        category="Tech",
        tags=["tech"],
        short_summary="Short summary",
        takeaway="Learned visually",
        key_points=[],
        tools_and_resources=[],
        extracted_urls=[],
        commands_or_code_snippets=[],
        transcript="",
        on_screen_text="Site link here",
        original_language="en",
        english_summary="Short summary",
        confidence=0.8,
    )
    complete_reel(
        shortcode=sc,
        analysis=analysis,
        analysis_mode="video",
        is_thin_extraction=True,
    )

    reel = get_reel_by_shortcode(sc)
    assert reel.analysis_mode == "video"
    assert reel.is_thin_extraction is True
    assert reel.on_screen_text == "Site link here"


def test_reanalyze_api_endpoint():
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-secret-token"}

    sc = "API_REANALYZE"
    url = f"https://www.instagram.com/reel/{sc}/"
    queue_reel(url, url, sc)

    with patch("app.vault.worker.trigger_immediate_reanalysis", new_callable=AsyncMock) as mock_trigger:
        resp = client.post(f"/api/vault/reels/{sc}/reanalyze?force_video=true", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        assert sc in data["message"]
        mock_trigger.assert_called_once_with(sc, force_video=True)


def test_reanalyze_unavailable_api_endpoint():
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-secret-token"}

    sc = "API_UNAVAIL"
    url = f"https://www.instagram.com/reel/{sc}/"
    queue_reel(url, url, sc)
    fail_reel(sc, VaultStatus.UNAVAILABLE.value, "Not found", "download")

    resp = client.post("/api/vault/reanalyze-unavailable", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["count"] == 1
    assert sc in data["shortcodes"]


def test_ui_detail_page_renders_video_badge_and_reanalyze_button():
    reel = VaultReel(
        shortcode="UI_TEST",
        original_url="https://www.instagram.com/reel/UI_TEST/",
        normalized_url="https://www.instagram.com/reel/UI_TEST/",
        created_at="2026-11-07T10:00:00Z",
        updated_at="2026-11-07T10:00:00Z",
        status=VaultStatus.DONE.value,
        title="UI Test Reel",
        analysis_mode="video",
        is_thin_extraction=True,
    )

    html = render_reel_detail_page(reel)
    assert "🎬 Video" in html
    assert "⚠️ Limited Extraction" in html
    assert "Re-analyze (Video)" in html
