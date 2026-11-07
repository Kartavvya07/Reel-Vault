"""Unit tests for URL parser, normalizer, and SSRF security validator."""

import pytest
from app.spike.url_parser import (
    InvalidInstagramURLError,
    SSRFValidationError,
    is_safe_ip,
    parse_and_normalize_url,
    validate_host_and_ssrf,
)


def test_standard_reel_url_normalization():
    raw = "https://www.instagram.com/reel/C1234567890/?igsh=MWZzZmVxOWR1YzAzaA=="
    normalized, shortcode, path_type = parse_and_normalize_url(raw, check_ssrf=False)
    assert normalized == "https://www.instagram.com/reel/C1234567890/"
    assert shortcode == "C1234567890"
    assert path_type == "reel"


def test_reels_plural_normalized_to_singular_reel():
    raw = "https://instagram.com/reels/TEST_SAMPLE_01/"
    normalized, shortcode, path_type = parse_and_normalize_url(raw, check_ssrf=False)
    assert normalized == "https://www.instagram.com/reel/TEST_SAMPLE_01/"
    assert shortcode == "TEST_SAMPLE_01"
    assert path_type == "reel"


def test_post_path_preserved_never_converted_to_reel():
    raw = "https://www.instagram.com/p/TEST_SAMPLE_02/?utm_source=ig_web_copy_link"
    normalized, shortcode, path_type = parse_and_normalize_url(raw, check_ssrf=False)
    assert normalized == "https://www.instagram.com/p/TEST_SAMPLE_02/"
    assert shortcode == "TEST_SAMPLE_02"
    assert path_type == "p"


def test_tracking_query_parameters_stripped():
    raw = "https://www.instagram.com/reel/CxYz_123/?utm_source=ig_embed&ig_mid=123&utm_campaign=share"
    normalized, shortcode, path_type = parse_and_normalize_url(raw, check_ssrf=False)
    assert normalized == "https://www.instagram.com/reel/CxYz_123/"
    assert "?" not in normalized


def test_invalid_domain_raises_error():
    with pytest.raises(InvalidInstagramURLError):
        parse_and_normalize_url("https://www.tiktok.com/@user/video/12345", check_ssrf=False)

    with pytest.raises(InvalidInstagramURLError):
        parse_and_normalize_url("https://malicious-instagram.com/reel/123", check_ssrf=False)


def test_empty_or_whitespace_raises_error():
    with pytest.raises(InvalidInstagramURLError):
        parse_and_normalize_url("", check_ssrf=False)

    with pytest.raises(InvalidInstagramURLError):
        parse_and_normalize_url("   ", check_ssrf=False)


def test_ssrf_ip_filtering():
    assert is_safe_ip("127.0.0.1") is False
    assert is_safe_ip("10.0.0.1") is False
    assert is_safe_ip("192.168.1.1") is False
    assert is_safe_ip("172.16.0.1") is False
    assert is_safe_ip("169.254.169.254") is False
    assert is_safe_ip("0.0.0.0") is False
    # Public IP
    assert is_safe_ip("157.240.241.174") is True

