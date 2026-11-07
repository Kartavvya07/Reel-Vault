"""Tests for Slice 6: Reliability and Everyday Workflow."""

from pathlib import Path
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from app.spike.config import settings
from app.spike.main import app
from app.spike.schemas import ReelAnalysisOutput, ToolResource, ExtractedUrl
from app.vault.database import (
    complete_reel,
    delete_reel,
    fail_reel,
    get_db_connection,
    get_reel_by_shortcode,
    init_vault_db,
    queue_reel,
    retry_reel,
    sanitize_error_message,
    set_reel_processing,
)
from app.vault.semantic import index_reel, init_embeddings_db, search_natural_language
from app.vault.ui import render_reel_detail_page


@pytest.fixture(autouse=True)
def setup_test_vault_db(tmp_path):
    """Isolates each test in a clean SQLite database with vault & embedding schemas."""
    test_db = tmp_path / "test_reliability_vault.db"
    with patch.object(settings, "sqlite_db_file", test_db):
        init_vault_db()
        init_embeddings_db()
        yield test_db


@pytest.fixture
def client():
    return TestClient(app)


def test_complete_reel_lifecycle_and_retry():
    """Verifies complete lifecycle: queued -> processing -> failed -> retry -> queued -> processing -> done."""
    sc = "sc_lifecycle_123"
    url = f"https://www.instagram.com/reel/{sc}/"

    # 1. Submission / Queue
    reel, is_new, msg = queue_reel(url, url, sc)
    assert is_new is True
    assert reel.status == "queued"
    assert reel.retry_count == 0

    # 2. Processing
    set_reel_processing(sc)
    reel_proc = get_reel_by_shortcode(sc)
    assert reel_proc.status == "processing"

    # 3. Failure with sanitization
    raw_error = "Traceback (most recent call last):\n  File 'worker.py', line 45\nRuntimeError: Connection to AIzaSyABC1234567890123456789012345 failed."
    fail_reel(sc, "failed", raw_error, "NETWORK_ERROR")
    reel_failed = get_reel_by_shortcode(sc)
    assert reel_failed.status == "failed"
    assert "AIzaSy" not in reel_failed.error_message
    assert "[REDACTED_KEY]" in reel_failed.error_message
    assert "Traceback" not in reel_failed.error_message

    # 4. Retry
    retried = retry_reel(sc)
    assert retried.status == "queued"
    assert retried.retry_count == 1
    assert retried.error_message is None

    # 5. Second processing & Done
    set_reel_processing(sc)
    analysis = ReelAnalysisOutput(
        title="TypeScript 5.5 Features Breakdown",
        category="Software Development",
        short_summary="A summary of inferred type predicates in TS 5.5.",
        english_summary="A summary of inferred type predicates in TS 5.5.",
        takeaway="Use inferred type predicates to simplify array filter functions.",
        key_points=["Inferred type predicates in Array.filter"],
        tools_and_resources=[ToolResource(name="TypeScript", kind="language")],
        tags=["typescript", "javascript"],
        original_language="en",
        transcript="TypeScript 5.5 now automatically narrows types when filtering arrays.",
        on_screen_text="TypeScript 5.5 Inferred Predicates",
    )
    complete_reel(sc, analysis)
    reel_done = get_reel_by_shortcode(sc)
    assert reel_done.status == "done"
    assert reel_done.title == "TypeScript 5.5 Features Breakdown"
    assert reel_done.processed_at is not None


