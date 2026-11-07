"""Tests for Reel Vault Slice 7: Backup and Restore."""

import json
import sqlite3
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.spike.config import settings
from app.spike.main import app
from app.spike.schemas import ToolResource
from app.vault.auth import get_expected_token
from app.vault.backup import (
    calculate_record_richness,
    export_library_backup,
    restore_library_backup,
    validate_backup_payload,
)
from app.vault.database import (
    complete_reel,
    delete_reel,
    get_db_connection,
    get_reel_by_shortcode,
    init_vault_db,
    queue_reel,
    search_library,
)
from app.vault.models import VaultReel, VaultStatus


@pytest.fixture
def auth_client():
    """Returns a TestClient with valid authentication cookie and token header."""
    client = TestClient(app)
    expected = get_expected_token()
    token = expected or "test_secret_token"
    client.cookies.set("vault_token", token)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def unauth_client():
    """Returns an unauthenticated TestClient."""
    return TestClient(app)


def test_unauthenticated_export_and_restore_rejected(unauth_client):
    """Verifies that unauthenticated access to backup export and restore is rejected with 401."""
    # Ensure expected token exists so auth is required
    with patch("app.vault.auth.get_expected_token", return_value="secret123"):
        # 1. Export unauth
        res_export = unauth_client.get("/api/vault/backup/export")
        assert res_export.status_code == 401

        # 2. Restore unauth
        res_restore = unauth_client.post("/api/vault/backup/restore", json={"reels": []})
        assert res_restore.status_code == 401


def test_round_trip_export_and_restore(auth_client):
    """Verifies complete export -> delete -> restore round-trip with FTS keyword index rebuilding."""
    init_vault_db()
    sc = "sc_roundtrip_777"
    url = f"https://www.instagram.com/reel/{sc}/"

    # 1. Setup clean record with rich data
    delete_reel(sc)
    reel, is_new, _ = queue_reel(url, url, sc)
    assert is_new is True

    # Complete reel with rich knowledge
    from app.spike.schemas import ReelAnalysisOutput, ToolResource, ExtractedUrl, CodeSnippet
    analysis = ReelAnalysisOutput(
        title="Slice 7 Automated Backup Mastery",
        category="DevOps",
        takeaway="Reliable backups must be round-trip verifiable and idempotent.",
        short_summary="A guide to idempotent JSON backups.",
        english_summary="A comprehensive guide to idempotent JSON backups in Reel Vault.",
        key_points=["Atomic transactions prevent partial writes", "Merge by shortcode preserves richer analyses"],
        tools_and_resources=[ToolResource(name="SQLite", type="database", url="https://sqlite.org", source="spoken")],
        extracted_urls=[ExtractedUrl(url="https://sqlite.org", source="spoken")],
        commands_or_code_snippets=[CodeSnippet(code="sqlite3 vault.db .dump", language="bash")],
        tags=["backup", "restore", "reliability"],
        original_language="en",
        transcript="Always verify your backups with round-trip restore tests before declaring success.",
        on_screen_text="Zero Data Loss Architecture",
        confidence=0.98,
    )
    complete_reel(sc, analysis)

    # Bookmark the reel
    res_bm = auth_client.post(f"/api/vault/reels/{sc}/bookmark")
    assert res_bm.status_code == 200
    assert res_bm.json()["is_bookmarked"] is True

    # 2. Export library
    res_export = auth_client.get("/api/vault/backup/export")
    assert res_export.status_code == 200
    assert "attachment; filename=" in res_export.headers.get("Content-Disposition", "")

    backup_data = res_export.json()
    assert backup_data["version"] == "1.0"
    assert backup_data["app"] == "reel-vault"
    assert backup_data["total_records"] >= 1

    # Find exported reel
    exported_reel = next((r for r in backup_data["reels"] if r["shortcode"] == sc), None)
    assert exported_reel is not None
    assert exported_reel["title"] == "Slice 7 Automated Backup Mastery"
    assert exported_reel["is_bookmarked"] is True
    assert "SQLite" in [t.get("name") for t in exported_reel["tools"]]
    assert "Atomic transactions prevent partial writes" in exported_reel["key_points"]

    # 3. Delete reel from vault
    delete_reel(sc)
    assert get_reel_by_shortcode(sc) is None

    # Search should return nothing for this title
    results_before, count_before = search_library(query="Automated Backup Mastery")
    assert not any(r.shortcode == sc for r in results_before)

    # 4. Restore backup
    res_restore = auth_client.post("/api/vault/backup/restore", json=backup_data)
    assert res_restore.status_code == 200
    restore_resp = res_restore.json()
    assert restore_resp["status"] == "ok"
    assert restore_resp["imported_count"] >= 1

    # 5. Verify restored record & keyword search
    restored = get_reel_by_shortcode(sc)
    assert restored is not None
    assert restored.title == "Slice 7 Automated Backup Mastery"
    assert restored.is_bookmarked is True
    assert restored.transcript == "Always verify your backups with round-trip restore tests before declaring success."

    # FTS Keyword search must immediately find it
    results_after, _ = search_library(query="Automated Backup Mastery")
    assert any(r.shortcode == sc for r in results_after)

    # Cleanup
    delete_reel(sc)


