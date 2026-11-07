"""Unit and integration tests for Reel Vault — Slice 1 Personal Knowledge Library."""

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from unittest.mock import patch

from app.spike.config import settings
from app.spike.main import app
from app.vault.database import (
    complete_reel,
    fail_reel,
    get_daily_processed_count,
    get_next_queued_reel,
    get_reel_by_shortcode,
    get_vault_stats,
    init_vault_db,
    queue_reel,
    retry_reel,
    search_library,
)
from app.vault.models import VaultReel, VaultStatus
from app.vault.worker import process_single_vault_reel


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Use a temporary database for each test to ensure test isolation."""
    temp_db = tmp_path / "test_vault.db"
    with patch.object(settings, "sqlite_db_file", temp_db):
        init_vault_db()
        yield temp_db


@pytest.fixture
def client():
    return TestClient(app)


def test_vault_home_page_renders(client):
    """Verifies that the main personal vault dashboard loads cleanly."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Reel Vault" in response.text
    assert "Save Instagram Reel to Vault" in response.text


def test_add_reel_and_shortcode_deduplication(client):
    """Verifies URL submission, shortcode extraction, path preservation, and deduplication."""
    url1 = "https://www.instagram.com/reel/TEST_SLICE1_001/?utm_source=ig_web_copy_link"

    # 1. First submission queues the reel
    res1 = client.post("/api/vault/reels", json={"url": url1})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["is_new"] is True
    assert data1["reel"]["shortcode"] == "TEST_SLICE1_001"
    assert data1["reel"]["path_type"] == "reel"
    assert data1["reel"]["original_url"] == url1
    assert data1["reel"]["status"] == "queued"

    # 2. Second submission with different query params or /reel/ path must NOT create a duplicate
    url2 = "https://www.instagram.com/reel/TEST_SLICE1_001/"
    res2 = client.post("/api/vault/reels", json={"url": url2})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["is_new"] is False
    assert data2["reel"]["shortcode"] == "TEST_SLICE1_001"


def test_worker_processes_queued_reel():
    """Tests the asynchronous worker processing a queued reel into 'done' state."""
    reel, is_new, _ = queue_reel(
        original_url="https://www.instagram.com/reel/DEMO_WORKER_99/",
        normalized_url="https://www.instagram.com/reel/DEMO_WORKER_99/",
        shortcode="DEMO_WORKER_99",
    )
    assert reel.status == VaultStatus.QUEUED.value

    # Run worker processing
    asyncio.run(process_single_vault_reel(reel))

    # Verify transition to done
    processed = get_reel_by_shortcode("DEMO_WORKER_99")
    assert processed is not None
    assert processed.status == VaultStatus.DONE.value
    assert processed.title is not None
    assert processed.takeaway is not None
    assert len(processed.key_points) > 0
    assert len(processed.tools) > 0


def test_fts5_keyword_search():
    """Verifies SQLite FTS5 search across title, takeaway, tools, and transcript."""
    reel, _, _ = queue_reel(
        original_url="https://www.instagram.com/reel/SEARCH_FTS_01/",
        normalized_url="https://www.instagram.com/reel/SEARCH_FTS_01/",
        shortcode="SEARCH_FTS_01",
    )
    asyncio.run(process_single_vault_reel(reel))

    # 1. Search by tool keyword "FastAPI"
    results, count = search_library(query="FastAPI")
    assert count >= 1
    assert any(r.shortcode == "SEARCH_FTS_01" for r in results)

    # 2. Search by takeaway term "modular"
    results2, count2 = search_library(query="modular")
    assert count2 >= 1
    assert any(r.shortcode == "SEARCH_FTS_01" for r in results2)

    # 3. Search by nonexistent keyword
    results3, count3 = search_library(query="nonexistent_keyword_xyz_99")
    assert count3 == 0


def test_retry_failed_reel(client):
    """Verifies that a failed reel can be retried and re-queued."""
    queue_reel(
        original_url="https://www.instagram.com/reel/RETRY_TEST_01/",
        normalized_url="https://www.instagram.com/reel/RETRY_TEST_01/",
        shortcode="RETRY_TEST_01",
    )
    fail_reel("RETRY_TEST_01", VaultStatus.FAILED.value, "Rate limited by Instagram", "RATE_LIMITED")

    failed = get_reel_by_shortcode("RETRY_TEST_01")
    assert failed.status == VaultStatus.FAILED.value
    assert failed.retry_count == 0

    # Retry via API
    res = client.post("/api/vault/reels/RETRY_TEST_01/retry")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "queued"
    assert data["retry_count"] == 1

    retried = get_reel_by_shortcode("RETRY_TEST_01")
    assert retried.status == VaultStatus.QUEUED.value
    assert retried.error_message is None


def test_daily_processing_cap():
    """Verifies that reaching the daily processing cap halts queue processing."""
    with patch.object(settings, "daily_processing_cap", 1):
        # 1. First reel succeeds
        r1, _, _ = queue_reel("https://www.instagram.com/reel/CAP_01/", "https://www.instagram.com/reel/CAP_01/", "CAP_01")
        asyncio.run(process_single_vault_reel(r1))
        assert get_reel_by_shortcode("CAP_01").status == VaultStatus.DONE.value
        assert get_daily_processed_count() == 1

        # 2. Second reel hits daily cap and fails
        r2, _, _ = queue_reel("https://www.instagram.com/reel/CAP_02/", "https://www.instagram.com/reel/CAP_02/", "CAP_02")
        asyncio.run(process_single_vault_reel(r2))
        p2 = get_reel_by_shortcode("CAP_02")
        assert p2.status == VaultStatus.FAILED.value
        assert "Daily processing cap" in p2.error_message


def test_vault_auth_protection(client):
    """Verifies that configuring VAULT_AUTH_TOKEN enforces token/cookie across API and pages."""
    with patch.object(settings, "vault_auth_token", "super-secret-vault-key"):
        # 1. Unauthenticated API request returns 401
        res1 = client.post("/api/vault/reels", json={"url": "https://www.instagram.com/reel/AUTH_TEST/"})
        assert res1.status_code == 401

        # 2. API request with X-Vault-Token succeeds
        res2 = client.post(
            "/api/vault/reels",
            headers={"X-Vault-Token": "super-secret-vault-key"},
            json={"url": "https://www.instagram.com/reel/AUTH_TEST/"},
        )
        assert res2.status_code == 200

        # 3. Unauthenticated page request renders the unlock form
        res3 = client.get("/")
        assert res3.status_code == 200
        assert "Unlock Vault" in res3.text

        # 4. Form submit sets cookie and redirects
        res4 = client.post("/login", data={"token": "super-secret-vault-key"}, follow_redirects=False)
        assert res4.status_code == 302
        assert "vault_token" in res4.headers.get("set-cookie", "")


def test_reel_detail_page_renders(client):
    """Verifies that the standalone /reels/{shortcode} detail page renders all fields."""
    r, _, _ = queue_reel("https://www.instagram.com/reel/DETAIL_01/", "https://www.instagram.com/reel/DETAIL_01/", "DETAIL_01")
    asyncio.run(process_single_vault_reel(r))

    res = client.get("/reels/DETAIL_01")
    assert res.status_code == 200
    assert "DETAIL_01" in res.text
    assert "Core Takeaway" in res.text
    assert "Key Points" in res.text
    assert "FastAPI" in res.text

