"""Metadata and caption extraction adapter with hop-by-hop SSRF redirect defense."""

import html
import logging
import os
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from typing import Any, Dict, List, Optional
import httpx

from app.spike.config import settings
from app.spike.url_parser import SSRFValidationError, validate_redirect_url

logger = logging.getLogger(__name__)

META_OEMBED_ENDPOINT = "https://graph.facebook.com/v19.0/instagram_oembed"
LEGACY_OEMBED_ENDPOINT = "https://api.instagram.com/oembed/"

OEMBED_ALLOWED_HOSTS = [
    "instagram.com",
    "www.instagram.com",
    "graph.facebook.com",
    "api.instagram.com",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 ReelVaultSpike/0.1.0"
)


def sanitize_url_for_logging(raw_url: str) -> str:
    """Removes sensitive tokens (access_token, token, key, secret) from URLs before logging."""
    try:
        parsed = urlparse(raw_url)
        if not parsed.query:
            return raw_url
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        sanitized_pairs = []
        for k, v in query_pairs:
            if k.lower() in ("access_token", "token", "key", "secret", "auth"):
                sanitized_pairs.append((k, "[REDACTED]"))
            else:
                sanitized_pairs.append((k, v))
        return urlunparse(parsed._replace(query=urlencode(sanitized_pairs)))
    except Exception:
        return "[URL_REDACTED]"


def extract_opengraph_tags(html_content: str) -> Dict[str, str]:
    """Extract OpenGraph and Twitter meta tags from raw HTML."""
    tags: Dict[str, str] = {}
    pattern = re.compile(
        r'<meta\s+(?:property|name)=["\'](og:[a-zA-Z0-9_:]+|twitter:[a-zA-Z0-9_:]+|description)["\']\s+content=["\']([^"\']*)["\']',
        re.IGNORECASE,
    )
    for match in pattern.finditer(html_content):
        prop, content = match.groups()
        tags[prop.lower()] = html.unescape(content)

    alt_pattern = re.compile(
        r'<meta\s+content=["\']([^"\']*)["\']\s+(?:property|name)=["\'](og:[a-zA-Z0-9_:]+|twitter:[a-zA-Z0-9_:]+|description)["\']',
        re.IGNORECASE,
    )
    for match in alt_pattern.finditer(html_content):
        content, prop = match.groups()
        tags[prop.lower()] = html.unescape(content)

    return tags


async def safe_http_get(
    client: httpx.AsyncClient,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    allowed_hosts: Optional[List[str]] = None,
    max_redirects: int = 5,
) -> httpx.Response:
    """
    Executes an HTTP GET with hop-by-hop SSRF redirect validation.
    Never follows redirects to non-allowlisted domains or private IP ranges.
    """
    hosts = allowed_hosts or OEMBED_ALLOWED_HOSTS
    current_url = url
    current_params = params

    for hop in range(max_redirects + 1):
        # Validate current URL against SSRF and allowlist before connecting
        validate_redirect_url(current_url, allowed_hosts=hosts)

        resp = await client.get(
            current_url,
            params=current_params,
            headers=headers,
            follow_redirects=False,
        )

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            # Resolve relative redirects securely
            next_url = urljoin(current_url, location)
            logger.debug(
                "Validated redirect hop %d: %s -> %s",
                hop + 1,
                sanitize_url_for_logging(current_url),
                sanitize_url_for_logging(next_url),
            )
            current_url = next_url
            current_params = None  # Params already encoded in redirect location
            continue

        return resp

    raise SSRFValidationError(f"Exceeded maximum redirect hops ({max_redirects}) for URL: {sanitize_url_for_logging(url)}")


async def fetch_oembed_metadata(
    url: str,
    access_token: Optional[str] = None,
    timeout: float = 12.0,
) -> Optional[Dict[str, Any]]:
    """
    Fetches Instagram oEmbed metadata with strict redirect and SSRF validation.
    """
    token = access_token or os.getenv("META_APP_TOKEN") or os.getenv("FACEBOOK_APP_TOKEN")
    headers = {"User-Agent": USER_AGENT}

    # 1. Try Meta Graph API oEmbed if token is configured
    if token:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await safe_http_get(
                    client,
                    META_OEMBED_ENDPOINT,
                    params={"url": url, "access_token": token, "omitscript": "true"},
                    headers=headers,
                    allowed_hosts=OEMBED_ALLOWED_HOSTS,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "source": "meta_oembed",
                        "title": data.get("title"),
                        "author_name": data.get("author_name"),
                        "author_url": data.get("author_url"),
                        "caption": data.get("title"),
                        "thumbnail_url": data.get("thumbnail_url"),
                        "raw": data,
                    }
                else:
                    logger.warning("Meta oEmbed returned HTTP %s", resp.status_code)
        except SSRFValidationError as ssrf_err:
            logger.error("SSRF validation error during Meta oEmbed: %s", ssrf_err)
            raise
        except Exception as e:
            logger.warning("Meta oEmbed request failed: %s", e)

    # 2. Try legacy/public endpoint or OpenGraph page scrape
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                resp = await safe_http_get(
                    client,
                    LEGACY_OEMBED_ENDPOINT,
                    params={"url": url, "omitscript": "true"},
                    headers=headers,
                    allowed_hosts=OEMBED_ALLOWED_HOSTS,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "source": "public_oembed",
                        "title": data.get("title"),
                        "author_name": data.get("author_name"),
                        "author_url": data.get("author_url"),
                        "caption": data.get("title"),
                        "thumbnail_url": data.get("thumbnail_url"),
                        "raw": data,
                    }
            except Exception:
                pass

            # Fallback to direct HTML OpenGraph tag parsing (restricted to Instagram hosts)
            resp = await safe_http_get(
                client,
                url,
                headers=headers,
                allowed_hosts=settings.allowed_hosts,
            )
            if resp.status_code == 200:
                og_tags = extract_opengraph_tags(resp.text)
                if og_tags:
                    caption = og_tags.get("og:description") or og_tags.get("description")
                    title = og_tags.get("og:title")
                    thumb = og_tags.get("og:image")
                    return {
                        "source": "opengraph_html",
                        "title": title,
                        "author_name": None,
                        "author_url": None,
                        "caption": caption,
                        "thumbnail_url": thumb,
                        "raw": og_tags,
                    }
    except SSRFValidationError as ssrf_err:
        logger.error("SSRF validation error during fallback fetch: %s", ssrf_err)
        raise
    except Exception as e:
        logger.warning("Direct HTML OpenGraph fetch failed for %s: %s", sanitize_url_for_logging(url), e)

    return None
