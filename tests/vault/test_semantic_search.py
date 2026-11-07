"""Tests for Slice 4: Natural-Language and Hybrid Semantic Search."""

import json
from unittest.mock import MagicMock, patch
import pytest
from starlette.testclient import TestClient

from app.spike.config import settings
from app.spike.main import app
from app.spike.schemas import ReelAnalysisOutput, ToolResource
from app.vault.database import (
    complete_reel,
    get_db_connection,
    init_vault_db,
    queue_reel,
    search_library,
)
from app.vault.semantic import (
    _QUERY_CACHE,
    build_searchable_document,
    cosine_similarity,
    deterministic_local_embed,
    get_embedding,
    index_all_reels,
    index_reel,
    init_embeddings_db,
    pack_vector,
    search_natural_language,
    unpack_vector,
)


@pytest.fixture(autouse=True)
def setup_test_vault_db(tmp_path):
    """Isolates each test in a clean SQLite database with vault & embedding schemas."""
    test_db = tmp_path / "test_semantic_vault.db"
    with patch.object(settings, "sqlite_db_file", test_db):
        init_vault_db()
        init_embeddings_db()
        yield test_db


def test_vector_packing_and_unpacking():
    """Verifies compact binary vector serialization and deserialization."""
    original = [0.123456, -0.654321, 0.999999, 0.0]
    blob = pack_vector(original)
    assert isinstance(blob, bytes)
    assert len(blob) == 4 * 4  # 4 floats * 4 bytes = 16 bytes

    unpacked = unpack_vector(blob, 4)
    for a, b in zip(original, unpacked):
        assert pytest.approx(a, rel=1e-5) == b


