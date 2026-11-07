"""Database management for Reel Vault personal library with SQLite FTS5 search."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.spike.config import settings
from app.spike.schemas import ReelAnalysisOutput, ToolResource, ExtractedUrl, CodeSnippet
from app.vault.models import VaultReel, VaultStatus, VaultStats

logger = logging.getLogger(__name__)

_db_lock = threading.RLock()


def get_db_connection() -> sqlite3.Connection:
    """Returns a SQLite connection with Row factory enabled and busy timeout."""
    conn = sqlite3.connect(settings.sqlite_db_file, timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_vault_db() -> None:
    """Initializes library_reels table, FTS5 virtual table, and seeds historical data if empty."""
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS library_reels (
                    shortcode TEXT PRIMARY KEY,
                    original_url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    path_type TEXT NOT NULL DEFAULT 'reel',
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    processed_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    failure_category TEXT,
                    title TEXT,
                    category TEXT,
                    takeaway TEXT,
                    short_summary TEXT,
                    key_points_json TEXT,
                    tools_json TEXT,
                    extracted_urls_json TEXT,
                    code_snippets_json TEXT,
                    tags_json TEXT,
                    language TEXT DEFAULT 'en',
                    transcript TEXT,
                    on_screen_text TEXT,
                    confidence REAL DEFAULT 1.0,
                    raw_analysis_json TEXT,
                    processed_via_queue INTEGER NOT NULL DEFAULT 0,
                    is_bookmarked INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # Safe migration if table already existed without columns
            try:
                cursor.execute("ALTER TABLE library_reels ADD COLUMN processed_via_queue INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE library_reels ADD COLUMN is_bookmarked INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE library_reels ADD COLUMN analysis_mode TEXT DEFAULT 'audio'")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE library_reels ADD COLUMN is_thin_extraction INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_vault_status ON library_reels(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_vault_category ON library_reels(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_vault_created ON library_reels(created_at)")

            # Create FTS5 virtual table for lightning-fast keyword search
            cursor.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS library_reels_fts USING fts5(
                    shortcode,
                    title,
                    takeaway,
                    tags,
                    tools,
                    key_points,
                    transcript,
                    onscreen_text
                )
                """
            )
            conn.commit()

            # Seed pre-existing successful data if library is completely empty
            cursor.execute("SELECT count(*) FROM library_reels")
            current_count = cursor.fetchone()[0]
            if current_count == 0:
                _seed_historical_checkpoint(cursor, conn)
        except Exception as e:
            logger.error("Error initializing vault database: %s", e)
        finally:
            conn.close()


