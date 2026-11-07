"""Metrics aggregation and statistical telemetry computation for Phase 0 Spike."""

from datetime import datetime, timezone
from typing import List
import numpy as np

from app.spike.schemas import ExperimentResult, FailureCategory, FetchStatus, MetricsSummary
from app.spike.storage import get_all_experiment_results


def compute_metrics(results: List[ExperimentResult]) -> MetricsSummary:
    """
    Computes aggregated statistical metrics from a list of experiment results.
    Guaranteed zero-division safe when results list is empty.
    """
    total_attempts = len(results)
    if total_attempts == 0:
        return MetricsSummary(
            total_attempts=0,
            unique_reels_processed=0,
            url_resolution_success_rate=0.0,
            metadata_retrieval_success_rate=0.0,
            caption_retrieval_success_rate=0.0,
            media_retrieval_success_rate=0.0,
            ai_analysis_success_rate=0.0,
            structured_output_validation_success_rate=0.0,
            end_to_end_processing_success_rate=0.0,
            caption_only_fallback_rate=0.0,
            median_processing_duration_seconds=0.0,
            p95_processing_duration_seconds=0.0,
            total_estimated_cost_usd=0.0,
            cost_per_successful_reel_usd=0.0,
            failure_distribution={},
            provider_distribution={},
            generated_at=datetime.now(timezone.utc),
        )

    # Exclude dummy/unknown placeholder shortcodes and synthetic controls from unique reel count
    unique_shortcodes = {
        r.shortcode
        for r in results
        if r.shortcode
        and r.shortcode not in ("unknown", "C12345Sample")
        and not r.shortcode.lower().startswith("synthetic")
    }

    # 1. URL resolution rate (excludes invalid URL syntax and SSRF attempts)
    url_res_count = sum(
        1
        for r in results
        if r.fetch_status != FetchStatus.INVALID_URL
        and r.failure_category not in (FailureCategory.INVALID_URL, FailureCategory.SSRF_ATTEMPT)
    )

    # 2. Metadata retrieval rate
    metadata_count = sum(1 for r in results if r.metadata_success)

    # 3. Caption retrieval rate
    caption_count = sum(1 for r in results if r.caption_success)

    # 4. Media retrieval rate: STRICTLY excludes caption-only fallbacks
    media_count = sum(1 for r in results if r.media_retrieval_success and not r.caption_only_fallback_used)

    # 5. AI analysis rate
    ai_count = sum(1 for r in results if r.ai_processing_success)

    # 6. Structured output validation rate
    output_val_count = sum(1 for r in results if r.output_validation_success)

    # 7. End-to-end success rate
    e2e_count = sum(1 for r in results if r.end_to_end_success)

    # 8. Caption-only fallback rate
    fallback_count = sum(1 for r in results if r.caption_only_fallback_used)

    # 9. Latency distributions
    durations = [r.processing_duration_seconds for r in results if r.processing_duration_seconds > 0]
    if durations:
        median_duration = float(np.median(durations))
        p95_duration = float(np.percentile(durations, 95))
    else:
        median_duration = 0.0
        p95_duration = 0.0

    # 10. Financial costs
    total_cost = sum(r.estimated_cost_usd for r in results)
    cost_per_success = (total_cost / e2e_count) if e2e_count > 0 else 0.0

    # Categorical distributions
    failure_dist = {}
    for r in results:
        cat = r.failure_category.value if hasattr(r.failure_category, "value") else str(r.failure_category)
        failure_dist[cat] = failure_dist.get(cat, 0) + 1

    provider_dist = {}
    for r in results:
        prov = r.ingestion_provider or "unknown"
        provider_dist[prov] = provider_dist.get(prov, 0) + 1

    return MetricsSummary(
        total_attempts=total_attempts,
        unique_reels_processed=len(unique_shortcodes),
        url_resolution_success_rate=round(url_res_count / total_attempts, 4),
        metadata_retrieval_success_rate=round(metadata_count / total_attempts, 4),
        caption_retrieval_success_rate=round(caption_count / total_attempts, 4),
        media_retrieval_success_rate=round(media_count / total_attempts, 4),
        ai_analysis_success_rate=round(ai_count / total_attempts, 4),
        structured_output_validation_success_rate=round(output_val_count / total_attempts, 4),
        end_to_end_processing_success_rate=round(e2e_count / total_attempts, 4),
        caption_only_fallback_rate=round(fallback_count / total_attempts, 4),
        median_processing_duration_seconds=round(median_duration, 2),
        p95_processing_duration_seconds=round(p95_duration, 2),
        total_estimated_cost_usd=round(total_cost, 6),
        cost_per_successful_reel_usd=round(cost_per_success, 6),
        failure_distribution=failure_dist,
        provider_distribution=provider_dist,
        generated_at=datetime.now(timezone.utc),
    )


def get_current_metrics() -> MetricsSummary:
    """Load all recorded experiments and calculate current metrics."""
    results = get_all_experiment_results()
    return compute_metrics(results)
