"""Storage management: JSONL logging, SQLite indexing, and temp media cleanup."""

import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import List, Optional

from app.spike.config import settings
from app.spike.schemas import ExperimentResult

logger = logging.getLogger(__name__)

_file_lock = threading.Lock()
_db_lock = threading.Lock()


def init_sqlite_db() -> None:
    """Initialize SQLite database schema if not already created."""
    with _db_lock:
        conn = sqlite3.connect(settings.sqlite_db_file)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    shortcode TEXT NOT NULL,
                    original_url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    path_type TEXT NOT NULL,
                    attempt_timestamp TEXT NOT NULL,
                    ingestion_provider TEXT NOT NULL,
                    fetch_status TEXT NOT NULL,
                    metadata_success INTEGER NOT NULL,
                    caption_success INTEGER NOT NULL,
                    media_retrieval_success INTEGER NOT NULL,
                    ai_processing_success INTEGER NOT NULL,
                    output_validation_success INTEGER NOT NULL,
                    end_to_end_success INTEGER NOT NULL,
                    caption_only_fallback_used INTEGER NOT NULL,
                    failure_category TEXT NOT NULL,
                    error_message TEXT,
                    processing_duration_seconds REAL NOT NULL,
                    input_token_count INTEGER,
                    output_token_count INTEGER,
                    estimated_cost_usd REAL NOT NULL,
                    analysis_output_json TEXT,
                    raw_metadata_json TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_shortcode ON experiments(shortcode)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fetch_status ON experiments(fetch_status)")
            conn.commit()
        finally:
            conn.close()