def _seed_historical_checkpoint(cursor: sqlite3.Cursor, conn: sqlite3.Connection) -> None:
    """Seeds historical processed reels from server_export_checkpoint.json if present."""
    checkpoint_file = settings.data_dir / "server_export_checkpoint.json"
    if not checkpoint_file.exists():
        return

    try:
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        records = data.get("results", [])
        seeded_count = 0
        for r in records:
            shortcode = r.get("shortcode")
            analysis = r.get("analysis_output")
            if not shortcode or not analysis:
                continue

            now_str = r.get("attempt_timestamp") or datetime.now(timezone.utc).isoformat()
            orig_url = r.get("original_url") or f"https://www.instagram.com/reel/{shortcode}/"
            norm_url = r.get("normalized_url") or f"https://www.instagram.com/reel/{shortcode}/"
            path_type = r.get("path_type") or "reel"

            title = analysis.get("title", "")
            category = analysis.get("category", "General")
            takeaway = analysis.get("takeaway", "")
            short_summary = analysis.get("short_summary", "")
            key_points = analysis.get("key_points", [])
            tools = analysis.get("tools_and_resources", [])
            extracted_urls = analysis.get("extracted_urls", [])
            code_snippets = analysis.get("commands_or_code_snippets", [])
            tags = analysis.get("tags", [])
            language = analysis.get("original_language", "en")
            transcript = analysis.get("transcript", "")
            on_screen_text = analysis.get("on_screen_text", "")
            confidence = float(analysis.get("confidence", 1.0))

            cursor.execute(
                """
                INSERT OR IGNORE INTO library_reels (
                    shortcode, original_url, normalized_url, path_type, status,
                    created_at, updated_at, processed_at, retry_count,
                    title, category, takeaway, short_summary,
                    key_points_json, tools_json, extracted_urls_json, code_snippets_json,
                    tags_json, language, transcript, on_screen_text, confidence, raw_analysis_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shortcode,
                    orig_url,
                    norm_url,
                    path_type,
                    VaultStatus.DONE.value,
                    now_str,
                    now_str,
                    now_str,
                    0,
                    title,
                    category,
                    takeaway,
                    short_summary,
                    json.dumps(key_points),
                    json.dumps(tools),
                    json.dumps(extracted_urls),
                    json.dumps(code_snippets),
                    json.dumps(tags),
                    language,
                    transcript,
                    on_screen_text,
                    confidence,
                    json.dumps(analysis),
                ),
            )

            # Insert into FTS5
            tools_text = " ".join([t.get("name", "") for t in tools if isinstance(t, dict)])
            key_points_text = " ".join(key_points)
            tags_text = " ".join(tags)

            cursor.execute(
                """
                INSERT OR IGNORE INTO library_reels_fts (
                    shortcode, title, takeaway, tags, tools, key_points, transcript, onscreen_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shortcode,
                    title,
                    takeaway,
                    tags_text,
                    tools_text,
                    key_points_text,
                    transcript or "",
                    on_screen_text or "",
                ),
            )
            seeded_count += 1

        conn.commit()
        logger.info("Successfully seeded %d historical reels into personal vault.", seeded_count)
    except Exception as e:
        logger.warning("Could not seed historical checkpoint data: %s", e)


def queue_reel(
    original_url: str, normalized_url: str, shortcode: str, path_type: str = "reel"
) -> Tuple[VaultReel, bool, str]:
    """
    Submits a Reel URL to the personal library queue or saves an unanalyzed post entry.
    Re-sharing an existing shortcode preserves identity and prevents duplication.
    """
    now_str = datetime.now(timezone.utc).isoformat()
    is_post = path_type in ("p", "tv", "post") or "/p/" in normalized_url or "/tv/" in normalized_url

    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            existing = cursor.fetchone()

            # Manual post handling when analyze_posts is disabled
            if is_post and not settings.analyze_posts:
                if existing:
                    reel = _row_to_vault_reel(existing)
                    # If it was previously failed/unavailable on login wall, transition to saved (not analyzed)
                    if reel.status in (VaultStatus.FAILED.value, VaultStatus.UNAVAILABLE.value):
                        cursor.execute(
                            """
                            UPDATE library_reels 
                            SET path_type = ?, status = ?, analysis_mode = 'not_analyzed', error_message = NULL, failure_category = NULL, updated_at = ?
                            WHERE shortcode = ?
                            """,
                            (path_type, VaultStatus.SAVED.value, now_str, shortcode),
                        )
                        conn.commit()
                        cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
                        return _row_to_vault_reel(cursor.fetchone()), False, "Post saved to vault (not analyzed)."
                    return reel, False, "This post is already saved in your vault!"

                # Insert immediately as saved (not analyzed)
                cursor.execute(
                    """
                    INSERT INTO library_reels (
                        shortcode, original_url, normalized_url, path_type, status,
                        analysis_mode, title, takeaway, category, tags_json,
                        created_at, updated_at, retry_count, processed_via_queue
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        shortcode,
                        original_url,
                        normalized_url,
                        path_type,
                        VaultStatus.SAVED.value,
                        "not_analyzed",
                        f"Post {shortcode}",
                        "",
                        None,
                        "[]",
                        now_str,
                        now_str,
                        0,
                        0,
                    ),
                )
                conn.commit()
                # Insert into FTS5
                try:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO library_reels_fts (
                            shortcode, title, takeaway, tags, tools, key_points, transcript, onscreen_text
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (shortcode, f"Post {shortcode}", "", "", "", "", "", ""),
                    )
                    conn.commit()
                except Exception:
                    pass

                cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
                new_row = cursor.fetchone()
                reel = _row_to_vault_reel(new_row)
                try:
                    from app.vault.semantic import index_reel
                    index_reel(shortcode, force=True)
                except Exception as e:
                    logger.warning("Could not index initial embedding for post %s: %s", shortcode, e)
                return reel, True, "Post saved to vault (not analyzed)."

            if existing:
                reel = _row_to_vault_reel(existing)
                if reel.status in (VaultStatus.DONE.value, VaultStatus.SAVED.value):
                    return reel, False, "This Reel is already saved in your vault!"
                elif reel.status in (VaultStatus.QUEUED.value, VaultStatus.PROCESSING.value):
                    return reel, False, f"Reel is already being processed ({reel.status})."
                else:
                    # Failed or unavailable: re-queue it
                    cursor.execute(
                        """
                        UPDATE library_reels 
                        SET status = ?, retry_count = retry_count + 1, error_message = NULL, updated_at = ?
                        WHERE shortcode = ?
                        """,
                        (VaultStatus.QUEUED.value, now_str, shortcode),
                    )
                    conn.commit()
                    cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
                    updated_row = cursor.fetchone()
                    return _row_to_vault_reel(updated_row), False, "Re-queued previously failed Reel for analysis."

            # Insert new record in queued status
            cursor.execute(
                """
                INSERT INTO library_reels (
                    shortcode, original_url, normalized_url, path_type, status,
                    created_at, updated_at, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shortcode,
                    original_url,
                    normalized_url,
                    path_type,
                    VaultStatus.QUEUED.value,
                    now_str,
                    now_str,
                    0,
                ),
            )
            conn.commit()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            new_row = cursor.fetchone()
            return _row_to_vault_reel(new_row), True, "Reel successfully queued for analysis."
        finally:
            conn.close()


