"""Unit and integration tests for Reel Vault Slice 2: Legacy Import & Reconciliation."""

import sqlite3
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from unittest.mock import patch

from app.spike.config import settings
from app.spike.main import app
from app.vault.database import get_reel_by_shortcode, init_vault_db, search_library
from app.vault.importer import import_legacy_reels
from app.vault.models import VaultStatus


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Isolate each test in a clean SQLite database with a generic mock legacy CSV fixture."""
    test_db = tmp_path / "test_import_vault.db"
    mock_csv = tmp_path / "mock_reels_recovered.csv"
    mock_csv.write_text(
        "Reel URL,Shortcode,Category,Title,Takeaway,Tools/Tech Mentioned,Key Points,Transcript,On-screen text,Processing source,Recovery status\n"
        "https://www.instagram.com/reel/SAMPLE_LEG_01/,SAMPLE_LEG_01,\"Front-End, UI/UX & Web Development\",Animation Guide,Use GSAP animations for modern web development and startup landing pages,GSAP,Point 1,Detailed speech transcript,Onscreen text,audio_and_video,complete\n"
        "https://www.instagram.com/reel/SAMPLE_LEG_02/,SAMPLE_LEG_02,,,,,Point 1,,,audio,incomplete_ambient_audio\n",
        encoding="utf-8-sig",
    )
    with patch.object(settings, "sqlite_db_file", test_db), \
         patch("app.vault.importer.LEGACY_CSV_PATH", mock_csv):
        init_vault_db()
        yield test_db


@pytest.fixture
def client():
    return TestClient(app)


def test_import_legacy_reels_creates_and_reconciles():
    """Verifies that legacy records are cleanly mapped and reconciled without duplicates."""
    res1 = import_legacy_reels()
    assert res1["status"] == "success"
    assert res1["total_legacy_records"] == 2
    assert res1["imported_count"] == 1
    assert res1["incomplete_count"] == 1
    assert res1["total_vault_reels"] == 2

    # Check substantive imported reel
    r_sub = get_reel_by_shortcode("SAMPLE_LEG_01")
    assert r_sub is not None
    assert r_sub.status == VaultStatus.DONE.value
    assert r_sub.category == "Front-End, UI/UX & Web Development"
    assert "GSAP" in r_sub.takeaway

    # Check incomplete ambient reel
    r_inc = get_reel_by_shortcode("SAMPLE_LEG_02")
    assert r_inc is not None
    assert r_inc.status == VaultStatus.UNAVAILABLE.value
    assert r_inc.failure_category == "INCOMPLETE_LEGACY_RECORD"


def test_import_legacy_reels_idempotency():
    """Verifies that running import multiple times creates 0 duplicates and skips all existing."""
    # First run
    res1 = import_legacy_reels()
    assert res1["total_vault_reels"] == 2

    # Second run
    res2 = import_legacy_reels()
    assert res2["status"] == "success"
    assert res2["imported_count"] == 0
    assert res2["incomplete_count"] == 0
    assert res2["merged_count"] == 0
    assert res2["skipped_count"] == 2
    assert res2["total_vault_reels"] == 2


def test_imported_knowledge_is_fts5_searchable():
    """Verifies that substantive imported reels are immediately indexed and searchable via FTS5."""
    import_legacy_reels()

    # Search for "GSAP" from SAMPLE_LEG_01
    results, count = search_library(query="GSAP")
    assert count >= 1
    assert any(r.shortcode == "SAMPLE_LEG_01" for r in results)

    # Search for "startup"
    results_startup, count_startup = search_library(query="startup")
    assert count_startup >= 1


def test_api_import_endpoint(client):
    """Verifies that the /api/vault/import-legacy HTTP endpoint works cleanly."""
    res = client.post("/api/vault/import-legacy")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["total_vault_reels"] == 2
