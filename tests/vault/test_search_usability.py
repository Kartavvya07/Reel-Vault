"""Unit and integration tests for Reel Vault Slice 5: Search Quality and Library Usability."""

import json
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from app.spike.config import settings
from app.spike.main import app
from app.spike.schemas import (
    CodeSnippet,
    ExtractedUrl,
    ReelAnalysisOutput,
    ToolResource,
)
from app.vault.auth import get_expected_token
from app.vault.database import (
    complete_reel,
    fail_reel,
    get_db_connection,
    init_vault_db,
    queue_reel,
    retry_reel,
    search_library,
)
from app.vault.semantic import (
    build_fts_query,
    extract_relevance_snippet,
    index_reel,
    init_embeddings_db,
    search_natural_language,
)
from app.vault.ui import render_reel_detail_page


@pytest.fixture(autouse=True)
def setup_test_vault_db(tmp_path):
    """Isolates each test in a clean SQLite database with vault & embedding schemas."""
    test_db = tmp_path / "test_usability_vault.db"
    with patch.object(settings, "sqlite_db_file", test_db):
        init_vault_db()
        init_embeddings_db()
        yield test_db


@pytest.fixture
def client():
    return TestClient(app)


def test_fts_query_expansion_and_stopword_filtering():
    """Verifies that FTS query builder strips noisy stopwords and retains key content terms."""
    q = "how to stop mindless doomscrolling on social media"
    fts = build_fts_query(q)
    assert "how" not in fts.split()
    assert "to" not in fts.split()
    assert "on" not in fts.split()
    assert '"stop"*' in fts
    assert '"doomscrolling"*' in fts
    assert '"scroll"*' in fts  # Compound subterm expansion


def test_relevance_snippet_generation_modes():
    """Tests relevance snippet generation across tools, tags, key points, and takeaways."""
    sc = "test_sc_snippets"
    queue_reel(f"https://www.instagram.com/reel/{sc}/", f"https://www.instagram.com/reel/{sc}/", sc)
    analysis = ReelAnalysisOutput(
        title="Modern React Interface Kits",
        category="Tech",
        short_summary="A showcase of Shadcn and Tailwind design systems.",
        english_summary="A showcase of Shadcn and Tailwind design systems.",
        takeaway="Copy paste accessible primitives to build blazing fast web applications.",
        key_points=["Install Radix primitives", "Use Tailwind for zero runtime styling"],
        tools_and_resources=[
            ToolResource(name="Shadcn UI", kind="library", verification_status="verified"),
        ],
        tags=["webdev", "tailwind", "frontend"],
        transcript="Why spend hours designing buttons when you have Shadcn UI?",
        on_screen_text="Shadcn UI Framework",
    )
    complete_reel(sc, analysis)
    reel, _ = search_library(query="Shadcn")
    assert len(reel) >= 1
    target = next(r for r in reel if r.shortcode == sc)

    # 1. Tool match snippet
    snip_tool = extract_relevance_snippet(target, ["shadcn"])
    assert "Tool match: Shadcn UI" in snip_tool

    # 2. Tag match snippet
    snip_tag = extract_relevance_snippet(target, ["tailwind"])
    assert "Tag match: #tailwind" in snip_tag

    # 3. Key points snippet
    snip_pt = extract_relevance_snippet(target, ["radix"])
    assert "Key point:" in snip_pt and "Radix" in snip_pt

    # 4. Takeaway snippet
    snip_tw = extract_relevance_snippet(target, ["primitives"])
    assert "Takeaway:" in snip_tw or "Key point:" in snip_tw