def test_single_entry_deletion_and_search_cleanup():
    """Verifies that delete_reel safely purges the record from database, FTS5 index, and embeddings."""
    sc = "sc_to_delete"
    sc_keeper = "sc_to_keep"
    url = f"https://www.instagram.com/reel/{sc}/"
    url_keeper = f"https://www.instagram.com/reel/{sc_keeper}/"

    # Create target reel
    queue_reel(url, url, sc)
    complete_reel(
        sc,
        ReelAnalysisOutput(
            title="Temporary Reel on Rust Web Frameworks",
            category="Tech",
            short_summary="A temporary reel.",
            english_summary="A temporary reel.",
            takeaway="Axum provides ergonomic routing built on Tower services.",
            key_points=["Axum routing"],
            tools_and_resources=[ToolResource(name="Axum", kind="framework")],
            tags=["rust", "axum"],
            transcript="Axum is currently the most popular Rust web framework.",
            on_screen_text="Rust Axum Tutorial",
        ),
    )
    index_reel(sc, force=True)

    # Create keeper reel
    queue_reel(url_keeper, url_keeper, sc_keeper)
    complete_reel(
        sc_keeper,
        ReelAnalysisOutput(
            title="Permanent Reel on Rust Tokio Async",
            category="Tech",
            short_summary="A permanent reel.",
            english_summary="A permanent reel.",
            takeaway="Tokio runtime powers asynchronous Rust servers.",
            key_points=["Tokio runtime"],
            tools_and_resources=[ToolResource(name="Tokio", kind="library")],
            tags=["rust", "tokio"],
            transcript="Tokio is an asynchronous runtime for the Rust programming language.",
            on_screen_text="Tokio Async Rust",
        ),
    )
    index_reel(sc_keeper, force=True)

    # Search initially finds both
    res_before, total_before = search_natural_language(query="Rust")
    assert any(r.shortcode == sc for r in res_before)
    assert any(r.shortcode == sc_keeper for r in res_before)

    # 1. Delete target reel
    deleted = delete_reel(sc)
    assert deleted is True

    # 2. Verify target reel is gone from library_reels
    assert get_reel_by_shortcode(sc) is None
    # Keeper reel remains intact (no bulk deletion)
    assert get_reel_by_shortcode(sc_keeper) is not None

    # 3. Verify target reel is gone from FTS5 index
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT count(*) FROM library_reels_fts WHERE shortcode = ?", (sc,))
    assert c.fetchone()[0] == 0
    c.execute("SELECT count(*) FROM library_reels_fts WHERE shortcode = ?", (sc_keeper,))
    assert c.fetchone()[0] == 1

    # 4. Verify target reel is gone from reel_embeddings
    c.execute("SELECT count(*) FROM reel_embeddings WHERE shortcode = ?", (sc,))
    assert c.fetchone()[0] == 0
    c.execute("SELECT count(*) FROM reel_embeddings WHERE shortcode = ?", (sc_keeper,))
    assert c.fetchone()[0] == 1
    conn.close()

    # 5. Search only finds keeper reel
    res_after, _ = search_natural_language(query="Rust")
    assert not any(r.shortcode == sc for r in res_after)
    assert any(r.shortcode == sc_keeper for r in res_after)

    # 6. Deleting non-existent reel returns False
    assert delete_reel("non_existent_shortcode") is False


