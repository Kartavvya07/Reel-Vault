"""PWA and Web Share Target utilities for Reel Vault."""

from pathlib import Path
import re
from typing import Any, Dict, Optional

from app.spike.url_parser import parse_and_normalize_url, InvalidInstagramURLError, SSRFValidationError

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ICONS_DIR = STATIC_DIR / "icons"

INSTAGRAM_REEL_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/([a-zA-Z0-9_-]+)(?:[/?][^\s\"'<>]*)?",
    re.IGNORECASE,
)


def ensure_pwa_icons() -> None:
    """Ensures PWA icon assets exist on disk in app/static/icons."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    svg_file = ICONS_DIR / "icon.svg"

    if not svg_file.exists():
        svg_code = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0F172A"/>
      <stop offset="100%" stop-color="#020617"/>
    </linearGradient>
    <linearGradient id="bolt" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#34D399"/>
      <stop offset="100%" stop-color="#10B981"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="128" fill="url(#bg)"/>
  <rect x="16" y="16" width="480" height="480" rx="112" fill="none" stroke="#10B981" stroke-opacity="0.3" stroke-width="12"/>
  <path d="M280 64 L160 272 L256 272 L232 448 L352 240 L256 240 Z" fill="url(#bolt)"/>
</svg>"""
        svg_file.write_text(svg_code, encoding="utf-8")

    def make_png(size: int, filename: str, padding_ratio: float = 0.15):
        target = ICONS_DIR / filename
        if target.exists():
            return
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        radius = int(size * 0.22)
        draw.rounded_rectangle(
            [(0, 0), (size - 1, size - 1)],
            radius=radius,
            fill=(15, 23, 42, 255),
            outline=(16, 185, 129, 90),
            width=max(2, size // 40),
        )
        p = size * padding_ratio
        h = size - 2 * p
        w = size - 2 * p
        points = [
            (p + w * 0.55, p + h * 0.05),
            (p + w * 0.20, p + h * 0.55),
            (p + w * 0.48, p + h * 0.55),
            (p + w * 0.42, p + h * 0.95),
            (p + w * 0.78, p + h * 0.45),
            (p + w * 0.50, p + h * 0.45),
        ]
        draw.polygon(points, fill=(16, 185, 129, 255))
        img.save(target, "PNG")

    make_png(192, "icon-192.png")
    make_png(512, "icon-512.png")
    make_png(512, "maskable-512.png", padding_ratio=0.22)


def get_web_app_manifest() -> Dict[str, Any]:
    """Generates the W3C Web App Manifest for Android PWA installation."""
    return {
        "name": "Reel Vault — Personal Knowledge Library",
        "short_name": "Reel Vault",
        "description": "Personal Instagram Reel knowledge library and extraction vault.",
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#080B12",
        "theme_color": "#080B12",
        "icons": [
            {
                "src": "/static/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "/static/icons/maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": "/static/icons/icon.svg",
                "sizes": "512x512",
                "type": "image/svg+xml",
                "purpose": "any",
            },
        ],
        "share_target": {
            "action": "/share-target",
            "method": "POST",
            "enctype": "application/x-www-form-urlencoded",
            "params": {
                "title": "title",
                "text": "text",
                "url": "url",
            },
        },
        "categories": ["productivity", "utilities"],
    }


def get_service_worker_script() -> str:
    """Returns the service worker JavaScript with offline app shell caching and network-first sync."""
    return """// Reel Vault Service Worker
const CACHE_NAME = 'reel-vault-v1';
const SHELL_ASSETS = [
    '/',
    '/manifest.json',
    '/static/icons/icon.svg',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/maskable-512.png'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(SHELL_ASSETS).catch((err) => {
                console.warn('Some shell assets could not be cached immediately:', err);
            });
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Dynamic API and share targets are strictly network-first
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/share-target') || event.request.method !== 'GET') {
        event.respondWith(fetch(event.request));
        return;
    }

    // Static assets & shell: Stale-While-Revalidate
    event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
            const fetchPromise = fetch(event.request).then((networkResponse) => {
                if (networkResponse && networkResponse.status === 200) {
                    const responseClone = networkResponse.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseClone);
                    });
                }
                return networkResponse;
            }).catch(() => cachedResponse);

            return cachedResponse || fetchPromise;
        })
    );
});
"""


def extract_instagram_url_from_share(
    title: Optional[str] = None,
    text: Optional[str] = None,
    url: Optional[str] = None,
) -> Optional[str]:
    """
    Robustly extracts an Instagram Reel URL from Android Web Share Target payload.
    Android and Instagram share targets may package the link in 'url', 'text', or 'title'.
    Preserves original URL including '/reel/' versus '/p/' paths.
    """
    candidates = []
    if url:
        candidates.append(url.strip())
    if text:
        candidates.append(text.strip())
    if title:
        candidates.append(title.strip())

    for c in candidates:
        if not c:
            continue

        # Look for Instagram URL pattern
        match = INSTAGRAM_REEL_PATTERN.search(c)
        if match:
            raw_matched = match.group(0)
            # Strip trailing punctuation often attached when sharing text
            raw_matched = raw_matched.rstrip(".,;!?)>]\'\"")
            try:
                # Validate URL structure and host SSRF safety
                parse_and_normalize_url(raw_matched)
                return raw_matched
            except (InvalidInstagramURLError, SSRFValidationError):
                continue

    return None