def requeue_stale_processing() -> int:
    """Re-queues any rows that were left in 'processing' status across worker/server restarts."""
    with _db_lock:
        conn = get_db_connection()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE library_reels 
                SET status = ?, error_message = 'Worker restarted; re-queued.', updated_at = ?
                WHERE status = ?
                """,
                (VaultStatus.QUEUED.value, now_str, VaultStatus.PROCESSING.value),
            )
            count = cursor.rowcount
            conn.commit()
            return count
        finally:
            conn.close()


def get_next_queued_reel() -> Optional[VaultReel]:
    """Retrieves the oldest queued reel for asynchronous processing."""
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM library_reels 
                WHERE status = ? 
                ORDER BY created_at ASC 
                LIMIT 1
                """,
                (VaultStatus.QUEUED.value,),
            )
            row = cursor.fetchone()
            return _row_to_vault_reel(row) if row else None
        finally:
            conn.close()


def set_reel_processing(shortcode: str) -> None:
    """Transitions a reel state to 'processing'."""
    with _db_lock:
        conn = get_db_connection()
        try:
            now_str = datetime.now(timezone.utc).isoformat()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE library_reels 
                SET status = ?, updated_at = ? 
                WHERE shortcode = ?
                """,
                (VaultStatus.PROCESSING.value, now_str, shortcode),
            )
            conn.commit()
        finally:
            conn.close()


def complete_reel(
    shortcode: str,
    analysis: ReelAnalysisOutput,
    analysis_mode: str = "audio",
    is_thin_extraction: bool = False,
) -> None:
    """Transitions a reel state to 'done' and populates extracted structured data & FTS5 index."""
    now_str = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            key_points_json = json.dumps(analysis.key_points)
            tools_json = json.dumps([t.model_dump() for t in analysis.tools_and_resources])
            urls_json = json.dumps([u.model_dump() for u in analysis.extracted_urls])
            snippets_json = json.dumps([s.model_dump() for s in analysis.commands_or_code_snippets])
            tags_json = json.dumps(analysis.tags)
            raw_analysis_json = analysis.model_dump_json()

            cursor.execute(
                """
                UPDATE library_reels 
                SET status = ?, updated_at = ?, processed_at = ?,
                    title = ?, category = ?, takeaway = ?, short_summary = ?,
                    key_points_json = ?, tools_json = ?, extracted_urls_json = ?,
                    code_snippets_json = ?, tags_json = ?, language = ?,
                    transcript = ?, on_screen_text = ?, confidence = ?,
                    raw_analysis_json = ?, analysis_mode = ?, is_thin_extraction = ?,
                    error_message = NULL, failure_category = NULL, processed_via_queue = 1
                WHERE shortcode = ?
                """,
                (
                    VaultStatus.DONE.value,
                    now_str,
                    now_str,
                    analysis.title,
                    analysis.category,
                    analysis.takeaway,
                    analysis.short_summary,
                    key_points_json,
                    tools_json,
                    urls_json,
                    snippets_json,
                    tags_json,
                    analysis.original_language,
                    analysis.transcript,
                    analysis.on_screen_text,
                    analysis.confidence,
                    raw_analysis_json,
                    analysis_mode,
                    1 if is_thin_extraction else 0,
                    shortcode,
                ),
            )

            # Update FTS5 virtual table
            cursor.execute("DELETE FROM library_reels_fts WHERE shortcode = ?", (shortcode,))
            tools_text = " ".join([t.name for t in analysis.tools_and_resources])
            key_points_text = " ".join(analysis.key_points)
            tags_text = " ".join(analysis.tags)

            cursor.execute(
                """
                INSERT INTO library_reels_fts (
                    shortcode, title, takeaway, tags, tools, key_points, transcript, onscreen_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    shortcode,
                    analysis.title,
                    analysis.takeaway,
                    tags_text,
                    tools_text,
                    key_points_text,
                    analysis.transcript or "",
                    analysis.on_screen_text or "",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # Automatically index embedding for natural language search
    try:
        from app.vault.semantic import index_reel
        index_reel(shortcode, force=True)
    except Exception as e:
        logger.warning("Could not auto-index reel %s: %s", shortcode, e)


def sanitize_error_message(msg: Optional[str]) -> str:
    """Sanitizes error messages to protect secrets, tokens, and suppress raw stack traces."""
    if not msg:
        return "An unknown error occurred during Reel processing."

    cleaned = str(msg)

    # If it's a Python traceback, extract the final exception line first
    if "Traceback (most recent call last):" in cleaned:
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        cleaned = lines[-1] if lines else "Internal processing error."

    # Remove known token/key patterns
    cleaned = re.sub(r"AIzaSy[A-Za-z0-9_-]+", "[REDACTED_KEY]", cleaned)
    cleaned = re.sub(r"(Bearer\s+)[A-Za-z0-9_.-]+", r"\1[REDACTED_TOKEN]", cleaned)
    cleaned = re.sub(r"([?&](?:token|key|api_key|auth)=)[^&\s]+", r"\1[REDACTED]", cleaned)

    if len(cleaned) > 280:
        cleaned = cleaned[:277] + "..."

    return cleaned


def fail_reel(shortcode: str, status: str, error_message: str, failure_category: str) -> None:
    """Transitions a reel state to 'failed' or 'unavailable' with sanitized error messages."""
    now_str = datetime.now(timezone.utc).isoformat()
    clean_error = sanitize_error_message(error_message)
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE library_reels 
                SET status = ?, error_message = ?, failure_category = ?, updated_at = ?, processed_at = ?, processed_via_queue = 1
                WHERE shortcode = ?
                """,
                (status, clean_error, failure_category, now_str, now_str, shortcode),
            )
            conn.commit()
        finally:
            conn.close()


def delete_reel(shortcode: str) -> bool:
    """
    Safely removes a Reel from the library, including its FTS5 index and semantic embeddings.
    Returns True if the reel existed and was deleted, False otherwise.
    """
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT shortcode FROM library_reels WHERE shortcode = ?", (shortcode,))
            if not cursor.fetchone():
                return False

            # Delete from library_reels
            cursor.execute("DELETE FROM library_reels WHERE shortcode = ?", (shortcode,))

            # Delete from FTS5 index
            try:
                cursor.execute("DELETE FROM library_reels_fts WHERE shortcode = ?", (shortcode,))
            except Exception:
                pass

            # Delete from embeddings table
            try:
                cursor.execute("DELETE FROM reel_embeddings WHERE shortcode = ?", (shortcode,))
            except Exception:
                pass

            conn.commit()
            return True
        finally:
            conn.close()


def retry_reel(shortcode: str) -> Optional[VaultReel]:
    """Manually re-queues a failed or unavailable reel for re-processing."""
    now_str = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            row = cursor.fetchone()
            if not row:
                return None

            cursor.execute(
                """
                UPDATE library_reels 
                SET status = ?, retry_count = retry_count + 1, error_message = NULL, updated_at = ?
                WHERE shortcode = ?
                """,
                (VaultStatus.QUEUED.value, now_str, shortcode),
            )
            conn.commit()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            return _row_to_vault_reel(cursor.fetchone())
        finally:
            conn.close()


def get_reel_by_shortcode(shortcode: str) -> Optional[VaultReel]:
    """Retrieves a single vault reel by its unique shortcode."""
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            row = cursor.fetchone()
            return _row_to_vault_reel(row) if row else None
        finally:
            conn.close()


def get_daily_processed_count() -> int:
    """Returns the total number of reels processed today (UTC date) via queue."""
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT count(*) FROM library_reels 
                WHERE processed_via_queue = 1
                  AND status IN (?, ?, ?) 
                  AND date(processed_at) = date('now')
                """,
                (VaultStatus.DONE.value, VaultStatus.FAILED.value, VaultStatus.UNAVAILABLE.value),
            )
            return cursor.fetchone()[0]
        finally:
            conn.close()


