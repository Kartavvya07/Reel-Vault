"""Unit and integration tests for Reel Vault Slice 3: Android PWA & Web Share Target."""

from pathlib import Path
import pytest
from starlette.testclient import TestClient
from unittest.mock import patch

from app.spike.config import settings
from app.spike.main import app
from app.vault.database import get_reel_by_shortcode, init_vault_db
from app.vault.pwa import extract_instagram_url_from_share


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Isolate each test in a clean SQLite database."""
    test_db = tmp_path / "test_pwa_vault.db"
    with patch.object(settings, "sqlite_db_file", test_db):
        init_vault_db()
        yield test_db


@pytest.fixture
def client():
    return TestClient(app)


def test_manifest_validity(client):
    """Verifies that the Web App Manifest meets Android PWA installability requirements."""
    res = client.get("/manifest.json")
    assert res.status_code == 200
    data = res.json()

    assert data["name"] == "Reel Vault — Personal Knowledge Library"
    assert data["short_name"] == "Reel Vault"
    assert data["display"] == "standalone"
    assert data["start_url"] == "/"
    assert data["theme_color"] == "#080B12"

    # Verify icons
    assert len(data["icons"]) >= 3
    icon_sizes = [i["sizes"] for i in data["icons"]]
    assert "192x192" in icon_sizes
    assert "512x512" in icon_sizes

    # Verify Web Share Target configuration
    share_target = data["share_target"]
    assert share_target["action"] == "/share-target"
    assert share_target["method"].upper() == "POST"
    assert "url" in share_target["params"]
    assert "text" in share_target["params"]
    assert "title" in share_target["params"]


def test_service_worker_script(client):
    """Verifies that the service worker script is served with correct headers."""
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert "application/javascript" in res.headers["content-type"]
    assert "addEventListener('install'" in res.text
    assert "addEventListener('fetch'" in res.text


def test_url_extraction_various_instagram_formats():
    """Tests extraction across diverse real-world Android share sheet payloads."""
    # 1. Standard pure url
    u1 = extract_instagram_url_from_share(url="https://www.instagram.com/reel/DABCD1234/?igsh=MWxyz123")
    assert u1 == "https://www.instagram.com/reel/DABCD1234/?igsh=MWxyz123"

    # 2. Instagram app sharing text with URL embedded
    u2 = extract_instagram_url_from_share(
        title="Instagram",
        text="Check out this Reel by @creator: https://www.instagram.com/reel/DXYZ98765/! Inspiring.",
    )
    assert u2 == "https://www.instagram.com/reel/DXYZ98765/"

    # 3. Post URL (/p/) preservation
    u3 = extract_instagram_url_from_share(text="https://www.instagram.com/p/DPOST12345/?utm_medium=copy_link")
    assert u3 == "https://www.instagram.com/p/DPOST12345/?utm_medium=copy_link"

    # 4. Non-Instagram / Malicious rejection
    assert extract_instagram_url_from_share(url="https://evil.com/reel/123") is None
    assert extract_instagram_url_from_share(text="http://127.0.0.1:8000/internal") is None
    assert extract_instagram_url_from_share(text="") is None


def test_share_target_post_submission(client):
    """Verifies that sharing a Reel via POST queues the reel and redirects with feedback."""
    share_payload = {
        "title": "Interesting Dev Tools",
        "text": "Check this out https://www.instagram.com/reel/SHARE_PWA_01/?igsh=test",
    }
    res = client.post("/share-target", data=share_payload, follow_redirects=False)
    assert res.status_code == 303
    location = res.headers["location"]
    assert "shared=SHARE_PWA_01" in location
    assert "is_new=1" in location

    # Check database
    reel = get_reel_by_shortcode("SHARE_PWA_01")
    assert reel is not None
    assert reel.shortcode == "SHARE_PWA_01"
    assert reel.status == "queued"
    assert reel.path_type == "reel"


def test_share_target_get_submission(client):
    """Verifies fallback GET share target handling with /p/ path preservation."""
    res = client.get(
        "/share-target",
        params={"url": "https://www.instagram.com/p/SHARE_GET_02/?utm_source=ig"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "shared=SHARE_GET_02" in res.headers["location"]

    reel = get_reel_by_shortcode("SHARE_GET_02")
    assert reel is not None
    assert reel.path_type == "p"


def test_share_target_deduplication(client):
    """Verifies that re-sharing an existing shortcode does not create a duplicate."""
    # First share
    client.post("/share-target", data={"url": "https://www.instagram.com/reel/DUP_SHARE_01/"})

    # Second share of same shortcode
    res2 = client.post(
        "/share-target",
        data={"text": "Re-sharing https://www.instagram.com/reel/DUP_SHARE_01/"},
        follow_redirects=False,
    )
    assert res2.status_code == 303
    assert "is_new=0" in res2.headers["location"]


def test_share_target_invalid_payload(client):
    """Verifies that sharing non-Instagram content redirects with clear error feedback."""
    res = client.post("/share-target", data={"text": "Just general text with no reel"}, follow_redirects=False)
    assert res.status_code == 303
    assert "share_error=" in res.headers["location"]


def test_share_target_auth_protection(client):
    """Verifies that an unauthenticated share target request redirects to login preserving destination."""
    with patch.object(settings, "vault_auth_token", "secret-pwa-token"):
        # 1. Unauthenticated share redirects to login with redirect param
        res = client.post(
            "/share-target",
            data={"url": "https://www.instagram.com/reel/AUTH_SHARE_01/"},
            follow_redirects=False,
        )
        assert res.status_code == 303
        assert "/login?redirect=" in res.headers["location"]

        # 2. Submitting login redirects to the preserved share-target URL
        login_res = client.post(
            "/login",
            data={"token": "secret-pwa-token", "redirect": "/share-target?url=https://www.instagram.com/reel/AUTH_SHARE_01/"},
            follow_redirects=False,
        )
        assert login_res.status_code == 302
        assert "/share-target?url=" in login_res.headers["location"]
        cookie = login_res.headers.get("set-cookie", "")

        # 3. Authenticated share request succeeds
        res_auth = client.post(
            "/share-target",
            headers={"Cookie": cookie.split(";")[0]},
            data={"url": "https://www.instagram.com/reel/AUTH_SHARE_01/"},
            follow_redirects=False,
        )
        assert res_auth.status_code == 303
        assert "shared=AUTH_SHARE_01" in res_auth.headers["location"]


def test_static_icons_served(client):
    """Verifies that PWA icon assets are served over HTTP."""
    res_svg = client.get("/static/icons/icon.svg")
    assert res_svg.status_code == 200
    assert "svg" in res_svg.headers["content-type"]

    res_png = client.get("/static/icons/icon-192.png")
    assert res_png.status_code == 200
    assert "image/png" in res_png.headers["content-type"]