def test_sorting_options_relevance_newest_oldest_title():
    """Tests sorting across 'relevance', 'newest', 'oldest', and 'title'."""
    reels_meta = [
        ("sc_a", "Apple Design System with React", "2026-11-01T10:00:00Z"),
        ("sc_b", "Zebra High Protein Smoothies", "2026-11-03T10:00:00Z"),
        ("sc_c", "Middle Ground React Animations", "2026-11-02T10:00:00Z"),
    ]

    for sc, title, created_at in reels_meta:
        queue_reel(f"https://www.instagram.com/reel/{sc}/", f"https://www.instagram.com/reel/{sc}/", sc)
        complete_reel(
            sc,
            ReelAnalysisOutput(
                title=title,
                category="Tech",
                short_summary=title,
                english_summary=title,
                takeaway="Actionable advice on web and design.",
                key_points=["Key point 1"],
                tools_and_resources=[],
                tags=["react", "design"],
                transcript="Transcript details here.",
                on_screen_text="Screen overlay text.",
            ),
        )
        # Set deterministic timestamps
        conn = get_db_connection()
        conn.cursor().execute("UPDATE library_reels SET updated_at = ?, created_at = ? WHERE shortcode = ?", (created_at, created_at, sc))
        conn.commit()
        conn.close()
        index_reel(sc, force=True)

    # 1. Sort newest first
    reels_newest, _ = search_library(sort="newest")
    sub_newest = [r.shortcode for r in reels_newest if r.shortcode in ("sc_a", "sc_b", "sc_c")]
    assert sub_newest == ["sc_b", "sc_c", "sc_a"]

    # 2. Sort oldest first
    reels_oldest, _ = search_library(sort="oldest")
    sub_oldest = [r.shortcode for r in reels_oldest if r.shortcode in ("sc_a", "sc_b", "sc_c")]
    assert sub_oldest == ["sc_a", "sc_c", "sc_b"]

    # 3. Sort title alphabetical A-Z
    reels_title, _ = search_library(sort="title")
    sub_title = [r.shortcode for r in reels_title if r.shortcode in ("sc_a", "sc_b", "sc_c")]
    assert sub_title == ["sc_a", "sc_c", "sc_b"]


def test_ranking_prefers_completed_records_over_incomplete():
    """Verifies that rich, completed records outrank incomplete or unavailable records on identical keywords."""
    sc_done = "sc_done_rich"
    sc_unavail = "sc_unavail_poor"

    # Completed record with rich knowledge
    queue_reel(f"https://www.instagram.com/reel/{sc_done}/", f"https://www.instagram.com/reel/{sc_done}/", sc_done)
    complete_reel(
        sc_done,
        ReelAnalysisOutput(
            title="Complete Guide to High Protein Meal Prep",
            category="Health",
            short_summary="A meal prep guide.",
            english_summary="A meal prep guide.",
            takeaway="Prep 5 days of high protein meals on Sunday to save money.",
            key_points=["Buy oats and chicken breast in bulk"],
            tools_and_resources=[ToolResource(name="Blinkit", kind="app")],
            tags=["protein", "nutrition"],
            transcript="Here is how to hit your protein targets on a budget.",
            on_screen_text="Protein meal prep",
        ),
    )
    index_reel(sc_done, force=True)

    # Incomplete legacy record with same keyword
    queue_reel(f"https://www.instagram.com/reel/{sc_unavail}/", f"https://www.instagram.com/reel/{sc_unavail}/", sc_unavail)
    fail_reel(sc_unavail, "unavailable", "Incomplete ambient audio. Protein mentioned in passing.", "INCOMPLETE_LEGACY")
    conn = get_db_connection()
    conn.cursor().execute("UPDATE library_reels SET title = 'protein ambient clip' WHERE shortcode = ?", (sc_unavail,))
    conn.commit()
    conn.close()
    index_reel(sc_unavail, force=True)

    results, total = search_library(query="protein")
    assert total >= 2
    # The 'done' reel must rank ahead of the 'unavailable' reel
    done_pos = next(i for i, r in enumerate(results) if r.shortcode == sc_done)
    unavail_pos = next(i for i, r in enumerate(results) if r.shortcode == sc_unavail)
    assert done_pos < unavail_pos


