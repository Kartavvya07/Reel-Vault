"""Reel Vault Slice 2: Idempotent legacy reels importer with safe reconciliation."""

import csv
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.spike.config import settings
from app.spike.schemas import CodeSnippet, ExtractedUrl, ReelAnalysisOutput, ToolResource
from app.vault.database import get_db_connection, _db_lock
from app.vault.models import VaultReel, VaultStatus

logger = logging.getLogger(__name__)

LEGACY_CSV_PATH = settings.data_dir.parent / "legacy" / "datasets" / "reels_database_recovered.csv"


def import_legacy_reels() -> Dict[str, Any]:
    """
    Imports and reconciles legacy Instagram Reel records into the SQLite library.
    Idempotent: safe to run repeatedly without creating duplicates or overwriting richer data.
    """
    if not LEGACY_CSV_PATH.exists():
        logger.warning("Legacy recovered CSV not found at %s", LEGACY_CSV_PATH)
        return {
            "status": "not_found",
            "message": f"Legacy file not found: {LEGACY_CSV_PATH}",
            "imported_count": 0,
            "merged_count": 0,
            "skipped_count": 0,
            "incomplete_count": 0,
            "total_legacy_records": 0,
        }

    with open(LEGACY_CSV_PATH, "r", encoding="utf-8-sig") as fp:
        legacy_rows = list(csv.DictReader(fp))

    imported_count = 0
    merged_count = 0
    skipped_count = 0
    incomplete_count = 0

    now_str = datetime.now(timezone.utc).isoformat()

    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()

            for row in legacy_rows:
                shortcode = row.get("Shortcode", "").strip()
                if not shortcode:
                    continue

                orig_url = row.get("Reel URL", "").strip() or f"https://www.instagram.com/reel/{shortcode}/"
                path_type = "p" if "/p/" in orig_url else "reel"
                norm_url = f"https://www.instagram.com/reel/{shortcode}/"

                category = row.get("Category", "").strip() or "General"
                takeaway_raw = row.get("Takeaway", "").strip()
                takeaway = takeaway_raw if takeaway_raw and takeaway_raw != "None" else "Extracted insight from Instagram Reel."
                title_raw = row.get("Title", "").strip()

                recovery_status = row.get("Recovery status", "").strip()
                processing_source = row.get("Processing source", "").strip()

                transcript_raw = row.get("Transcript", "").strip()
                transcript = (
                    transcript_raw.replace("---", "").strip()
                    if transcript_raw and transcript_raw != "None"
                    else None
                )

                onscreen_raw = row.get("On-screen text", "").strip()
                on_screen_text = (
                    onscreen_raw if onscreen_raw and onscreen_raw != "None" else None
                )

                # Tools parsing
                tools_raw = row.get("Tools/Tech Mentioned", "").strip()
                tools: List[ToolResource] = []
                if tools_raw and tools_raw != "None":
                    for t in tools_raw.split(","):
                        name = t.strip()
                        if name:
                            tools.append(
                                ToolResource(
                                    name=name,
                                    type="tool",
                                    source="legacy_import",
                                    verification_status="unverified",
                                )
                            )

                # Key points
                key_points_raw = row.get("Key Points", "").strip()
                key_points: List[str] = []
                if key_points_raw and key_points_raw != "None":
                    key_points = [p.strip() for p in key_points_raw.split(";") if p.strip()]
                    if not key_points:
                        key_points = [key_points_raw]

                # Synthesize title cleanly if missing
                if title_raw and title_raw != "None":
                    title = title_raw
                else:
                    # Derive high-quality title from category and takeaway
                    if takeaway and takeaway != "None":
                        first_sentence = takeaway.split(".")[0].strip()
                        if len(first_sentence) > 65:
                            title = first_sentence[:62] + "..."
                        else:
                            title = first_sentence
                    else:
                        title = f"{category}: Reel {shortcode}"

                # Tags derived from category
                raw_words = re.split(r"[,/&\s]+", category)
                tags = [w.lower() for w in raw_words if len(w) > 3]

                # Check if shortcode already exists in database
                cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
                existing = cursor.fetchone()

                if existing:
                    # RECONCILIATION: Safe merge of missing fields only
                    # Never overwrite richer or newer Gemini processed data
                    updated_fields = []
                    update_params = []

                    # 1. Backfill transcript if existing has none and legacy has substantive transcript
                    if (not existing["transcript"]) and transcript:
                        updated_fields.append("transcript = ?")
                        update_params.append(transcript)

                    # 2. Backfill on_screen_text if existing has none and legacy has it
                    if (not existing["on_screen_text"]) and on_screen_text:
                        updated_fields.append("on_screen_text = ?")
                        update_params.append(on_screen_text)

                    # 3. Backfill tools if existing has empty tools and legacy has tools
                    existing_tools = json.loads(existing["tools_json"]) if existing["tools_json"] else []
                    if (not existing_tools) and tools:
                        updated_fields.append("tools_json = ?")
                        update_params.append(json.dumps([t.model_dump() for t in tools]))

                    if updated_fields:
                        updated_fields.append("updated_at = ?")
                        update_params.append(now_str)
                        update_params.append(shortcode)

                        set_clause = ", ".join(updated_fields)
                        cursor.execute(f"UPDATE library_reels SET {set_clause} WHERE shortcode = ?", update_params)

                        # Update FTS5 virtual table
                        cursor.execute("DELETE FROM library_reels_fts WHERE shortcode = ?", (shortcode,))
                        tools_text = " ".join([t.name for t in tools]) if tools else ""
                        cursor.execute(
                            """
                            INSERT INTO library_reels_fts (
                                shortcode, title, takeaway, tags, tools, key_points, transcript, onscreen_text
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                shortcode,
                                existing["title"] or title,
                                existing["takeaway"] or takeaway,
                                " ".join(tags),
                                tools_text,
                                " ".join(key_points),
                                transcript or existing["transcript"] or "",
                                on_screen_text or existing["on_screen_text"] or "",
                            ),
                        )
                        merged_count += 1
                    else:
                        skipped_count += 1
                else:
                    # NEW RECORD INSERTION
                    is_incomplete = recovery_status in (
                        "incomplete_ambient_audio",
                        "incomplete_requires_manual_attention",
                    )

                    if is_incomplete:
                        status = VaultStatus.UNAVAILABLE.value
                        failure_cat = "INCOMPLETE_LEGACY_RECORD"
                        err_msg = f"Legacy record without substantive speech or video knowledge ({recovery_status}). Available for Retry."
                        confidence = 0.2
                        incomplete_count += 1
                    else:
                        status = VaultStatus.DONE.value
                        failure_cat = None
                        err_msg = None
                        confidence = 0.85
                        imported_count += 1

                    tools_json = json.dumps([t.model_dump() for t in tools])
                    key_points_json = json.dumps(key_points)
                    tags_json = json.dumps(tags)

                    raw_meta = {
                        "imported_from": "legacy/datasets/reels_database_recovered.csv",
                        "recovery_status": recovery_status,
                        "processing_source": processing_source,
                    }

                    cursor.execute(
                        """
                        INSERT INTO library_reels (
                            shortcode, original_url, normalized_url, path_type, status,
                            created_at, updated_at, processed_at, retry_count,
                            error_message, failure_category,
                            title, category, takeaway, short_summary,
                            key_points_json, tools_json, extracted_urls_json, code_snippets_json,
                            tags_json, language, transcript, on_screen_text,
                            confidence, raw_analysis_json, processed_via_queue
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            shortcode,
                            orig_url,
                            norm_url,
                            path_type,
                            status,
                            now_str,
                            now_str,
                            now_str if status == VaultStatus.DONE.value else None,
                            0,
                            err_msg,
                            failure_cat,
                            title,
                            category,
                            takeaway,
                            takeaway,  # short summary
                            key_points_json,
                            tools_json,
                            "[]",
                            "[]",
                            tags_json,
                            "en",
                            transcript,
                            on_screen_text,
                            confidence,
                            json.dumps(raw_meta),
                            0,  # Does not consume queue cap
                        ),
                    )

                    # Update FTS5
                    tools_text = " ".join([t.name for t in tools])
                    cursor.execute(
                        """
                        INSERT INTO library_reels_fts (
                            shortcode, title, takeaway, tags, tools, key_points, transcript, onscreen_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            shortcode,
                            title,
                            takeaway,
                            " ".join(tags),
                            tools_text,
                            " ".join(key_points),
                            transcript or "",
                            on_screen_text or "",
                        ),
                    )

            conn.commit()

            cursor.execute("SELECT count(*) FROM library_reels")
            total_now = cursor.fetchone()[0]

            logger.info(
                "Legacy import finished: %d imported, %d merged, %d incomplete, %d skipped. Total in vault: %d",
                imported_count,
                merged_count,
                incomplete_count,
                skipped_count,
                total_now,
            )

            return {
                "status": "success",
                "imported_count": imported_count,
                "merged_count": merged_count,
                "skipped_count": skipped_count,
                "incomplete_count": incomplete_count,
                "total_legacy_records": len(legacy_rows),
                "total_vault_reels": total_now,
            }
        finally:
            conn.close()

