"""Unit tests for metric calculation and zero-division protection."""

import pytest
from app.spike.metrics import compute_metrics
from app.spike.schemas import ExperimentResult, FailureCategory, FetchStatus


def test_empty_results_zero_division_safety():
    summary = compute_metrics([])
    assert summary.total_attempts == 0
    assert summary.unique_reels_processed == 0
    assert summary.url_resolution_success_rate == 0.0
    assert summary.media_retrieval_success_rate == 0.0
    assert summary.end_to_end_processing_success_rate == 0.0
    assert summary.median_processing_duration_seconds == 0.0
    assert summary.cost_per_successful_reel_usd == 0.0


def test_metric_aggregation_formulas():
    # Construct 4 mock experiment results
    r1 = ExperimentResult(
        experiment_id="exp_1",
        shortcode="code_1",
        original_url="https://www.instagram.com/reel/code_1/",
        normalized_url="https://www.instagram.com/reel/code_1/",
        path_type="reel",
        attempt_timestamp="2026-11-03T10:00:00Z",
        ingestion_provider="server_ytdlp",
        fetch_status=FetchStatus.SUCCESS_FULL,
        metadata_success=True,
        caption_success=True,
        media_retrieval_success=True,
        ai_processing_success=True,
        output_validation_success=True,
        end_to_end_success=True,
        caption_only_fallback_used=False,
        failure_category=FailureCategory.NONE,
        processing_duration_seconds=10.0,
        estimated_cost_usd=0.005,
    )
    r2 = ExperimentResult(
        experiment_id="exp_2",
        shortcode="code_2",
        original_url="https://www.instagram.com/reel/code_2/",
        normalized_url="https://www.instagram.com/reel/code_2/",
        path_type="reel",
        attempt_timestamp="2026-11-03T10:05:00Z",
        ingestion_provider="oembed",
        fetch_status=FetchStatus.SUCCESS_CAPTION_ONLY,
        metadata_success=True,
        caption_success=True,
        media_retrieval_success=False,  # Fallback: media was not retrieved!
        ai_processing_success=True,
        output_validation_success=True,
        end_to_end_success=True,
        caption_only_fallback_used=True,
        failure_category=FailureCategory.NONE,
        processing_duration_seconds=4.0,
        estimated_cost_usd=0.001,
    )
    r3 = ExperimentResult(
        experiment_id="exp_3",
        shortcode="code_3",
        original_url="https://www.instagram.com/reel/code_3/",
        normalized_url="https://www.instagram.com/reel/code_3/",
        path_type="reel",
        attempt_timestamp="2026-11-03T10:10:00Z",
        ingestion_provider="server_ytdlp",
        fetch_status=FetchStatus.LOGIN_WALL,
        metadata_success=False,
        caption_success=False,
        media_retrieval_success=False,
        ai_processing_success=False,
        output_validation_success=False,
        end_to_end_success=False,
        caption_only_fallback_used=False,
        failure_category=FailureCategory.LOGIN_WALL,
        processing_duration_seconds=2.0,
        estimated_cost_usd=0.0,
    )
    r4 = ExperimentResult(
        experiment_id="exp_4",
        shortcode="code_4",
        original_url="https://www.instagram.com/reel/code_4/",
        normalized_url="https://www.instagram.com/reel/code_4/",
        path_type="reel",
        attempt_timestamp="2026-11-03T10:15:00Z",
        ingestion_provider="server_ytdlp",
        fetch_status=FetchStatus.SUCCESS_FULL,
        metadata_success=True,
        caption_success=True,
        media_retrieval_success=True,
        ai_processing_success=False,
        output_validation_success=False,
        end_to_end_success=False,
        caption_only_fallback_used=False,
        failure_category=FailureCategory.AI_AUTH_ERROR,
        processing_duration_seconds=8.0,
        estimated_cost_usd=0.0,
    )

    summary = compute_metrics([r1, r2, r3, r4])

    assert summary.total_attempts == 4
    assert summary.unique_reels_processed == 4
    # URL resolution rate: 4/4 = 1.0
    assert summary.url_resolution_success_rate == 1.0
    # Media retrieval rate: 2/4 = 0.5 (r1 and r4)
    assert summary.media_retrieval_success_rate == 0.5
    # Caption retrieval rate: 3/4 = 0.75 (r1, r2, r4)
    assert summary.caption_retrieval_success_rate == 0.75
    # AI analysis success: 2/4 = 0.5 (r1 and r2)
    assert summary.ai_analysis_success_rate == 0.5
    # End to end: 2/4 = 0.5 (r1 and r2)
    assert summary.end_to_end_processing_success_rate == 0.5
    # Caption fallback rate: 1/4 = 0.25 (r2)
    assert summary.caption_only_fallback_rate == 0.25
    # Total cost: 0.005 + 0.001 = 0.006
    assert summary.total_estimated_cost_usd == 0.006
    # Cost per successful reel: 0.006 / 2 = 0.003
    assert summary.cost_per_successful_reel_usd == 0.003
    # Latencies: [10.0, 4.0, 2.0, 8.0] -> sorted: 2, 4, 8, 10. Median is 6.0
    assert summary.median_processing_duration_seconds == 6.0

