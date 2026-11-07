"""Reel Vault Slice 7: Backup and Restore functionality with safe reconciliation."""

from datetime import datetime, timezone
import json
import logging
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.spike.config import settings
from app.spike.schemas import CodeSnippet, ExtractedUrl, ToolResource
from app.vault.database import get_db_connection, _db_lock, sanitize_error_message
from app.vault.models import VaultReel, VaultStatus

logger = logging.getLogger(__name__)

BACKUP_VERSION = "1.0"


def export_library_backup() -> Dict[str, Any]:
    """
    Exports the complete personal Reel Vault library as a clean, versioned JSON backup.
    Includes all Reel knowledge fields, original URLs, shortcode identities, statuses,
    timestamps, and user bookmarks.
    Never exports secrets, API keys, cookies, or runtime configuration.
    """
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM library_reels ORDER BY created_at ASC")
            rows = cursor.fetchall()

            exported_reels: List[Dict[str, Any]] = []
            for row in rows:
                key_points = json.loads(row["key_points_json"]) if row["key_points_json"] else []
                tools_raw = json.loads(row["tools_json"]) if row["tools_json"] else []
                urls_raw = json.loads(row["extracted_urls_json"]) if row["extracted_urls_json"] else []
                snippets_raw = json.loads(row["code_snippets_json"]) if row["code_snippets_json"] else []
                tags = json.loads(row["tags_json"]) if row["tags_json"] else []

                # Ensure error messages are strictly sanitized
                clean_error = sanitize_error_message(row["error_message"]) if row["error_message"] else None

                is_bookmarked = bool(row["is_bookmarked"]) if "is_bookmarked" in row.keys() else False

                reel_dict: Dict[str, Any] = {
                    "shortcode": row["shortcode"],
                    "original_url": row["original_url"],
                    "normalized_url": row["normalized_url"],
                    "path_type": row["path_type"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "processed_at": row["processed_at"],
                    "retry_count": int(row["retry_count"] or 0),
                    "error_message": clean_error,
                    "failure_category": row["failure_category"],
                    "title": row["title"],
                    "category": row["category"],
                    "takeaway": row["takeaway"],
                    "short_summary": row["short_summary"],
                    "key_points": key_points,
                    "tools": tools_raw,
                    "extracted_urls": urls_raw,
                    "code_snippets": snippets_raw,
                    "tags": tags,
                    "language": row["language"] or "en",
                    "transcript": row["transcript"],
                    "on_screen_text": row["on_screen_text"],
                    "confidence": float(row["confidence"] or 1.0),
                    "is_bookmarked": is_bookmarked,
                    "raw_analysis_json": row["raw_analysis_json"],
                }
                exported_reels.append(reel_dict)

            return {
                "version": BACKUP_VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "app": "reel-vault",
                "total_records": len(exported_reels),
                "reels": exported_reels,
            }
        finally:
            conn.close()


def validate_backup_payload(data: Any) -> Tuple[bool, Optional[str], List[Dict[str, Any]]]:
    """
    Validates the entire backup structure before touching the database.
    Rejects malformed files, incompatible structures, or corrupted items.
    Deduplicates multiple records with the same shortcode in the backup file by keeping the richer one.
    """
    if not isinstance(data, (dict, list)):
        return False, "Malformed backup format: root must be a JSON object or array.", []

    if isinstance(data, dict):
        if "reels" not in data or not isinstance(data["reels"], list):
            return False, "Backup file must contain a 'reels' list of saved Reel records.", []
        reels_raw = data["reels"]
        version = data.get("version", "1.0")
        if not isinstance(version, str):
            return False, "Invalid backup version identifier.", []
    else:
        reels_raw = data

    allowed_statuses = {s.value for s in VaultStatus}
    staged_records: Dict[str, Dict[str, Any]] = {}

    for idx, item in enumerate(reels_raw):
        if not isinstance(item, dict):
            return False, f"Invalid item format at index {idx}: expected an object.", []

        shortcode = str(item.get("shortcode", "")).strip()
        if not shortcode:
            return False, f"Reel at index {idx} is missing required 'shortcode'.", []

        if not re.match(r"^[A-Za-z0-9_-]+$", shortcode):
            return False, f"Invalid shortcode format '{shortcode}' at index {idx}.", []

        orig_url = str(item.get("original_url", "")).strip()
        if not orig_url:
            return False, f"Reel '{shortcode}' at index {idx} is missing required 'original_url'.", []

        status_val = str(item.get("status", VaultStatus.DONE.value)).strip().lower()
        if status_val not in allowed_statuses:
            return False, f"Reel '{shortcode}' at index {idx} has unrecognized status '{status_val}'.", []

        # Validate lists if provided
        for list_field in ("key_points", "tools", "extracted_urls", "code_snippets", "tags"):
            val = item.get(list_field)
            if val is not None and not isinstance(val, list):
                return False, f"Field '{list_field}' on Reel '{shortcode}' must be a list.", []

        # Validate path_type
        path_type = str(item.get("path_type", "")).strip().lower()
        if not path_type:
            path_type = "p" if "/p/" in orig_url else "reel"

        norm_url = str(item.get("normalized_url", "")).strip() or f"https://www.instagram.com/reel/{shortcode}/"

        clean_item = dict(item)
        clean_item["shortcode"] = shortcode
        clean_item["original_url"] = orig_url
        clean_item["normalized_url"] = norm_url
        clean_item["path_type"] = path_type
        clean_item["status"] = status_val

        # Deduplicate records with the same shortcode inside the backup file
        if shortcode in staged_records:
            existing_staged = staged_records[shortcode]
            if calculate_record_richness(clean_item) > calculate_record_richness(existing_staged):
                staged_records[shortcode] = clean_item
        else:
            staged_records[shortcode] = clean_item

    return True, None, list(staged_records.values())


def calculate_record_richness(r: Dict[str, Any]) -> int:
    """Calculates a numerical richness score for a Reel record to avoid overwriting rich data."""
    score = 0
    status = r.get("status")
    if status == VaultStatus.DONE.value:
        score += 100
    elif status == VaultStatus.PROCESSING.value:
        score += 20
    elif status == VaultStatus.QUEUED.value:
        score += 10

    if r.get("title"):
        score += 15
    if r.get("takeaway"):
        score += 15
    if r.get("short_summary"):
        score += 10

    kp = r.get("key_points") or []
    if isinstance(kp, list):
        score += len(kp) * 5

    tools = r.get("tools") or []
    if isinstance(tools, list):
        score += len(tools) * 5

    urls = r.get("extracted_urls") or []
    if isinstance(urls, list):
        score += len(urls) * 5

    snippets = r.get("code_snippets") or []
    if isinstance(snippets, list):
        score += len(snippets) * 5

    tags = r.get("tags") or []
    if isinstance(tags, list):
        score += len(tags) * 2

    transcript = r.get("transcript")
    if transcript:
        score += min(len(str(transcript)) // 20, 50)

    onscreen = r.get("on_screen_text")
    if onscreen:
        score += min(len(str(onscreen)) // 20, 30)

    if r.get("confidence"):
        try:
            score += int(float(r["confidence"]) * 10)
        except Exception:
            pass

    return score


def restore_library_backup(backup_data: Any) -> Dict[str, Any]:
    """
    Restores the library from a validated backup payload.
    - Rejects malformed backup before touching database.
    - Atomic transaction: entire restore rolls back on any database failure.
    - Merges by shortcode without overwriting richer analyses with poorer data.
    - Preserves original /reel/ vs /p/ paths.
    - Idempotent: repeated runs produce no duplicates or regressions.
    - Rebuilds FTS virtual table and refreshes semantic embeddings.
    """
    valid, err_msg, validated_reels = validate_backup_payload(backup_data)
    if not valid:
        raise ValueError(err_msg or "Invalid backup data.")

    imported_count = 0
    merged_count = 0
    skipped_count = 0
    affected_shortcodes: List[str] = []

    now_str = datetime.now(timezone.utc).isoformat()

    with _db_lock:
        conn = get_db_connection()
        try:
            with conn:  # Begins atomic SQLite transaction (rolls back on exception)
                cursor = conn.cursor()
                for item in validated_reels:
                    sc = item["shortcode"]
                    cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (sc,))
                    existing = cursor.fetchone()

                    if not existing:
                        _insert_reel_record(cursor, item, now_str)
                        imported_count += 1
                        affected_shortcodes.append(sc)
                    else:
                        action = _merge_reel_record(cursor, existing, item, now_str)
                        if action == "merged":
                            merged_count += 1
                            affected_shortcodes.append(sc)
                        else:
                            skipped_count += 1

                # Update FTS5 virtual table for all affected records
                for sc in affected_shortcodes:
                    cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (sc,))
                    row = cursor.fetchone()
                    if row:
                        _update_fts_index(cursor, row)
        except Exception as e:
            logger.error("Restore failed during database transaction, rolled back: %s", e)
            raise e
        finally:
            conn.close()

    # Rebuild / refresh embeddings safely outside the database transaction
    try:
        from app.vault.semantic import index_reel
        for sc in affected_shortcodes:
            index_reel(sc, force=False)
    except Exception as e:
        logger.warning("Could not refresh semantic embeddings after restore: %s", e)

    return {
        "status": "ok",
        "total_in_backup": len(validated_reels),
        "imported_count": imported_count,
        "merged_count": merged_count,
        "skipped_count": skipped_count,
        "errors": [],
        "message": f"Restore complete: {imported_count} imported, {merged_count} merged, {skipped_count} skipped.",
    }


def _insert_reel_record(cursor: sqlite3.Cursor, item: Dict[str, Any], default_timestamp: str) -> None:
    """Inserts a new Reel record from the backup into library_reels."""
    key_points_json = json.dumps(item.get("key_points") or [])
    tools_json = json.dumps(item.get("tools") or [])
    urls_json = json.dumps(item.get("extracted_urls") or [])
    snippets_json = json.dumps(item.get("code_snippets") or [])
    tags_json = json.dumps(item.get("tags") or [])
    raw_analysis = item.get("raw_analysis_json")
    if isinstance(raw_analysis, dict):
        raw_analysis = json.dumps(raw_analysis)

    cursor.execute(
        """
        INSERT INTO library_reels (
            shortcode, original_url, normalized_url, path_type, status,
            created_at, updated_at, processed_at, retry_count,
            error_message, failure_category, title, category, takeaway, short_summary,
            key_points_json, tools_json, extracted_urls_json, code_snippets_json, tags_json,
            language, transcript, on_screen_text, confidence, raw_analysis_json,
            processed_via_queue, is_bookmarked
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["shortcode"],
            item["original_url"],
            item["normalized_url"],
            item["path_type"],
            item["status"],
            item.get("created_at") or default_timestamp,
            item.get("updated_at") or default_timestamp,
            item.get("processed_at"),
            int(item.get("retry_count") or 0),
            item.get("error_message"),
            item.get("failure_category"),
            item.get("title"),
            item.get("category"),
            item.get("takeaway"),
            item.get("short_summary"),
            key_points_json,
            tools_json,
            urls_json,
            snippets_json,
            tags_json,
            item.get("language") or "en",
            item.get("transcript"),
            item.get("on_screen_text"),
            float(item.get("confidence") or 1.0),
            raw_analysis,
            0,
            1 if item.get("is_bookmarked") else 0,
        ),
    )


def _merge_reel_record(
    cursor: sqlite3.Cursor,
    existing: sqlite3.Row,
    imported: Dict[str, Any],
    timestamp: str,
) -> str:
    """
    Reconciles an imported record with an existing record by shortcode.
    Never overwrites richer existing data with poorer imported data.
    Preserves original /reel/ vs /p/ paths.
    Returns 'merged' if changes were applied, or 'skipped' if unchanged.
    """
    existing_dict = {k: existing[k] for k in existing.keys()}
    # Convert JSON strings to lists for fair richness comparison
    for k in ("key_points_json", "tools_json", "extracted_urls_json", "code_snippets_json", "tags_json"):
        clean_key = k.replace("_json", "")
        existing_dict[clean_key] = json.loads(existing_dict[k]) if existing_dict.get(k) else []

    existing_richness = calculate_record_richness(existing_dict)
    imported_richness = calculate_record_richness(imported)

    # Preserve /p/ vs /tv/ vs /reel/ original path format
    orig_url = existing["original_url"]
    path_type = existing["path_type"]
    if path_type in ("p", "tv") or "/p/" in orig_url or "/tv/" in orig_url:
        target_orig_url = orig_url
        target_path_type = path_type if path_type in ("p", "tv") else ("tv" if "/tv/" in orig_url else "p")
    else:
        target_orig_url = imported.get("original_url") or orig_url
        target_path_type = imported.get("path_type") or path_type

    # Case 1: Existing is DONE and imported is NOT DONE -> Never overwrite done analysis
    if existing["status"] == VaultStatus.DONE.value and imported.get("status") != VaultStatus.DONE.value:
        # Only backfill non-destructive user bookmark if needed
        if not existing_dict.get("is_bookmarked") and imported.get("is_bookmarked"):
            cursor.execute(
                "UPDATE library_reels SET is_bookmarked = 1, updated_at = ? WHERE shortcode = ?",
                (timestamp, existing["shortcode"]),
            )
            return "merged"
        return "skipped"

    # Case 2: Imported is strictly richer than existing (e.g. existing was queued/failed or sparse legacy)
    if imported_richness > existing_richness:
        key_points_json = json.dumps(imported.get("key_points") or [])
        tools_json = json.dumps(imported.get("tools") or [])
        urls_json = json.dumps(imported.get("extracted_urls") or [])
        snippets_json = json.dumps(imported.get("code_snippets") or [])
        tags_json = json.dumps(imported.get("tags") or [])
        raw_analysis = imported.get("raw_analysis_json")
        if isinstance(raw_analysis, dict):
            raw_analysis = json.dumps(raw_analysis)

        # Preserve bookmark if either was bookmarked
        is_bookmarked = 1 if (existing_dict.get("is_bookmarked") or imported.get("is_bookmarked")) else 0

        cursor.execute(
            """
            UPDATE library_reels 
            SET original_url = ?, path_type = ?, status = ?, updated_at = ?,
                processed_at = COALESCE(?, processed_at),
                title = COALESCE(?, title),
                category = COALESCE(?, category),
                takeaway = COALESCE(?, takeaway),
                short_summary = COALESCE(?, short_summary),
                key_points_json = ?, tools_json = ?, extracted_urls_json = ?,
                code_snippets_json = ?, tags_json = ?, language = ?,
                transcript = COALESCE(?, transcript),
                on_screen_text = COALESCE(?, on_screen_text),
                confidence = ?, raw_analysis_json = COALESCE(?, raw_analysis_json),
                is_bookmarked = ?
            WHERE shortcode = ?
            """,
            (
                target_orig_url,
                target_path_type,
                imported["status"],
                timestamp,
                imported.get("processed_at"),
                imported.get("title"),
                imported.get("category"),
                imported.get("takeaway"),
                imported.get("short_summary"),
                key_points_json,
                tools_json,
                urls_json,
                snippets_json,
                tags_json,
                imported.get("language") or "en",
                imported.get("transcript"),
                imported.get("on_screen_text"),
                float(imported.get("confidence") or 1.0),
                raw_analysis,
                is_bookmarked,
                existing["shortcode"],
            ),
        )
        return "merged"

    # Case 3: Existing is richer or equal; safe backfill of missing fields only
    updates = []
    params = []

    # Backfill missing transcript
    if not existing["transcript"] and imported.get("transcript"):
        updates.append("transcript = ?")
        params.append(imported["transcript"])

    # Backfill missing on-screen text
    if not existing["on_screen_text"] and imported.get("on_screen_text"):
        updates.append("on_screen_text = ?")
        params.append(imported["on_screen_text"])

    # Backfill missing tools
    existing_tools = existing_dict.get("tools") or []
    if not existing_tools and imported.get("tools"):
        updates.append("tools_json = ?")
        params.append(json.dumps(imported["tools"]))

    # Backfill missing key points
    existing_kp = existing_dict.get("key_points") or []
    if not existing_kp and imported.get("key_points"):
        updates.append("key_points_json = ?")
        params.append(json.dumps(imported["key_points"]))

    # Backfill bookmark
    if not existing_dict.get("is_bookmarked") and imported.get("is_bookmarked"):
        updates.append("is_bookmarked = 1")

    if updates:
        updates.append("updated_at = ?")
        params.append(timestamp)
        params.append(existing["shortcode"])
        set_clause = ", ".join(updates)
        cursor.execute(f"UPDATE library_reels SET {set_clause} WHERE shortcode = ?", params)
        return "merged"

    return "skipped"


def _update_fts_index(cursor: sqlite3.Cursor, row: sqlite3.Row) -> None:
    """Updates the FTS5 full-text search index for a Reel record."""
    shortcode = row["shortcode"]
    cursor.execute("DELETE FROM library_reels_fts WHERE shortcode = ?", (shortcode,))

    tools_raw = json.loads(row["tools_json"]) if row["tools_json"] else []
    tools_text = " ".join([t.get("name", "") if isinstance(t, dict) else str(t) for t in tools_raw])

    kp_raw = json.loads(row["key_points_json"]) if row["key_points_json"] else []
    kp_text = " ".join([str(p) for p in kp_raw])

    tags_raw = json.loads(row["tags_json"]) if row["tags_json"] else []
    tags_text = " ".join([str(t) for t in tags_raw])

    cursor.execute(
        """
        INSERT INTO library_reels_fts (
            shortcode, title, takeaway, tags, tools, key_points, transcript, onscreen_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shortcode,
            row["title"] or "",
            row["takeaway"] or "",
            tags_text,
            tools_text,
            kp_text,
            row["transcript"] or "",
            row["on_screen_text"] or "",
        ),
    )