def get_vault_stats() -> VaultStats:
    """Computes comprehensive vault statistics and distinct categories."""
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT status, count(*) FROM library_reels GROUP BY status")
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

            cursor.execute("SELECT DISTINCT category FROM library_reels WHERE category IS NOT NULL AND category != ''")
            categories = sorted([row[0] for row in cursor.fetchall()])

            daily_count = get_daily_processed_count()

            total = sum(status_counts.values())
            return VaultStats(
                total_reels=total,
                done_count=status_counts.get(VaultStatus.DONE.value, 0),
                processing_count=status_counts.get(VaultStatus.PROCESSING.value, 0),
                queued_count=status_counts.get(VaultStatus.QUEUED.value, 0),
                failed_count=status_counts.get(VaultStatus.FAILED.value, 0),
                unavailable_count=status_counts.get(VaultStatus.UNAVAILABLE.value, 0),
                daily_processed_count=daily_count,
                daily_cap=settings.daily_processing_cap,
                categories=categories,
            )
        finally:
            conn.close()


def search_library(
    query: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    sort: Optional[str] = "relevance",
    limit: int = 100,
    offset: int = 0,
    return_meta: bool = False,
    item_type: Optional[str] = None,
    needs_note: Optional[bool] = None,
) -> Any:
    """
    Searches and filters the library using hybrid semantic vector and SQLite FTS5 keyword matching.
    Supports sorting ('relevance', 'newest', 'oldest', 'title') and filtering by category, status, type, and note status.
    When return_meta=True, returns (reels, total_matches, is_degraded, embedding_model).
    """
    from app.vault.semantic import search_natural_language
    return search_natural_language(
        query=query,
        category=category,
        status=status,
        sort=sort,
        limit=limit,
        offset=offset,
        return_meta=return_meta,
        item_type=item_type,
        needs_note=needs_note,
    )


