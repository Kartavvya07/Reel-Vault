"""URL parsing, normalization, validation, and SSRF security defense."""

import ipaddress
import re
import socket
from urllib.parse import urlparse
from typing import List, Optional, Tuple
from app.spike.config import settings

# Supported Instagram URL patterns:
# matches /reel/{shortcode}, /reels/{shortcode}, /p/{shortcode}, /tv/{shortcode}
INSTAGRAM_URL_REGEX = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(reel|reels|p|tv)/([a-zA-Z0-9_-]{3,64})/?.*$",
    re.IGNORECASE,
)


class SSRFValidationError(ValueError):
    """Raised when an address attempts to target loopback or private infrastructure."""
    pass


class InvalidInstagramURLError(ValueError):
    """Raised when a URL is not a recognized Instagram Reel or Post."""
    pass


def is_safe_ip(ip_str: str) -> bool:
    """Verifies that an IP is globally routable and not loopback, private, or cloud metadata."""
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_loopback:
            return False
        if ip.is_private:
            return False
        if ip.is_link_local:
            return False
        if ip.is_multicast:
            return False
        if ip.is_reserved:
            return False
        # Specific check for AWS/GCP/Azure link-local metadata IP
        if ip_str == "169.254.169.254":
            return False
        if ip_str == "0.0.0.0":
            return False
        return True
    except ValueError:
        return False


def validate_host_and_ssrf(raw_url: str, custom_allowed_hosts: Optional[List[str]] = None) -> None:
    """
    Validates host against allowlist and checks resolved IPs against SSRF restrictions.
    Enforces DNS resolution safety without silent bypass.
    """
    parsed = urlparse(raw_url.strip())
    if not parsed.scheme or parsed.scheme.lower() not in ("http", "https"):
        raise InvalidInstagramURLError(f"Invalid URL scheme: '{parsed.scheme}'. Only http and https supported.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise InvalidInstagramURLError("URL is missing a valid hostname.")

    allowed = custom_allowed_hosts or settings.allowed_hosts

    # Host allowlist validation
    if hostname not in allowed:
        raise SSRFValidationError(
            f"Host '{hostname}' is not authorized. Allowed hosts: {', '.join(allowed)}."
        )

    # DNS resolution and SSRF defense
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for entry in addr_info:
            ip_str = entry[4][0]
            if not is_safe_ip(ip_str):
                raise SSRFValidationError(f"Target host '{hostname}' resolves to restricted IP: '{ip_str}'")
    except socket.gaierror as e:
        if settings.is_production:
            raise SSRFValidationError(f"DNS resolution failed for host '{hostname}': {e}")


def validate_redirect_url(redirect_url: str, allowed_hosts: Optional[List[str]] = None) -> None:
    """
    Validates a redirect target URL at every hop.
    Ensures that an outbound request cannot be bounced to an internal IP or unauthorized domain.
    """
    validate_host_and_ssrf(redirect_url, custom_allowed_hosts=allowed_hosts)


def parse_and_normalize_url(raw_url: str, check_ssrf: bool = True) -> Tuple[str, str, str]:
    """
    Parses, validates, cleans, and normalizes an Instagram URL.

    Requirements:
    1. Strips all tracking query parameters (?igsh=..., ?utm_source=..., etc.).
    2. Preserves original path type (/p/ vs /reel/). Does NOT convert /p/ into /reel/.
    3. Normalizes /reels/ to /reel/.
    4. Validates shortcode structure (alphanumeric and _ - characters, length 3-64).

    Returns:
        (normalized_url, shortcode, path_type)
    """
    clean_raw = raw_url.strip()
    if not clean_raw:
        raise InvalidInstagramURLError("URL string cannot be empty.")

    if check_ssrf:
        validate_host_and_ssrf(clean_raw)

    match = INSTAGRAM_URL_REGEX.match(clean_raw)
    if not match:
        raise InvalidInstagramURLError(
            f"URL '{clean_raw}' does not match supported Instagram formats (/reel/, /reels/, /p/, /tv/)."
        )

    raw_path_type, shortcode = match.groups()
    lower_path = raw_path_type.lower()
    path_type = lower_path if lower_path in ("p", "tv") else "reel"

    # Canonical normalized representation
    normalized_url = f"https://www.instagram.com/{path_type}/{shortcode}/"

    return normalized_url, shortcode, path_type