def test_cosine_similarity():
    """Tests exact angle distance calculations."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v2), rel=1e-5) == 1.0

    v3 = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v3), rel=1e-5) == 0.0

    v4 = [0.7071, 0.7071, 0.0]
    assert pytest.approx(cosine_similarity(v1, v4), rel=1e-3) == 0.7071


def test_deterministic_local_embed_properties():
    """Tests local fallback embedding generator vector size and L2 normalization."""
    vec1 = deterministic_local_embed("stop mindless doomscrolling on social media")
    assert len(vec1) == 256
    # Check L2 unit length
    magnitude = sum(x * x for x in vec1) ** 0.5
    assert pytest.approx(magnitude, rel=1e-4) == 1.0

    # Conceptual similarity between related phrases should be significantly positive
    vec2 = deterministic_local_embed("overcoming brainrot and endless phone scrolling")
    sim = cosine_similarity(vec1, vec2)
    assert sim > 0.15

    # Completely unrelated phrase
    vec_unrelated = deterministic_local_embed("high protein butter milk recipe chicken nutrition")
    sim_unrelated = cosine_similarity(vec1, vec_unrelated)
    assert sim > sim_unrelated


def test_index_reel_idempotency_and_hash_caching():
    """Tests that re-indexing identical content avoids redundant compute."""
    shortcode = "test_sc_semantic_1"
    reel, _, _ = queue_reel(
        original_url=f"https://www.instagram.com/reel/{shortcode}/",
        normalized_url=f"https://www.instagram.com/reel/{shortcode}/",
        shortcode=shortcode,
    )
    analysis = ReelAnalysisOutput(
        title="Stop Doomscrolling with Screen Zen",
        category="Productivity",
        short_summary="A guide on how to curb phone addiction using friction apps.",
        english_summary="A guide on how to curb phone addiction using friction apps.",
        takeaway="Use grayscale mode and friction apps to stop mindless phone addiction.",
        key_points=["Turn on iOS grayscale mode", "Set 15 min daily Instagram limits"],
        tools_and_resources=[ToolResource(name="Screen Zen", kind="tool")],
        tags=["screentime", "habits", "focus"],
        original_language="en",
        transcript="If you find yourself constantly doomscrolling late at night, try this simple friction trick.",
        on_screen_text="How to cure brainrot and stop scrolling",
    )
    complete_reel(shortcode, analysis)

    # First index: already indexed by complete_reel, but with force=True returns True
    indexed_first = index_reel(shortcode, force=True)
    assert indexed_first is True

    # Second index without changes and without force: should skip (content hash match)
    indexed_second = index_reel(shortcode, force=False)
    assert indexed_second is False


def test_direct_tool_and_keyword_query():
    """Tests exact tool name search query."""
    sc = "test_sc_tool_query"
    queue_reel(
        original_url=f"https://www.instagram.com/reel/{sc}/",
        normalized_url=f"https://www.instagram.com/reel/{sc}/",
        shortcode=sc,
    )
    analysis = ReelAnalysisOutput(
        title="Speed up front-end dev with Shadcn UI and Tailwind",
        category="Tech",
        short_summary="A walkthrough of Shadcn UI and copy-paste React components.",
        english_summary="A walkthrough of Shadcn UI and copy-paste React components.",
        takeaway="Copy paste beautiful accessible React components directly into your codebase.",
        key_points=["Zero npm package bloat", "Accessible Radix primitives"],
        tools_and_resources=[
            ToolResource(name="Shadcn UI", kind="library", url="https://ui.shadcn.com"),
            ToolResource(name="TailwindCSS", kind="framework"),
        ],
        tags=["webdev", "react", "frontend"],
        original_language="en",
        transcript="Why install heavy UI packages when you can copy Shadcn components?",
        on_screen_text="Shadcn UI component library",
    )
    complete_reel(sc, analysis)
    index_reel(sc, force=True)

    results, total = search_library(query="Shadcn UI")
    assert total >= 1
    found = next((r for r in results if r.shortcode == sc), None)
    assert found is not None
    assert found.relevance_score is not None
    assert found.relevance_score > 0.4
    assert found.match_snippet is not None
    assert "Shadcn" in found.match_snippet


def test_indirect_conceptual_query():
    """Tests semantic search finding a reel without exact keyword overlap."""
    sc = "test_sc_conceptual_query"
    queue_reel(
        original_url=f"https://www.instagram.com/reel/{sc}/",
        normalized_url=f"https://www.instagram.com/reel/{sc}/",
        shortcode=sc,
    )
    analysis = ReelAnalysisOutput(
        title="Curing Digital Brain Fog and Phone Addiction",
        category="Mental Health",
        short_summary="Techniques to reduce phone addiction and reclaim your focus.",
        english_summary="Techniques to reduce phone addiction and reclaim your focus.",
        takeaway="Place your phone in another room before sleep to eliminate obsessive feed refreshing.",
        key_points=["Digital sundown at 9pm", "Physical alarm clock replacement"],
        tools_and_resources=[],
        tags=["digitaldetox", "mindfulness"],
        original_language="en",
        transcript="Your attention span is fried because you wake up and instantly refresh social apps in bed.",
        on_screen_text="Break your evening screen addiction",
    )
    complete_reel(sc, analysis)
    index_reel(sc, force=True)

    # Query with synonyms / concepts: "doomscrolling late night"
    results, total = search_library(query="doomscrolling late night")
    found = next((r for r in results if r.shortcode == sc), None)
    assert found is not None
    assert found.relevance_score is not None
    assert found.match_source in ("semantic", "hybrid", "keyword")


def test_relevance_ranking_orders_best_match_first():
    """Tests that the most relevant reel ranks highest in the results."""
    sc_best = "test_sc_best_fit"
    sc_other = "test_sc_other_fit"

    # Best fit: cooking protein pancakes
    queue_reel(f"https://www.instagram.com/reel/{sc_best}/", f"https://www.instagram.com/reel/{sc_best}/", sc_best)
    complete_reel(
        sc_best,
        ReelAnalysisOutput(
            title="High Protein Pancake Recipe 40g Protein",
            category="Fitness",
            short_summary="A recipe for high-protein pancakes.",
            english_summary="A recipe for high-protein pancakes.",
            takeaway="Quick oat and whey protein pancakes ready in under 5 minutes.",
            key_points=["Oats, egg whites, whey isolate"],
            tools_and_resources=[],
            tags=["highprotein", "mealprep", "breakfast"],
            original_language="en",
            transcript="Here is how to make fluffy high protein pancakes for breakfast.",
            on_screen_text="40g Protein Breakfast Pancakes",
        ),
    )
    index_reel(sc_best, force=True)

    # Other fit: general fitness workout
    queue_reel(f"https://www.instagram.com/reel/{sc_other}/", f"https://www.instagram.com/reel/{sc_other}/", sc_other)
    complete_reel(
        sc_other,
        ReelAnalysisOutput(
            title="Leg Day Squat Technique Breakdown",
            category="Fitness",
            short_summary="Proper technique for leg squats.",
            english_summary="Proper technique for leg squats.",
            takeaway="Proper hip hinge prevents lower back fatigue during heavy squats after a breakfast workout.",
            key_points=["Knees tracking over toes"],
            tools_and_resources=[],
            tags=["squats", "workout"],
            original_language="en",
            transcript="Keep your chest up and brace your core throughout the eccentric motion.",
            on_screen_text="Squat form cues",
        ),
    )
    index_reel(sc_other, force=True)

    results, total = search_library(query="high protein breakfast pancake recipe")
    assert total >= 2
    # The pancake reel should rank ahead of the squat technique reel
    pancake_idx = next(i for i, r in enumerate(results) if r.shortcode == sc_best)
    squat_idx = next(i for i, r in enumerate(results) if r.shortcode == sc_other)
    assert pancake_idx < squat_idx


def test_missing_embeddings_keyword_fallback():
    """Verifies that reels without embeddings remain searchable via keyword fallback."""
    sc_no_emb = "test_sc_no_embeddings"
    queue_reel(
        original_url=f"https://www.instagram.com/reel/{sc_no_emb}/",
        normalized_url=f"https://www.instagram.com/reel/{sc_no_emb}/",
        shortcode=sc_no_emb,
    )
    analysis = ReelAnalysisOutput(
        title="Unembedded Legacy Reel on WebAssembly",
        category="Tech",
        short_summary="WebAssembly running Rust code.",
        english_summary="WebAssembly running Rust code.",
        takeaway="Compiling Rust code to WebAssembly modules for browser execution.",
        key_points=["Near native execution speed in the browser"],
        tools_and_resources=[ToolResource(name="Rust", kind="language")],
        tags=["wasm", "rust"],
        original_language="en",
        transcript="WebAssembly runs bytecode at near native speed in modern browser runtimes.",
        on_screen_text="Rust to WASM pipeline",
    )
    complete_reel(sc_no_emb, analysis)

    # Explicitly delete its embedding to simulate a record without embedding
    conn = get_db_connection()
    conn.cursor().execute("DELETE FROM reel_embeddings WHERE shortcode = ?", (sc_no_emb,))
    conn.commit()
    conn.close()

    # Keyword search must still find it via FTS5
    results, total = search_library(query="WebAssembly Rust")
    found = next((r for r in results if r.shortcode == sc_no_emb), None)
    assert found is not None
    assert found.match_source == "keyword"
    assert found.relevance_score is not None


def test_empty_results_and_no_match():
    """Tests that nonsensical queries return empty results gracefully."""
    results, total = search_library(query="xyzqwerty98765nonsensephrase123")
    assert total == 0
    assert len(results) == 0


def test_hybrid_search_with_category_and_status_filters():
    """Tests that category and status filters are respected during natural-language search."""
    sc_tech = "test_sc_filter_tech"
    sc_food = "test_sc_filter_food"

    queue_reel(f"https://www.instagram.com/reel/{sc_tech}/", f"https://www.instagram.com/reel/{sc_tech}/", sc_tech)
    complete_reel(
        sc_tech,
        ReelAnalysisOutput(
            title="Fast SQLite Search Engine Tips",
            category="Software Development",
            short_summary="Guide to SQLite FTS5 search.",
            english_summary="Guide to SQLite FTS5 search.",
            takeaway="Use SQLite FTS5 extension with BM25 ranking for local text indexing.",
            key_points=["Create virtual table using FTS5"],
            tools_and_resources=[ToolResource(name="SQLite", kind="database")],
            tags=["sqlite", "indexing"],
            original_language="en",
            transcript="SQLite FTS5 virtual tables provide full-text search without external servers.",
            on_screen_text="SQLite full text search",
        ),
    )
    index_reel(sc_tech, force=True)

    queue_reel(f"https://www.instagram.com/reel/{sc_food}/", f"https://www.instagram.com/reel/{sc_food}/", sc_food)
    complete_reel(
        sc_food,
        ReelAnalysisOutput(
            title="Search for the Best Local Coffee Roasters",
            category="Food & Drink",
            short_summary="How to find fresh espresso beans.",
            english_summary="How to find fresh espresso beans.",
            takeaway="How to evaluate espresso bean origin and roast dates.",
            key_points=["Single origin vs blend"],
            tools_and_resources=[],
            tags=["coffee", "espresso"],
            original_language="en",
            transcript="When you search for specialty coffee beans, always check roast date.",
            on_screen_text="Espresso bean freshness",
        ),
    )
    index_reel(sc_food, force=True)

    # Search with category filter: "Software Development"
    results, total = search_library(query="search", category="Software Development")
    assert any(r.shortcode == sc_tech for r in results)
    assert not any(r.shortcode == sc_food for r in results)


def test_index_embeddings_api_endpoint():
    """Tests POST /api/vault/index-embeddings authorization and execution."""
    client = TestClient(app)
    token = "secret_vault_token_42"

    with patch.object(settings, "vault_auth_token", token):
        # 1. Unauthenticated request without token fails with 401
        unauth_resp = client.post("/api/vault/index-embeddings")
        assert unauth_resp.status_code == 401

        # 2. Authenticated request with Bearer token succeeds
        auth_resp = client.post(
            "/api/vault/index-embeddings",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert auth_resp.status_code == 200
        data = auth_resp.json()
        assert data["status"] == "ok"
        assert "result" in data
        assert "total" in data["result"]
        assert "indexed" in data["result"]


def test_real_gemini_embeddings_active():
    """Fails if real Gemini embeddings are not selected when API key is configured."""
    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.embeddings = [MagicMock(values=[0.05] * 3072)]
        mock_client.models.embed_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with patch.object(settings, "gemini_api_key", "test-api-key"), \
             patch.object(settings, "gemini_embedding_model", "gemini-embedding-001"):
            _QUERY_CACHE.clear()
            vec, model_name, dim = get_embedding("natural language search query", is_query=True)

            assert model_name == "gemini-embedding-001"
            assert dim == 3072
            assert len(vec) == 3072
            assert model_name != "local-semantic-embedder"


def test_gemini_embedding_fallback_when_no_key():
    """Verifies that local embedder is selected when no key is present."""
    with patch.object(settings, "gemini_api_key", None):
        _QUERY_CACHE.clear()
        vec, model_name, dim = get_embedding("test query text", is_query=True)
        assert model_name == "local-semantic-embedder"
        assert dim == 256
        assert len(vec) == 256


def test_relevance_cutoff_drops_nonsense_queries():
    """Verifies that nonsense queries below the relevance cutoff return 0 matches."""
    sc = "test_sc_cutoff_check"
    queue_reel(f"https://www.instagram.com/reel/{sc}/", f"https://www.instagram.com/reel/{sc}/", sc)
    complete_reel(
        sc,
        ReelAnalysisOutput(
            title="Understanding Advanced Operating Systems",
            category="Computer Science",
            short_summary="Virtual memory and paging systems.",
            english_summary="Virtual memory and paging systems.",
            takeaway="How CPU MMUs translate virtual addresses into physical addresses.",
            key_points=["Page tables and TLB cache"],
            tools_and_resources=[],
            tags=["os", "kernel"],
            original_language="en",
            transcript="Virtual memory maps process address spaces using paging and page replacement algorithms.",
            on_screen_text="Paging and TLB architecture",
        ),
    )
    index_reel(sc, force=True)

    # Nonsense query with no overlap and zero semantic relation
    results, total = search_library(query="zxcvbnm qazwsx nonsensephrase 99999")
    assert total == 0
    assert len(results) == 0


@pytest.mark.skipif(not settings.gemini_api_key, reason="Requires live GEMINI_API_KEY")
def test_live_gemini_embeddings_call():
    """Verifies actual Gemini embedding API responds with 3072 dims when live key is set."""
    _QUERY_CACHE.clear()
    vec, model_name, dim = get_embedding("repo for parsing pdfs", is_query=True)
    assert model_name == "gemini-embedding-001"
    assert dim == 3072
    assert len(vec) == 3072
    assert any(x != 0.0 for x in vec)
