"""Unit tests for Pydantic schema validation."""

import pytest
from pydantic import ValidationError
from app.spike.schemas import (
    CodeSnippet,
    ExtractedUrl,
    FailureCategory,
    FetchStatus,
    ReelAnalysisOutput,
    ToolResource,
)


def test_valid_reel_analysis_output():
    data = {
        "title": "Top 3 Python Automation Libraries",
        "category": "Python & Backend",
        "tags": ["python", "automation", "scripts"],
        "short_summary": "A review of playwright, beautifulsoup, and schedule for Python automation.",
        "takeaway": "Use playwright for modern browser scraping instead of outdated selenium scripts.",
        "key_points": [
            "Playwright supports async out of the box",
            "Schedule is great for lightweight cron jobs",
        ],
        "tools_and_resources": [
            {
                "name": "Playwright",
                "type": "python_package",
                "url": "https://playwright.dev",
                "source": "speech",
                "verification_status": "unverified",
            }
        ],
        "extracted_urls": [
            {"url": "https://playwright.dev", "context": "Official site", "source": "caption"}
        ],
        "commands_or_code_snippets": [
            {"language_or_shell": "bash", "code": "pip install playwright", "description": "Install"}
        ],
        "transcript": "Check out these top three Python automation libraries...",
        "on_screen_text": "Top 3 Python Libraries",
        "original_language": "en",
        "english_summary": "Summary in English of the top 3 libraries.",
        "confidence": 0.95,
        "processing_warnings": [],
    }
    output = ReelAnalysisOutput.model_validate(data)
    assert output.title == "Top 3 Python Automation Libraries"
    assert len(output.tools_and_resources) == 1
    assert output.tools_and_resources[0].name == "Playwright"
    assert output.confidence == 0.95


def test_confidence_boundary_validation():
    base_data = {
        "title": "Title",
        "category": "Category",
        "short_summary": "Summary",
        "takeaway": "Takeaway",
        "english_summary": "English summary",
    }
    # Valid
    valid = ReelAnalysisOutput.model_validate({**base_data, "confidence": 1.0})
    assert valid.confidence == 1.0

    # Over 1.0 raises ValidationError
    with pytest.raises(ValidationError):
        ReelAnalysisOutput.model_validate({**base_data, "confidence": 1.5})

    # Under 0.0 raises ValidationError
    with pytest.raises(ValidationError):
        ReelAnalysisOutput.model_validate({**base_data, "confidence": -0.1})


def test_missing_required_fields_raises_validation_error():
    with pytest.raises(ValidationError):
        # Missing title, short_summary, takeaway, english_summary
        ReelAnalysisOutput.model_validate({"category": "Tech"})