def _row_to_vault_reel(row: sqlite3.Row) -> VaultReel:
    """Converts a SQLite row from library_reels to a VaultReel model."""
    key_points = json.loads(row["key_points_json"]) if row["key_points_json"] else []
    tools_raw = json.loads(row["tools_json"]) if row["tools_json"] else []
    tools = [ToolResource(**t) if isinstance(t, dict) else ToolResource(name=str(t)) for t in tools_raw]

    urls_raw = json.loads(row["extracted_urls_json"]) if row["extracted_urls_json"] else []
    extracted_urls = [ExtractedUrl(**u) if isinstance(u, dict) else ExtractedUrl(url=str(u)) for u in urls_raw]

    snippets_raw = json.loads(row["code_snippets_json"]) if row["code_snippets_json"] else []
    code_snippets = [CodeSnippet(**s) if isinstance(s, dict) else CodeSnippet(code=str(s)) for s in snippets_raw]

    tags = json.loads(row["tags_json"]) if row["tags_json"] else []

    # Derive or read analysis_mode (detect mock pipeline output)
    is_mock = False
    if "analysis_mode" in row.keys() and row["analysis_mode"] == "mock":
        is_mock = True
    elif (
        row["takeaway"] == "Prioritize minimal, high-leverage workflows and modular architecture."
        or str(row["title"] or "").startswith("Knowledge Insights: ")
        or "Extracted via Reel Vault asynchronous worker." in key_points
    ):
        is_mock = True

    if is_mock:
        analysis_mode = "mock"
    elif "analysis_mode" in row.keys() and row["analysis_mode"]:
        analysis_mode = row["analysis_mode"]
    elif row["status"] == VaultStatus.SAVED.value or row["path_type"] in ("post", "p", "tv"):
        analysis_mode = "not_analyzed"
    else:
        # Fallback heuristic for historical records
        if row["on_screen_text"] and len(str(row["on_screen_text"]).strip()) > 5:
            analysis_mode = "video"
        elif row["transcript"] and len(str(row["transcript"]).split()) >= 10:
            analysis_mode = "audio"
        else:
            analysis_mode = "caption_only"

    # Derive or read is_thin_extraction
    is_thin = False
    if "is_thin_extraction" in row.keys() and row["is_thin_extraction"] is not None:
        is_thin = bool(row["is_thin_extraction"])
    else:
        # Thin if done but has minimal substance
        transcript_words = len(str(row["transcript"]).split()) if row["transcript"] else 0
        has_onscreen = bool(row["on_screen_text"] and len(str(row["on_screen_text"]).strip()) > 5)
        has_tools = bool(tools and len(tools) > 0)
        has_pts = bool(key_points and len(key_points) > 0)
        if row["status"] == VaultStatus.DONE.value and not has_onscreen and not has_tools and not has_pts and transcript_words < 12:
            is_thin = True
        elif row["status"] == VaultStatus.DONE.value and analysis_mode == "caption_only" and not has_tools and not has_pts:
            is_thin = True

    return VaultReel(
        shortcode=row["shortcode"],
        original_url=row["original_url"],
        normalized_url=row["normalized_url"],
        path_type=row["path_type"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        processed_at=row["processed_at"],
        retry_count=int(row["retry_count"]),
        error_message=row["error_message"],
        failure_category=row["failure_category"],
        title=row["title"],
        category=row["category"],
        takeaway=row["takeaway"],
        short_summary=row["short_summary"],
        key_points=key_points,
        tools=tools,
        extracted_urls=extracted_urls,
        code_snippets=code_snippets,
        tags=tags,
        language=row["language"] or "en",
        transcript=row["transcript"],
        on_screen_text=row["on_screen_text"],
        confidence=float(row["confidence"] or 1.0),
        is_bookmarked=bool(row["is_bookmarked"]) if "is_bookmarked" in row.keys() else False,
        analysis_mode=analysis_mode,
        is_thin_extraction=is_thin,
    )


def toggle_bookmark(shortcode: str) -> Optional[VaultReel]:
    """Toggles the bookmark status of a saved reel."""
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT is_bookmarked FROM library_reels WHERE shortcode = ?", (shortcode,))
            row = cursor.fetchone()
            if not row:
                return None
            current_val = int(row["is_bookmarked"]) if "is_bookmarked" in row.keys() else 0
            new_val = 0 if current_val else 1
            now_str = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "UPDATE library_reels SET is_bookmarked = ?, updated_at = ? WHERE shortcode = ?",
                (new_val, now_str, shortcode),
            )
            conn.commit()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            return _row_to_vault_reel(cursor.fetchone())
        finally:
            conn.close()


