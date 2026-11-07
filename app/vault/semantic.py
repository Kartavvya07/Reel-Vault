"""Slice 4 & 5: Natural-Language, Semantic and Hybrid Search Quality for Reel Vault."""

from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import re
import struct
from typing import Any, Dict, List, Optional, Tuple

from app.spike.config import settings
from app.vault.database import get_db_connection, _db_lock, _row_to_vault_reel
from app.vault.models import VaultReel, VaultStatus

logger = logging.getLogger(__name__)

RELEVANCE_CUTOFF = 0.30
SEM_BASELINE_GEMINI = 0.52
SEM_BASELINE_LOCAL = 0.20

# Query embedding cache in memory to prevent repeated API calls for identical queries
_QUERY_CACHE: Dict[str, Tuple[List[float], str, int]] = {}
FALLBACK_DIMENSIONS = 256

# Standard English stopwords to filter during search parsing and deterministic vectorization
STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "could",
    "did", "do", "does", "doing", "don", "down", "during", "each", "few", "for", "from", "further", "had",
    "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i",
    "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "myself", "no",
    "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "out", "over",
    "own", "same", "she", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "with", "you",
    "your", "ways", "way", "video", "videos", "reel", "reels", "find", "finds", "using", "like", "query", "queries"
}


def init_embeddings_db() -> None:
    """Creates the SQLite reel_embeddings table if not already present."""
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS reel_embeddings (
                    shortcode TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    embedding_blob BLOB NOT NULL,
                    embedding_model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_model ON reel_embeddings(embedding_model)")
            conn.commit()
        finally:
            conn.close()


def pack_vector(vec: List[float]) -> bytes:
    """Packs a float list into compact float32 binary blob."""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes, dim: int) -> List[float]:
    """Unpacks a float32 binary blob into a float list."""
    return list(struct.unpack(f"{dim}f", blob))


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Computes cosine similarity between two float vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    sim = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, (sim + 1.0) / 2.0 if sim < 0 else sim))


def deterministic_local_embed(text: str, dim: int = FALLBACK_DIMENSIONS) -> List[float]:
    """
    High-speed deterministic semantic projection vectorizer.
    Produces dense, cosine-normalized vectors based on token and subword n-gram hashing.
    Used for local testing, offline usage, and graceful fallback when Gemini API is unavailable.
    """
    clean_text = text.lower()
    raw_tokens = re.findall(r"[a-z0-9_-]+", clean_text)
    if not raw_tokens:
        return [0.0] * dim

    tokens = [t for t in raw_tokens if len(t) > 1 and t not in STOPWORDS]
    if not tokens:
        tokens = raw_tokens

    vec = [0.0] * dim

    for i, token in enumerate(tokens):
        # 1. Whole word feature
        h_word = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
        weight = 1.0 + (1.0 / (i + 1))  # Early title/takeaway words weighted slightly higher
        vec[h_word] += weight

        # 2. Subword n-grams for stemming and synonym alignment
        for n in (3, 4, 5):
            if len(token) >= n:
                for j in range(len(token) - n + 1):
                    sub = token[j : j + n]
                    h_sub = int(hashlib.md5(sub.encode("utf-8")).hexdigest(), 16) % dim
                    vec[h_sub] += 0.3

        # 3. Bigram feature for concept phrases (e.g. "doom scrolling", "front end")
        if i < len(tokens) - 1:
            bigram = f"{token}_{tokens[i+1]}"
            h_bi = int(hashlib.md5(bigram.encode("utf-8")).hexdigest(), 16) % dim
            vec[h_bi] += 0.8

    # L2 Normalization
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def get_embedding(text: str, is_query: bool = False) -> Tuple[List[float], str, int]:
    """
    Generates embedding using the configured model, with graceful local fallback.
    Returns (vector, model_name, dimensions).
    """
    clean = text.strip()
    if not clean:
        return [0.0] * FALLBACK_DIMENSIONS, "fallback-empty", FALLBACK_DIMENSIONS

    # Cache key depends on active model, task type, and clean text
    active_model = settings.gemini_embedding_model if settings.gemini_api_key else "local-semantic-embedder"
    cache_key = f"{active_model}:{is_query}:{clean}"
    if cache_key in _QUERY_CACHE:
        return _QUERY_CACHE[cache_key]

    # 1. Use configured Gemini Embedding Model if API key is present
    if settings.gemini_api_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=settings.gemini_api_key)
            model_id = settings.gemini_embedding_model
            config = types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
            )
            response = client.models.embed_content(
                model=model_id,
                contents=clean[:4000],  # Respect model context window
                config=config,
            )
            embedding_values = None
            if hasattr(response, "embeddings") and response.embeddings:
                embedding_values = response.embeddings[0].values
            elif hasattr(response, "embedding") and hasattr(response.embedding, "values"):
                embedding_values = response.embedding.values

            if embedding_values:
                vec = list(embedding_values)
                res = (vec, model_id, len(vec))
                _QUERY_CACHE[cache_key] = res
                return res
        except Exception as e:
            logger.warning(
                "Gemini embedding API call failed (%s); using local-semantic-embedder fallback: %s",
                settings.gemini_embedding_model,
                e,
            )
    else:
        logger.warning("No gemini_api_key configured; using local-semantic-embedder fallback.")

    # 2. Deterministic local embedder fallback
    local_vec = deterministic_local_embed(clean, dim=FALLBACK_DIMENSIONS)
    res = (local_vec, "local-semantic-embedder", FALLBACK_DIMENSIONS)
    _QUERY_CACHE[cache_key] = res
    return res