def test_secret_exclusion_in_backup():
    """Verifies that secrets, API keys, tokens, cookies, and tracebacks are excluded/sanitized in export."""
    sc = "sc_secret_test_999"
    url = f"https://www.instagram.com/reel/{sc}/"
    delete_reel(sc)

    # Queue and fail with raw API key in message
    queue_reel(url, url, sc)
    from app.vault.database import fail_reel
    raw_error = "Traceback (most recent call last):\n  File 'worker.py', line 12\nRuntimeError: Failed with key AIzaSyXYZSecret123456789 and Bearer my_secret_token"
    fail_reel(sc, "failed", raw_error, "NETWORK_ERROR")

    backup = export_library_backup()
    backup_str = json.dumps(backup)

    # Secrets must NEVER appear anywhere in the export string
    assert "AIzaSyXYZSecret" not in backup_str
    assert "my_secret_token" not in backup_str
    assert "Traceback (most recent call last)" not in backup_str
    if settings.spike_api_key:
        assert settings.spike_api_key not in backup_str
    if settings.gemini_api_key:
        assert settings.gemini_api_key not in backup_str

    delete_reel(sc)


def test_malformed_backup_rejected_before_db(auth_client):
    """Verifies that malformed backup payloads are rejected with clear errors before DB alteration."""
    def extract_error(response):
        data = response.json()
        if "error" in data and isinstance(data["error"], dict):
            return data["error"].get("message", "")
        return data.get("detail", "")

    # 1. Non-dict root
    res1 = auth_client.post("/api/vault/backup/restore", json="not a json object")
    assert res1.status_code == 400

    # 2. Missing reels array
    res2 = auth_client.post("/api/vault/backup/restore", json={"version": "1.0", "wrong_key": []})
    assert res2.status_code == 400
    assert "must contain a 'reels' list" in extract_error(res2)

    # 3. Item missing shortcode
    res3 = auth_client.post(
        "/api/vault/backup/restore",
        json={"reels": [{"original_url": "https://www.instagram.com/reel/abc/"}]},
    )
    assert res3.status_code == 400
    assert "missing required 'shortcode'" in extract_error(res3)

    # 4. Item missing original_url
    res4 = auth_client.post(
        "/api/vault/backup/restore",
        json={"reels": [{"shortcode": "sc_no_url"}]},
    )
    assert res4.status_code == 400
    assert "missing required 'original_url'" in extract_error(res4)

    # 5. Invalid status
    res5 = auth_client.post(
        "/api/vault/backup/restore",
        json={"reels": [{"shortcode": "sc_bad_status", "original_url": "https://inst.com/p/x/", "status": "exploding"}]},
    )
    assert res5.status_code == 400
    assert "unrecognized status" in extract_error(res5)


def test_duplicate_shortcodes_in_backup_deduplicated():
    """Verifies that duplicate shortcodes in the same backup file are deduplicated safely, keeping richer one."""
    payload = {
        "reels": [
            {
                "shortcode": "sc_dup_in_file",
                "original_url": "https://www.instagram.com/reel/sc_dup_in_file/",
                "status": "queued",
                "title": None,
            },
            {
                "shortcode": "sc_dup_in_file",
                "original_url": "https://www.instagram.com/reel/sc_dup_in_file/",
                "status": "done",
                "title": "Rich Duplicated Record",
                "takeaway": "Deduplicated before database write.",
                "transcript": "Substantive speech transcript.",
            },
        ]
    }
    valid, err, staged = validate_backup_payload(payload)
    assert valid is True
    assert len(staged) == 1
    assert staged[0]["title"] == "Rich Duplicated Record"


