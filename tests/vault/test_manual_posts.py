"""Integration tests for Instagram Posts (/p/ and /tv/) manual entries without analysis."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.spike.config import settings
from app.spike.main import app
from app.vault.database import (
    get_next_queued_reel,
    get_reel_by_shortcode,
    init_vault_db,
    search_library,
)
from app.vault.models import VaultReel, VaultStatus


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Use a temporary database for each test to ensure test isolation."""
    temp_db = tmp_path / "test_manual_posts.db"
    with patch.object(settings, "sqlite_db_file", temp_db), \
         patch.object(settings, "analyze_posts", False):
        init_vault_db()
        yield temp_db


@pytest.fixture
def client():
    return TestClient(app)


def test_manual_post_saving_instant_no_analysis(client):
    """Verifies that /p/ and /tv/ links are saved immediately as manual entries without analysis."""
    post_url = "https://www.instagram.com/p/TEST_POST_01/?dlrf=123"

    with patch("app.vault.worker.pipeline.ingest") as mock_ingest, \
         patch("app.vault.worker.ai_engine.analyze") as mock_ai:
        res = client.post("/api/vault/reels", json={"url": post_url})
        assert res.status_code == 200
        data = res.json()
        assert data["is_new"] is True
        assert data["reel"]["shortcode"] == "TEST_POST_01"
        assert data["reel"]["status"] == VaultStatus.SAVED.value
        assert data["reel"]["analysis_mode"] == "not_analyzed"
        assert data["reel"]["takeaway"] == ""

        # Verify background worker does NOT queue or pick up this entry
        next_queued = get_next_queued_reel()
        assert next_queued is None

        # Verify 0 ingestion/Gemini calls
        assert mock_ingest.call_count == 0
        assert mock_ai.call_count == 0


def test_share_target_redirects_with_open_quick_note(client):
    """Verifies Android share target redirect includes open_quick_note=1 for posts."""
    post_url = "https://www.instagram.com/p/TEST_POST_02/?psln=abc"
    res = client.get(
        "/share-target",
        params={"text": f"Found this post: {post_url}"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    loc = res.headers["location"]
    assert "shared=TEST_POST_02" in loc
    assert "open_quick_note=1" in loc

    saved = get_reel_by_shortcode("TEST_POST_02")
    assert saved is not None
    assert saved.status == VaultStatus.SAVED.value
    assert saved.analysis_mode == "not_analyzed"


def test_patch_update_details_and_search_by_note(client):
    """Tests editing title, note, category, and tags via PATCH endpoint and search discovery."""
    shortcode = "TEST_POST_03"
    client.post("/api/vault/reels", json={"url": f"https://www.instagram.com/p/{shortcode}/"})

    # Update post details with custom user note
    update_payload = {
        "title": "Vibe Coder Color Palette Tools",
        "note": "Use Realtime Colors and Godly to test production palette contrast and themes",
        "category": "Design & UI",
        "tags": ["colors", "palette", "vibecoding"],
    }
    patch_res = client.patch(f"/api/vault/reels/{shortcode}", json=update_payload)
    assert patch_res.status_code == 200
    updated = patch_res.json()
    assert updated["title"] == "Vibe Coder Color Palette Tools"
    assert "Realtime Colors" in updated["takeaway"]
    assert updated["category"] == "Design & UI"
    assert "vibecoding" in updated["tags"]

    # Keyword search for unique word from note
    matches, total = search_library(query="Realtime")
    assert total >= 1
    assert any(m.shortcode == shortcode for m in matches)

    # Keyword search for tag
    tag_matches, tag_total = search_library(query="vibecoding")
    assert tag_total >= 1
    assert any(m.shortcode == shortcode for m in tag_matches)


def test_filters_by_type_and_needs_note(client):
    """Verifies type filtering (All / Reels / Posts) and needs_note filter."""
    # 1. Add a reel
    client.post("/api/vault/reels", json={"url": "https://www.instagram.com/reel/REEL_TEST_01/"})

    # 2. Add post 1 without note (needs note)
    client.post("/api/vault/reels", json={"url": "https://www.instagram.com/p/POST_NO_NOTE/"})

    # 3. Add post 2 with note
    client.post("/api/vault/reels", json={"url": "https://www.instagram.com/p/POST_WITH_NOTE/"})
    client.patch("/api/vault/reels/POST_WITH_NOTE", json={"note": "Configured OAuth 2.0 with Auth0"})

    # Filter: type="posts"
    posts_res = client.get("/api/vault/reels", params={"type": "posts"})
    assert posts_res.status_code == 200
    p_data = posts_res.json()
    assert all(r["path_type"] in ("p", "tv", "post") for r in p_data["reels"])
    assert any(r["shortcode"] == "POST_NO_NOTE" for r in p_data["reels"])
    assert any(r["shortcode"] == "POST_WITH_NOTE" for r in p_data["reels"])
    assert not any(r["shortcode"] == "REEL_TEST_01" for r in p_data["reels"])

    # Filter: type="reels"
    reels_res = client.get("/api/vault/reels", params={"type": "reels"})
    assert reels_res.status_code == 200
    r_data = reels_res.json()
    assert all(r["path_type"] not in ("p", "tv", "post") for r in r_data["reels"])
    assert any(r["shortcode"] == "REEL_TEST_01" for r in r_data["reels"])

    # Filter: needs_note=True
    needs_note_res = client.get("/api/vault/reels", params={"needs_note": "true"})
    assert needs_note_res.status_code == 200
    nn_data = needs_note_res.json()
    assert any(r["shortcode"] == "POST_NO_NOTE" for r in nn_data["reels"])
    assert not any(r["shortcode"] == "POST_WITH_NOTE" for r in nn_data["reels"])


def test_ui_contains_post_badges_and_quick_form():
    """Verifies that ui.py contains Post badge, Not analyzed label, and quick note modal."""
    ui_html = Path("app/vault/ui.py").read_text(encoding="utf-8")
    assert "quickNoteModal" in ui_html
    assert "📌 Post" in ui_html
    assert "Not analyzed" in ui_html
    assert "openQuickNoteModal" in ui_html
    assert "Needs note" in ui_html
    assert "saveReelEdits" in ui_html