def build_searchable_document(row: Any) -> Tuple[str, str]:
    """
    Assembles clean indexed text fields without noisy boilerplate labels.
    Returns (document_text, sha256_content_hash).
    """
    parts = []

    def get_val(key):
        if hasattr(row, key):
            return getattr(row, key)
        try:
            return row[key]
        except (KeyError, TypeError):
            return None

    title = get_val("title")
    category = get_val("category")
    takeaway = get_val("takeaway")

    if title and title != "None":
        parts.append(title)
    if category and category != "None":
        parts.append(category)
    if takeaway and takeaway != "None":
        parts.append(takeaway)

    # Key points
    key_points_raw = get_val("key_points_json")
    if key_points_raw:
        try:
            pts = json.loads(key_points_raw) if isinstance(key_points_raw, str) else key_points_raw
            if pts:
                parts.append(" ".join(str(p) for p in pts))
        except Exception:
            pass

    # Tools
    tools_raw = get_val("tools_json")
    if tools_raw:
        try:
            tls = json.loads(tools_raw) if isinstance(tools_raw, str) else tools_raw
            tool_names = [t.get("name", "") if isinstance(t, dict) else str(t) for t in tls]
            if tool_names:
                parts.append(" ".join(tool_names))
        except Exception:
            pass

    # Tags
    tags_raw = get_val("tags_json")
    if tags_raw:
        try:
            tgs = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
            if tgs:
                parts.append(" ".join(str(t) for t in tgs))
        except Exception:
            pass

    # Transcript
    transcript = get_val("transcript")
    if transcript and transcript != "None":
        parts.append(transcript[:1200])

    # On-screen text
    on_screen_text = get_val("on_screen_text")
    if on_screen_text and on_screen_text != "None":
        parts.append(on_screen_text[:600])

    full_text = "\n".join(parts)
    content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    return full_text, content_hash