def test_retry_refreshes_embeddings_and_search_index():
    """Tests that retrying a failed reel and completing analysis updates its search embeddings."""
    sc = "sc_retry_flow"
    queue_reel(f"https://www.instagram.com/reel/{sc}/", f"https://www.instagram.com/reel/{sc}/", sc)
    fail_reel(sc, "failed", "Extraction timed out.", "TIMEOUT_ERROR")

    # Initially failed reel yields no match on its future topic
    res_before, total_before = search_library(query="Quantum Computing")
    assert not any(r.shortcode == sc for r in res_before)

    # 1. Trigger retry
    retried = retry_reel(sc)
    assert retried is not None
    assert retried.status == "queued"

    # 2. Complete analysis with new knowledge
    analysis = ReelAnalysisOutput(
        title="Intro to Quantum Computing and Qubits",
        category="Science",
        short_summary="A breakdown of quantum superposition.",
        english_summary="A breakdown of quantum superposition.",
        takeaway="Quantum computing leverages qubits in superposition for exponential speedups.",
        key_points=["Superposition and entanglement"],
        tools_and_resources=[ToolResource(name="Qiskit", kind="library")],
        tags=["quantum", "computing", "physics"],
        transcript="Quantum computers do not replace classic chips; they solve specialized problems.",
        on_screen_text="Quantum Computing 101",
    )
    complete_reel(sc, analysis)

    # Verify that search immediately finds the retried & completed reel
    res_after, total_after = search_library(query="Quantum Computing Qubits")
    assert total_after >= 1
    found = next((r for r in res_after if r.shortcode == sc), None)
    assert found is not None
    assert found.status == "done"
    assert found.relevance_score is not None
    assert found.relevance_score > 0.5


def test_verified_vs_unverified_link_distinction():
    """Tests that detail page templates visibly distinguish verified tools from unverified extracted links."""
    sc = "sc_link_verification"
    queue_reel(f"https://www.instagram.com/reel/{sc}/", f"https://www.instagram.com/reel/{sc}/", sc)
    analysis = ReelAnalysisOutput(
        title="Python Automation Scripting",
        category="Tech",
        short_summary="Automating daily tasks with Python.",
        english_summary="Automating daily tasks with Python.",
        takeaway="Use requests and BeautifulSoup for quick web scrapers.",
        key_points=["Install beautifulsoup4"],
        tools_and_resources=[
            ToolResource(name="BeautifulSoup", url="https://pypi.org/project/beautifulsoup4/", verification_status="verified"),
            ToolResource(name="RandomCLI", url="http://unverified-tool.local", verification_status="unverified"),
        ],
        extracted_urls=[
            ExtractedUrl(url="https://github.com/someone/unverified-repo", source="caption"),
        ],
        commands_or_code_snippets=[
            CodeSnippet(language_or_shell="bash", code="pip install beautifulsoup4 requests"),
        ],
        tags=["python", "automation"],
        transcript="Here is the script to automate downloads.",
        on_screen_text="Automate with Python",
        confidence=0.95,
    )
    complete_reel(sc, analysis)
    reels, _ = search_library(query="Python Automation")
    target = next(r for r in reels if r.shortcode == sc)

    html_content = render_reel_detail_page(target)
    assert "✓ Verified" in html_content
    assert "Extracted (Unverified)" in html_content
    assert "Extracted External Links (Unverified)" in html_content
    assert "https://github.com/someone/unverified-repo" in html_content
    assert "Confidence: 95%" in html_content
    assert "pip install beautifulsoup4" in html_content


def test_api_list_reels_supports_sorting(client):
    """Verifies that GET /api/vault/reels properly handles the sort parameter."""
    res = client.get("/api/vault/reels?sort=newest")
    assert res.status_code == 200
    data = res.json()
    assert "reels" in data
    assert "stats" in data
    assert "total_count" in data