def save_experiment_result(result: ExperimentResult) -> None:
    """
    Persists an experiment result to both results.jsonl and experiments.db.
    Thread-safe and atomic.
    """
    json_line = result.model_dump_json()

    # 1. Append to JSONL file
    with _file_lock:
        with open(settings.results_file, "a", encoding="utf-8") as f:
            f.write(json_line + "\n")

    # 2. Insert or replace in SQLite
    with _db_lock:
        conn = sqlite3.connect(settings.sqlite_db_file)
        try:
            cursor = conn.cursor()
            analysis_json = result.analysis_output.model_dump_json() if result.analysis_output else None
            raw_meta_json = json.dumps(result.raw_metadata) if result.raw_metadata else None

            cursor.execute(
                """
                INSERT OR REPLACE INTO experiments (
                    experiment_id, shortcode, original_url, normalized_url, path_type,
                    attempt_timestamp, ingestion_provider, fetch_status,
                    metadata_success, caption_success, media_retrieval_success,
                    ai_processing_success, output_validation_success, end_to_end_success,
                    caption_only_fallback_used, failure_category, error_message,
                    processing_duration_seconds, input_token_count, output_token_count,
                    estimated_cost_usd, analysis_output_json, raw_metadata_json, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.experiment_id,
                    result.shortcode,
                    result.original_url,
                    result.normalized_url,
                    result.path_type,
                    result.attempt_timestamp,
                    result.ingestion_provider,
                    result.fetch_status.value,
                    1 if result.metadata_success else 0,
                    1 if result.caption_success else 0,
                    1 if result.media_retrieval_success else 0,
                    1 if result.ai_processing_success else 0,
                    1 if result.output_validation_success else 0,
                    1 if result.end_to_end_success else 0,
                    1 if result.caption_only_fallback_used else 0,
                    result.failure_category.value,
                    result.error_message,
                    result.processing_duration_seconds,
                    result.input_token_count,
                    result.output_token_count,
                    result.estimated_cost_usd,
                    analysis_json,
                    raw_meta_json,
                    result.retry_count,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def get_experiment_result(experiment_id: str) -> Optional[ExperimentResult]:
    """Retrieve a single experiment result by ID from SQLite."""
    with _db_lock:
        conn = sqlite3.connect(settings.sqlite_db_file)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return _row_to_experiment_result(row)
        finally:
            conn.close()


def get_all_experiment_results() -> List[ExperimentResult]:
    """
    Retrieve all experiment records.
    Reads from SQLite to ensure accurate transactions, or falls back to JSONL.
    """
    results: List[ExperimentResult] = []
    with _db_lock:
        if settings.sqlite_db_file.exists():
            conn = sqlite3.connect(settings.sqlite_db_file)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM experiments ORDER BY attempt_timestamp ASC")
                for row in cursor.fetchall():
                    results.append(_row_to_experiment_result(row))
                if results:
                    return results
            except Exception as e:
                logger.warning("Error querying SQLite database: %s", e)
            finally:
                conn.close()

    # Fallback to JSONL file if SQLite had no rows
    if settings.results_file.exists():
        with _file_lock:
            with open(settings.results_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            results.append(ExperimentResult.model_validate_json(line))
                        except Exception as e:
                            logger.warning("Failed parsing jsonl line: %s", e)
    return results


def _row_to_experiment_result(row: sqlite3.Row) -> ExperimentResult:
    """Helper to convert an SQLite row to an ExperimentResult schema."""
    analysis_data = json.loads(row["analysis_output_json"]) if row["analysis_output_json"] else None
    raw_meta = json.loads(row["raw_metadata_json"]) if row["raw_metadata_json"] else None

    return ExperimentResult(
        experiment_id=row["experiment_id"],
        shortcode=row["shortcode"],
        original_url=row["original_url"],
        normalized_url=row["normalized_url"],
        path_type=row["path_type"],
        attempt_timestamp=row["attempt_timestamp"],
        ingestion_provider=row["ingestion_provider"],
        fetch_status=row["fetch_status"],
        metadata_success=bool(row["metadata_success"]),
        caption_success=bool(row["caption_success"]),
        media_retrieval_success=bool(row["media_retrieval_success"]),
        ai_processing_success=bool(row["ai_processing_success"]),
        output_validation_success=bool(row["output_validation_success"]),
        end_to_end_success=bool(row["end_to_end_success"]),
        caption_only_fallback_used=bool(row["caption_only_fallback_used"]),
        failure_category=row["failure_category"],
        error_message=row["error_message"],
        processing_duration_seconds=float(row["processing_duration_seconds"]),
        input_token_count=row["input_token_count"],
        output_token_count=row["output_token_count"],
        estimated_cost_usd=float(row["estimated_cost_usd"]),
        analysis_output=analysis_data,
        raw_metadata=raw_meta,
        retry_count=int(row["retry_count"]),
    )


def cleanup_temp_media(target: Optional[str | Path] = None) -> None:
    """
    Remove temporary media files.
    If target is a specific path or prefix, removes matching files.
    If target is None, removes all files in temp_media_dir.
    """
    try:
        if not settings.temp_media_dir.exists():
            return

        import shutil
        if target is not None:
            p = Path(target)
            if not p.is_absolute():
                p = settings.temp_media_dir / p
            if p.is_dir():
                try:
                    shutil.rmtree(p, ignore_errors=True)
                    logger.debug("Cleaned up temp media directory: %s", p)
                    return
                except Exception as e:
                    logger.warning("Could not delete temp directory %s: %s", p, e)
                    return
            if p.is_file():
                try:
                    p.unlink(missing_ok=True)
                    logger.debug("Cleaned up temp media file: %s", p)
                    return
                except Exception as e:
                    logger.warning("Could not delete temp file %s: %s", p, e)
                    return

            raw_name = p.name
            pattern = f"{raw_name}*" if not raw_name.endswith("*") else raw_name
        else:
            pattern = "*"

        for item in settings.temp_media_dir.glob(pattern):
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                    logger.debug("Cleaned up temp media directory: %s", item)
                elif item.is_file():
                    item.unlink(missing_ok=True)
                    logger.debug("Cleaned up temp media file: %s", item)
            except Exception as e:
                logger.warning("Could not delete temp item %s: %s", item, e)
    except Exception as cleanup_err:
        logger.warning("Error during media cleanup for target %s: %s", target, cleanup_err)


init_sqlite_db()