def index_reel(shortcode: str, force: bool = False) -> bool:
    """
    Generates and stores the semantic embedding for a single reel in SQLite.
    Avoids redundant API calls by verifying content hash.
    """
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM library_reels WHERE shortcode = ?", (shortcode,))
            row = cursor.fetchone()
            if not row:
                return False

            doc_text, content_hash = build_searchable_document(row)
            if not doc_text.strip():
                return False

            # Check if embedding already exists and is up to date
            if not force:
                cursor.execute(
                    "SELECT content_hash, embedding_model FROM reel_embeddings WHERE shortcode = ?",
                    (shortcode,),
                )
                existing = cursor.fetchone()
                target_model = settings.gemini_embedding_model if settings.gemini_api_key else "local-semantic-embedder"
                if existing and existing[0] == content_hash and existing[1] == target_model:
                    return False  # Already indexed and up to date, skip

            # Generate embedding as document
            vec, model_name, dim = get_embedding(doc_text, is_query=False)
            blob = pack_vector(vec)
            now_str = datetime.now(timezone.utc).isoformat()

            cursor.execute(
                """
                INSERT OR REPLACE INTO reel_embeddings (
                    shortcode, content_hash, embedding_blob, embedding_model, dimensions, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (shortcode, content_hash, blob, model_name, dim, now_str),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def index_all_reels(force: bool = False) -> Dict[str, int]:
    """
    Indexes all library reels into reel_embeddings.
    Idempotent: skips reels whose content hash is unchanged.
    """
    init_embeddings_db()
    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM library_reels")
            rows = cursor.fetchall()
        finally:
            conn.close()

    total = len(rows)
    indexed_count = 0
    skipped_count = 0
    errors_count = 0

    for r in rows:
        sc = r["shortcode"]
        try:
            did_index = index_reel(sc, force=force)
            if did_index:
                indexed_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            logger.warning("Error indexing reel %s: %s", sc, e)
            errors_count += 1

    return {
        "total": total,
        "indexed": indexed_count,
        "skipped": skipped_count,
        "errors": errors_count,
    }


def build_fts_query(clean_query: str) -> str:
    """
    Builds an FTS5 match expression combining exact phrase and content terms.
    Expands compound words and strips unhelpful stopwords.
    """
    tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9_-]+", clean_query)]
    content_tokens = [t for t in tokens if t not in STOPWORDS and len(t) > 1]
    if not content_tokens:
        content_tokens = [t for t in tokens if len(t) > 1]

    if not content_tokens:
        return ""

    fts_parts = []
    # 1. Whole phrase if 2+ content tokens
    if len(content_tokens) >= 2:
        phrase_str = " ".join(content_tokens)
        fts_parts.append(f'"{phrase_str}"')

    # 2. Individual content terms with prefix wildcard
    term_parts = []
    for t in content_tokens:
        term_parts.append(f'"{t}"*')
        # Subterms for common compound or inflected terms
        if "scroll" in t and t != "scroll":
            term_parts.append('"scroll"*')
        if "protein" in t and t != "protein":
            term_parts.append('"protein"*')
        if "animat" in t and t != "animat":
            term_parts.append('"animat"*')

    if term_parts:
        fts_parts.append(" OR ".join(term_parts))

    return " OR ".join(fts_parts)


def extract_relevance_snippet(reel: VaultReel, query_terms: List[str]) -> str:
    """Generates an informative contextual snippet explaining why the reel matched."""
    # 1. Check tools
    for tool in reel.tools:
        for t in query_terms:
            if t in tool.name.lower():
                return f"Tool match: {tool.name}"

    # 2. Check tags
    for tag in reel.tags:
        for t in query_terms:
            if t in tag.lower():
                return f"Tag match: #{tag}"

    # 3. Check key points
    for pt in reel.key_points:
        for t in query_terms:
            if t in pt.lower():
                idx = pt.lower().find(t)
                start = max(0, idx - 20)
                end = min(len(pt), idx + len(t) + 60)
                snippet = pt[start:end].strip()
                prefix = "..." if start > 0 else ""
                suffix = "..." if end < len(pt) else ""
                return f"Key point: {prefix}{snippet}{suffix}"

    # 4. Check takeaway
    if reel.takeaway:
        for t in query_terms:
            if t in reel.takeaway.lower():
                idx = reel.takeaway.lower().find(t)
                start = max(0, idx - 25)
                end = min(len(reel.takeaway), idx + len(t) + 60)
                snippet = reel.takeaway[start:end].strip()
                prefix = "..." if start > 0 else ""
                suffix = "..." if end < len(reel.takeaway) else ""
                return f"Takeaway: {prefix}{snippet}{suffix}"

    # 5. Check title
    if reel.title:
        for t in query_terms:
            if t in reel.title.lower():
                return f"Title: {reel.title}"

    # 6. Check transcript
    if reel.transcript:
        for t in query_terms:
            if t in reel.transcript.lower():
                idx = reel.transcript.lower().find(t)
                start = max(0, idx - 25)
                end = min(len(reel.transcript), idx + len(t) + 65)
                snippet = reel.transcript[start:end].strip()
                return f"Transcript: ...{snippet}..."

    # 7. Semantic / concept fallback
    if reel.takeaway:
        return f"Concept match: {reel.takeaway[:90]}..."
    return reel.short_summary or "Semantic concept match."


def search_natural_language(
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
    Executes hybrid search combining SQLite FTS5 BM25 keyword matching and vector cosine similarity.
    Supports sorting by 'relevance', 'newest', 'oldest', and 'title', while preserving category/status filters.
    When return_meta=True, returns (reels, total_matches, is_degraded, embedding_model).
    """
    init_embeddings_db()

    with _db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()

            # ------------------------------------------------------------------
            # 0. BLANK QUERY: Standard Chronological or Sorted Listing
            # ------------------------------------------------------------------
            if not query or not query.strip():
                where_clauses = []
                params: List[Any] = []
                if category and category.lower() != "all":
                    where_clauses.append("LOWER(category) = LOWER(?)")
                    params.append(category)
                if status and status.lower() != "all":
                    where_clauses.append("LOWER(status) = LOWER(?)")
                    params.append(status)
                if item_type == "reels":
                    where_clauses.append("path_type = 'reel'")
                elif item_type == "posts":
                    where_clauses.append("path_type IN ('post', 'p', 'tv')")
                if needs_note:
                    where_clauses.append("(takeaway IS NULL OR TRIM(takeaway) = '')")

                where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                cursor.execute(f"SELECT count(*) FROM library_reels {where_sql}", params)
                total = cursor.fetchone()[0]

                if sort == "oldest":
                    order_sql = "ORDER BY updated_at ASC"
                elif sort == "title":
                    order_sql = "ORDER BY LOWER(title) ASC"
                else:  # newest / default
                    order_sql = """
                        ORDER BY 
                            CASE WHEN status = 'processing' THEN 1 WHEN status = 'queued' THEN 2 ELSE 3 END,
                            updated_at DESC
                    """
                cursor.execute(f"SELECT * FROM library_reels {where_sql} {order_sql} LIMIT ? OFFSET ?", params + [limit, offset])
                reels = [_row_to_vault_reel(r) for r in cursor.fetchall()]

                active_model = settings.gemini_embedding_model if settings.gemini_api_key else "local-semantic-embedder"
                is_degraded = bool(not settings.gemini_api_key or settings.gemini_embedding_model == "local-semantic-embedder")

                if return_meta:
                    return reels, total, is_degraded, active_model
                return reels, total

            clean_query = query.strip()
            raw_terms = re.findall(r"[a-zA-Z0-9_-]+", clean_query.lower())
            query_terms = [t for t in raw_terms if len(t) > 1 and t not in STOPWORDS]
            if not query_terms:
                query_terms = [t for t in raw_terms if len(t) > 1]

            # ------------------------------------------------------------------
            # 1. KEYWORD SEARCH (SQLite FTS5 BM25 with column weights & LIKE fallback)
            # ------------------------------------------------------------------
            keyword_scores: Dict[str, float] = {}
            fts_query = build_fts_query(clean_query)

            if fts_query:
                try:
                    cursor.execute(
                        """
                        SELECT shortcode, bm25(library_reels_fts, 0.0, 10.0, 5.0, 4.0, 8.0, 2.0, 1.0, 1.5) as rank
                        FROM library_reels_fts 
                        WHERE library_reels_fts MATCH ?
                        ORDER BY rank ASC
                        """,
                        (fts_query,),
                    )
                    rows = cursor.fetchall()
                    if rows:
                        min_rank = min(r[1] for r in rows)
                        max_rank = max(r[1] for r in rows)
                        denom = (max_rank - min_rank) if max_rank != min_rank else 1.0
                        for r in rows:
                            # Inverted normalized score: 1.0 = best match
                            norm_score = 1.0 - ((r[1] - min_rank) / denom) * 0.45
                            keyword_scores[r[0]] = norm_score
                except Exception as fts_err:
                    logger.debug("FTS5 match syntax fallback to LIKE: %s", fts_err)

            # Fallback to LIKE if FTS yielded no results
            if not keyword_scores:
                for term in query_terms:
                    like_term = f"%{term}%"
                    cursor.execute(
                        """
                        SELECT shortcode FROM library_reels 
                        WHERE title LIKE ? OR takeaway LIKE ? OR tools_json LIKE ? OR tags_json LIKE ?
                        """,
                        (like_term, like_term, like_term, like_term),
                    )
                    for r in cursor.fetchall():
                        keyword_scores[r[0]] = max(keyword_scores.get(r[0], 0.0), 0.75)

            # ------------------------------------------------------------------
            # 2. SEMANTIC SEARCH (Vector Cosine Similarity & Calibration)
            # ------------------------------------------------------------------
            semantic_scores: Dict[str, float] = {}
            query_vec, model_name, query_dim = get_embedding(clean_query, is_query=True)
            is_degraded = bool(model_name == "local-semantic-embedder" or "fallback" in model_name)

            if query_vec and any(x != 0.0 for x in query_vec):
                cursor.execute(
                    "SELECT shortcode, embedding_blob, dimensions FROM reel_embeddings WHERE dimensions = ?",
                    (query_dim,),
                )
                embedding_rows = cursor.fetchall()
                is_gemini = not is_degraded
                sem_baseline = SEM_BASELINE_GEMINI if is_gemini else SEM_BASELINE_LOCAL
                sem_denom = (1.0 - sem_baseline) if sem_baseline < 1.0 else 1.0

                for sc, blob, dim in embedding_rows:
                    try:
                        reel_vec = unpack_vector(blob, dim)
                        sim = cosine_similarity(query_vec, reel_vec)
                        norm_sem = max(0.0, (sim - sem_baseline) / sem_denom)
                        if norm_sem > 0.02:
                            semantic_scores[sc] = norm_sem
                    except Exception:
                        continue

            # ------------------------------------------------------------------
            # 3. HYBRID SCORE FUSION, CALIBRATION & RELEVANCE CUTOFF
            # ------------------------------------------------------------------
            all_candidate_shortcodes = set(keyword_scores.keys()).union(set(semantic_scores.keys()))
            if not all_candidate_shortcodes:
                if return_meta:
                    return [], 0, is_degraded, model_name
                return [], 0

            placeholders = ",".join(["?"] * len(all_candidate_shortcodes))
            where_clauses = [f"shortcode IN ({placeholders})"]
            params = list(all_candidate_shortcodes)

            if category and category.lower() != "all":
                where_clauses.append("LOWER(category) = LOWER(?)")
                params.append(category)

            if status and status.lower() != "all":
                where_clauses.append("LOWER(status) = LOWER(?)")
                params.append(status)

            if item_type == "reels":
                where_clauses.append("path_type = 'reel'")
            elif item_type == "posts":
                where_clauses.append("path_type IN ('post', 'p', 'tv')")

            if needs_note:
                where_clauses.append("(takeaway IS NULL OR TRIM(takeaway) = '')")

            filter_sql = "WHERE " + " AND ".join(where_clauses)
            cursor.execute(f"SELECT * FROM library_reels {filter_sql}", params)
            candidate_rows = cursor.fetchall()

            scored_reels: List[Tuple[float, VaultReel]] = []
            effective_cutoff = RELEVANCE_CUTOFF if not is_degraded else 0.05

            for row in candidate_rows:
                reel = _row_to_vault_reel(row)
                sc = reel.shortcode

                sem_s = semantic_scores.get(sc, 0.0)
                kw_s = keyword_scores.get(sc, 0.0)

                # Determine match source and blended score
                if sem_s > 0.0 and kw_s > 0.0:
                    blended = (sem_s * 0.50) + (kw_s * 0.50)
                    match_source = "hybrid"
                elif sem_s > 0.0:
                    blended = sem_s * 0.90
                    match_source = "semantic"
                else:
                    blended = kw_s * 0.70
                    match_source = "keyword"

                # Favor completed/saved records with rich knowledge over unavailable/failed records
                if reel.status not in (VaultStatus.DONE.value, VaultStatus.SAVED.value):
                    blended *= 0.65

                # Drop weak or unrelated matches below relevance cutoff
                if blended < effective_cutoff:
                    continue

                snippet = extract_relevance_snippet(reel, query_terms)

                reel.relevance_score = round(blended, 3)
                reel.match_source = match_source
                reel.match_snippet = snippet

                scored_reels.append((blended, reel))

            if not scored_reels:
                if return_meta:
                    return [], 0, is_degraded, model_name
                return [], 0

            # ------------------------------------------------------------------
            # 4. SORTING & PAGINATION
            # ------------------------------------------------------------------
            if sort == "newest":
                scored_reels.sort(key=lambda x: x[1].created_at or x[1].updated_at or "", reverse=True)
            elif sort == "oldest":
                scored_reels.sort(key=lambda x: x[1].created_at or x[1].updated_at or "", reverse=False)
            elif sort == "title":
                scored_reels.sort(key=lambda x: (x[1].title or "").lower(), reverse=False)
            else:  # "relevance"
                scored_reels.sort(key=lambda x: x[0], reverse=True)

            total_matches = len(scored_reels)
            paged = [reel for _, reel in scored_reels[offset : offset + limit]]

            if return_meta:
                return paged, total_matches, is_degraded, model_name
            return paged, total_matches
        finally:
            conn.close()