def test_delete_api_endpoint_auth_and_responses(client):
    """Tests DELETE /api/vault/reels/{shortcode} authentication and status codes."""
    token = "vault_delete_secret"
    sc = "sc_api_del_test"
    url = f"https://www.instagram.com/reel/{sc}/"
    queue_reel(url, url, sc)

    with patch.object(settings, "vault_auth_token", token):
        # 1. Unauthenticated delete fails with 401
        res_unauth = client.delete(f"/api/vault/reels/{sc}")
        assert res_unauth.status_code == 401

        # 2. Non-existent shortcode with auth fails with 404
        res_404 = client.delete(
            "/api/vault/reels/sc_ghost_123",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res_404.status_code == 404

        # 3. Valid authenticated delete succeeds with 200
        res_ok = client.delete(
            f"/api/vault/reels/{sc}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res_ok.status_code == 200
        data = res_ok.json()
        assert data["status"] == "deleted"
        assert data["shortcode"] == sc
        assert get_reel_by_shortcode(sc) is None


def test_duplicate_submission_feedback_and_preservation(client):
    """Verifies duplicate submission returns existing reel without creating duplicate rows or overwriting data."""
    sc = "sc_duplicate_test"
    url = f"https://www.instagram.com/reel/{sc}/"

    # First submission
    reel1, is_new1, msg1 = queue_reel(url, url, sc)
    assert is_new1 is True

    # Complete the reel with rich analysis
    complete_reel(
        sc,
        ReelAnalysisOutput(
            title="First Rich Analysis",
            category="Education",
            short_summary="Original summary.",
            english_summary="Original summary.",
            takeaway="Important takeaway preserved.",
            key_points=["Point 1"],
            tools_and_resources=[],
            tags=["edu"],
            transcript="Original transcript.",
            on_screen_text="Original text.",
        ),
    )

    # Re-submitting the same URL via API
    token = "dup_secret_123"
    with patch.object(settings, "vault_auth_token", token):
        res = client.post(
            "/api/vault/reels",
            json={"url": url},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["is_new"] is False
        assert "already saved" in data["message"].lower()
        assert data["reel"]["shortcode"] == sc
        assert data["reel"]["title"] == "First Rich Analysis"

    # Verify no duplicate rows in SQLite
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT count(*) FROM library_reels WHERE shortcode = ?", (sc,))
    assert c.fetchone()[0] == 1
    conn.close()


def test_share_target_error_and_duplicate_handling(client):
    """Tests share-target error redirection for invalid payloads and duplicate sharing."""
    # 1. Missing or malformed Instagram URL
    res_bad = client.post(
        "/share-target",
        data={"title": "Random text without URL", "text": "Just chatting with friends"},
        follow_redirects=False,
    )
    assert res_bad.status_code == 303
    assert "share_error" in res_bad.headers["Location"]

    # 2. Valid URL shared when unauthenticated redirects to login with encoded destination
    token = "share_auth_token"
    test_url = "https://www.instagram.com/reel/CwSharedReel123/"
    with patch.object(settings, "vault_auth_token", token):
        res_unauth = client.post(
            "/share-target",
            data={"url": test_url},
            follow_redirects=False,
        )
        assert res_unauth.status_code == 303
        loc = res_unauth.headers["Location"]
        assert loc.startswith("/login?redirect=")
        assert "share-target" in loc

    # 3. Duplicate share returns is_new=0
    queue_reel(test_url, test_url, "CwSharedReel123")
    res_dup = client.post(
        "/share-target",
        data={"url": test_url},
        follow_redirects=False,
    )
    assert res_dup.status_code == 303
    loc = res_dup.headers["Location"]
    assert "is_new=0" in loc
    assert "shared=CwSharedReel123" in loc


def test_detail_page_copy_actions_and_sanitization():
    """Verifies detail page exposes copy buttons and distinguishes verified from unverified links."""
    sc = "sc_detail_copy_actions"
    url = f"https://www.instagram.com/reel/{sc}/"
    queue_reel(url, url, sc)
    complete_reel(
        sc,
        ReelAnalysisOutput(
            title="Fast API Deployment on Railway",
            category="DevOps",
            short_summary="A tutorial on Dockerizing FastAPI.",
            english_summary="A tutorial on Dockerizing FastAPI.",
            takeaway="Use multi-stage Docker builds to keep container images under 100MB.",
            key_points=["Slim python base image"],
            tools_and_resources=[
                ToolResource(name="Railway CLI", url="https://docs.railway.app", verification_status="verified"),
                ToolResource(name="Custom Script", url="http://local.dev/script", verification_status="unverified"),
            ],
            extracted_urls=[
                ExtractedUrl(url="https://github.com/fastapi/fastapi", source="caption"),
            ],
            tags=["fastapi", "docker", "railway"],
            transcript="Deploying to Railway takes just one CLI command.",
            on_screen_text="Railway Up",
            confidence=0.98,
        ),
    )
    reel = get_reel_by_shortcode(sc)
    html_page = render_reel_detail_page(reel)

    # 1. Instagram URL copy button
    assert "Copy URL" in html_page
    assert html_page.count("Copy URL") >= 1

    # 2. Link copy actions for tools and extracted URLs
    assert "Copy" in html_page

    # 3. Verified vs unverified badges
    assert "✓ Verified" in html_page
    assert "Extracted (Unverified)" in html_page
    assert "Extracted External Links (Unverified)" in html_page

    # 4. Delete action present
    assert "Delete Reel" in html_page