def test_richer_record_preservation_and_merging():
    """Verifies that restore never overwrites richer analyses with poorer data, and preserves /p/ URLs."""
    sc = "sc_rich_test_555"
    p_url = f"https://www.instagram.com/p/{sc}/"
    delete_reel(sc)

    # 1. Seed database with a rich DONE record using /p/ URL
    from app.spike.schemas import ReelAnalysisOutput
    queue_reel(p_url, f"https://www.instagram.com/reel/{sc}/", sc, path_type="p")
    analysis = ReelAnalysisOutput(
        title="Rich Existing Gemini Analysis",
        category="Coding",
        takeaway="Existing rich data must be protected against degradation.",
        short_summary="Rich existing summary.",
        english_summary="Rich existing English summary.",
        key_points=["Point 1", "Point 2"],
        original_language="en",
        transcript="Detailed original transcript.",
        confidence=0.99,
    )
    complete_reel(sc, analysis)

    # 2. Attempt to restore an inferior backup with status='queued' and missing transcript
    inferior_backup = {
        "reels": [
            {
                "shortcode": sc,
                "original_url": f"https://www.instagram.com/reel/{sc}/",  # /reel/ instead of /p/
                "status": "queued",
                "title": "Inferior Title",
                "takeaway": None,
                "transcript": None,
            }
        ]
    }
    res = restore_library_backup(inferior_backup)
    assert res["skipped_count"] == 1
    assert res["imported_count"] == 0

    # Ensure rich record was NOT corrupted and /p/ URL is preserved
    preserved = get_reel_by_shortcode(sc)
    assert preserved.title == "Rich Existing Gemini Analysis"
    assert preserved.status == "done"
    assert preserved.path_type == "p"
    assert "/p/" in preserved.original_url
    assert preserved.transcript == "Detailed original transcript."

    # 3. Test reverse: Existing in DB is sparse QUEUED; imported backup has full DONE analysis
    sc2 = "sc_sparse_test_444"
    url2 = f"https://www.instagram.com/reel/{sc2}/"
    delete_reel(sc2)
    queue_reel(url2, url2, sc2)

    rich_backup = {
        "reels": [
            {
                "shortcode": sc2,
                "original_url": url2,
                "status": "done",
                "title": "Newly Restored Rich Knowledge",
                "takeaway": "Upgrades previously sparse records.",
                "key_points": ["Upgraded"],
                "transcript": "Restored transcript.",
                "confidence": 0.95,
            }
        ]
    }
    res2 = restore_library_backup(rich_backup)
    assert res2["merged_count"] == 1

    upgraded = get_reel_by_shortcode(sc2)
    assert upgraded.status == "done"
    assert upgraded.title == "Newly Restored Rich Knowledge"
    assert upgraded.transcript == "Restored transcript."

    # Cleanup
    delete_reel(sc)
    delete_reel(sc2)


def test_restore_idempotency():
    """Verifies that importing the exact same backup repeatedly creates no duplicates or changes."""
    sc = "sc_idempotent_888"
    url = f"https://www.instagram.com/reel/{sc}/"
    delete_reel(sc)

    backup = {
        "reels": [
            {
                "shortcode": sc,
                "original_url": url,
                "status": "done",
                "title": "Idempotent Backup Record",
                "takeaway": "Repeated restore yields identical state.",
            }
        ]
    }

    # Run 1: Import
    res1 = restore_library_backup(backup)
    assert res1["imported_count"] == 1
    assert res1["skipped_count"] == 0

    # Run 2: Exact same payload
    res2 = restore_library_backup(backup)
    assert res2["imported_count"] == 0
    assert res2["merged_count"] == 0
    assert res2["skipped_count"] == 1

    # Run 3: Third time
    res3 = restore_library_backup(backup)
    assert res3["imported_count"] == 0
    assert res3["merged_count"] == 0
    assert res3["skipped_count"] == 1

    delete_reel(sc)


def test_rollback_on_failure():
    """Verifies that if a failure occurs during the database transaction, everything rolls back atomically."""
    sc1 = "sc_rollback_safe_1"
    sc2 = "sc_rollback_safe_2"
    delete_reel(sc1)
    delete_reel(sc2)

    backup = {
        "reels": [
            {"shortcode": sc1, "original_url": f"https://www.instagram.com/reel/{sc1}/", "status": "queued"},
            {"shortcode": sc2, "original_url": f"https://www.instagram.com/reel/{sc2}/", "status": "queued"},
        ]
    }

    # Mock an error during insertion of the second reel
    call_count = 0
    from app.vault import backup as backup_mod
    real_insert = backup_mod._insert_reel_record

    def failing_insert(cursor, item, ts):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise sqlite3.OperationalError("Simulated database write disk error")
        return real_insert(cursor, item, ts)

    with patch.object(backup_mod, "_insert_reel_record", side_effect=failing_insert):
        with pytest.raises(sqlite3.OperationalError):
            restore_library_backup(backup)

    # Entire transaction MUST have rolled back: neither record should exist in DB!
    assert get_reel_by_shortcode(sc1) is None
    assert get_reel_by_shortcode(sc2) is None


def test_multipart_file_upload_restore(auth_client):
    """Verifies that restore works via multipart/form-data file upload (UI file picker format)."""
    sc = "sc_multipart_test_101"
    delete_reel(sc)

    backup_payload = {
        "version": "1.0",
        "reels": [
            {
                "shortcode": sc,
                "original_url": f"https://www.instagram.com/reel/{sc}/",
                "status": "done",
                "title": "Uploaded via Multipart File",
                "takeaway": "Multipart upload test passed.",
            }
        ],
    }

    file_bytes = json.dumps(backup_payload).encode("utf-8")
    files = {"file": ("backup_test.json", file_bytes, "application/json")}

    res = auth_client.post("/api/vault/backup/restore", files=files)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["imported_count"] == 1

    reel = get_reel_by_shortcode(sc)
    assert reel is not None
    assert reel.title == "Uploaded via Multipart File"

    delete_reel(sc)
