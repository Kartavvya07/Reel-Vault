import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.spike.config import SpikeSettings
from app.spike.main import validate_startup_pipeline_safety
from app.vault.models import VaultReel, VaultStatus
from app.vault.ui import render_main_vault_page, render_reel_detail_page


def test_refuse_start_on_railway_with_mock_pipeline_and_no_allow_mock(monkeypatch):
    """Application must refuse to start on Railway when real pipeline is disabled without ALLOW_MOCK=true."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_REAL_PIPELINE", "false")
    monkeypatch.setenv("ALLOW_MOCK", "false")

    test_settings = SpikeSettings()
    assert test_settings.is_railway is True
    assert test_settings.enable_real_pipeline is False
    assert test_settings.allow_mock is False

    with pytest.raises(RuntimeError) as exc_info:
        validate_startup_pipeline_safety(test_settings)
    assert "Refusing to start on Railway" in str(exc_info.value)
    assert "ALLOW_MOCK=true" in str(exc_info.value)


def test_allow_start_on_railway_when_allow_mock_is_set(monkeypatch):
    """Application may start on Railway with mock pipeline ONLY when ALLOW_MOCK=true is explicitly set."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_REAL_PIPELINE", "false")
    monkeypatch.setenv("ALLOW_MOCK", "true")

    test_settings = SpikeSettings()
    assert test_settings.is_railway is True
    assert test_settings.enable_real_pipeline is False
    assert test_settings.allow_mock is True

    # Should not raise
    validate_startup_pipeline_safety(test_settings)


def test_allow_start_on_railway_when_real_pipeline_enabled(monkeypatch):
    """Application starts normally on Railway when ENABLE_REAL_PIPELINE is true."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_REAL_PIPELINE", "true")
    monkeypatch.setenv("ALLOW_MOCK", "false")

    test_settings = SpikeSettings()
    assert test_settings.is_railway is True
    assert test_settings.enable_real_pipeline is True

    # Should not raise
    validate_startup_pipeline_safety(test_settings)


def test_allow_start_locally_without_railway(monkeypatch):
    """Local environments (dev/test) are not blocked even if mock pipeline is used."""
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.delenv("RAILWAY_SERVICE_ID", raising=False)
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    monkeypatch.delenv("IS_RAILWAY", raising=False)
    monkeypatch.setenv("ENABLE_REAL_PIPELINE", "false")
    monkeypatch.setenv("ALLOW_MOCK", "false")

    test_settings = SpikeSettings()
    assert test_settings.is_railway is False

    # Should not raise
    validate_startup_pipeline_safety(test_settings)


def test_mock_mode_banner_rendered_when_pipeline_disabled(monkeypatch):
    """Red MOCK MODE banner is visible in the UI when ENABLE_REAL_PIPELINE=false."""
    monkeypatch.setenv("ENABLE_REAL_PIPELINE", "false")
    with patch("app.vault.ui.settings.enable_real_pipeline", False):
        html = render_main_vault_page()
        assert 'id="mockModeBanner"' in html
        assert "MOCK MODE ACTIVE:" in html
        assert 'class=" mb-6 p-4 rounded-2xl bg-rose-500/15' in html or 'class="mb-6 p-4 rounded-2xl bg-rose-500/15' in html or 'hidden' not in html.split('id="mockModeBanner"')[1].split('>')[0]


def test_mock_mode_banner_hidden_when_pipeline_enabled():
    """Red MOCK MODE banner is hidden when ENABLE_REAL_PIPELINE=true."""
    with patch("app.vault.ui.settings.enable_real_pipeline", True):
        html = render_main_vault_page()
        mock_tag = html.split('id="mockModeBanner"')[1].split('>')[0]
        assert "hidden" in mock_tag


def test_mock_result_badge_on_detail_page():
    """A reel produced with mock analysis renders a distinct 🧪 Mock Data badge."""
    mock_reel = VaultReel(
        shortcode="MOCK_REEL_1",
        original_url="https://www.instagram.com/reel/MOCK_REEL_1/",
        normalized_url="https://www.instagram.com/reel/MOCK_REEL_1/",
        created_at="2026-11-07T12:00:00Z",
        updated_at="2026-11-07T12:00:00Z",
        status=VaultStatus.DONE.value,
        title="Knowledge Insights: MOCK_REEL_1",
        analysis_mode="mock",
    )
    html = render_reel_detail_page(mock_reel)
    assert "🧪 Mock Data" in html