def reanalyze_reel(shortcode: str, force_video: bool = True) -> Optional[VaultReel]:
    """Re-queues a single reel specifically for video re-analysis."""
    now_str = datetime.now(timezone.utc).isoformat()
    mode = "video" if force_video else "audio"
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            row = cursor.fetchone()
            if not row:
                return None

            cursor.execute(
                """
                UPDATE library_reels
                SET status = ?, retry_count = retry_count + 1, error_message = NULL, failure_category = NULL, analysis_mode = ?, updated_at = ?
                WHERE shortcode = ?
                """,
                (VaultStatus.QUEUED.value, mode, now_str, shortcode),
            )
            conn.commit()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            return _row_to_vault_reel(cursor.fetchone())
        finally:
            conn.close()


def reanalyze_unavailable_reels() -> Dict[str, Any]:
    """Re-queues all reels currently marked as 'unavailable' or 'failed' for video re-analysis."""
    now_str = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT shortcode FROM library_reels
                WHERE status IN (?, ?)
                """,
                (VaultStatus.UNAVAILABLE.value, VaultStatus.FAILED.value),
            )
            shortcodes = [r[0] for r in cursor.fetchall()]
            if not shortcodes:
                return {"count": 0, "shortcodes": []}

            placeholders = ",".join(["?"] * len(shortcodes))
            cursor.execute(
                f"""
                UPDATE library_reels
                SET status = ?, retry_count = retry_count + 1, error_message = NULL, failure_category = NULL, analysis_mode = 'video', updated_at = ?
                WHERE shortcode IN ({placeholders})
                """,
                [VaultStatus.QUEUED.value, now_str] + shortcodes,
            )
            conn.commit()
            return {"count": len(shortcodes), "shortcodes": shortcodes}
        finally:
            conn.close()


def update_reel_details(
    shortcode: str,
    title: Optional[str] = None,
    note: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Optional[VaultReel]:
    """
    Updates user-editable metadata (title, note/takeaway, category, tags) for a reel or post.
    Syncs with FTS5 keyword index and triggers semantic re-embedding.
    """
    now_str = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            existing = cursor.fetchone()
            if not existing:
                return None

            updates = ["updated_at = ?"]
            params: List[Any] = [now_str]

            if title is not None:
                updates.append("title = ?")
                params.append(title.strip() if title else f"Post {shortcode}")

            if note is not None:
                updates.append("takeaway = ?")
                params.append(note.strip())

            if category is not None:
                updates.append("category = ?")
                params.append(category.strip() if category else None)

            if tags is not None:
                updates.append("tags_json = ?")
                params.append(json.dumps([t.strip() for t in tags if t.strip()]))

            params.append(shortcode)
            set_clause = ", ".join(updates)
            cursor.execute(f"UPDATE library_reels SET {set_clause} WHERE shortcode = ?", params)
            conn.commit()

            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            updated_row = cursor.fetchone()
            reel = _row_to_vault_reel(updated_row)

            # Update FTS5 virtual table
            try:
                cursor.execute("DELETE FROM library_reels_fts WHERE shortcode = ?", (shortcode,))
                tools_text = " ".join([t.name for t in reel.tools])
                key_points_text = " ".join(reel.key_points)
                tags_text = " ".join(reel.tags)
                cursor.execute(
                    """
                    INSERT INTO library_reels_fts (
                        shortcode, title, takeaway, tags, tools, key_points, transcript, onscreen_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        shortcode,
                        reel.title or "",
                        reel.takeaway or "",
                        tags_text,
                        tools_text,
                        key_points_text,
                        reel.transcript or "",
                        reel.on_screen_text or "",
                    ),
                )
                conn.commit()
            except Exception as e:
                logger.warning("Could not update FTS5 for %s: %s", shortcode, e)
        finally:
            conn.close()

    # Re-embed updated document in semantic vector space
    try:
        from app.vault.semantic import index_reel
        index_reel(shortcode, force=True)
    except Exception as e:
        logger.warning("Could not re-index semantic embedding for %s: %s", shortcode, e)

    return reel


# Automatically ensure tables and indexes are initialized on import
init_vault_db()
