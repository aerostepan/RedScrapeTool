"""Transparent opportunity scoring."""

from __future__ import annotations

from typing import Any


def opportunity_score(
    frequency: int,
    avg_urgency: float,
    avg_emotional_intensity: float,
    avg_commercial_potential: float,
    workaround_count: int,
    pricing_complaint_count: int,
    integration_gap_count: int,
) -> float:
    """Score formula from the project brief."""

    return round(
        frequency * 2
        + avg_urgency * 5
        + avg_emotional_intensity * 4
        + avg_commercial_potential * 5
        + workaround_count * 3
        + pricing_complaint_count * 2
        + integration_gap_count * 3,
        2,
    )


def score_components(items: list[dict[str, Any]]) -> dict[str, Any]:
    frequency = len(items)
    avg_urgency = average([extract_score(item, "urgency") for item in items])
    avg_emotional = average([extract_score(item, "emotional_intensity") for item in items])
    avg_commercial = average([extract_score(item, "commercial_potential") for item in items])
    workaround_count = sum(bool(extraction(item).get("existing_workarounds")) for item in items)
    pricing_count = sum(bool(extraction(item).get("pricing_complaints")) for item in items)
    integration_count = sum(bool(extraction(item).get("integration_gaps")) for item in items)
    score = opportunity_score(
        frequency=frequency,
        avg_urgency=avg_urgency,
        avg_emotional_intensity=avg_emotional,
        avg_commercial_potential=avg_commercial,
        workaround_count=workaround_count,
        pricing_complaint_count=pricing_count,
        integration_gap_count=integration_count,
    )
    return {
        "frequency": frequency,
        "avg_urgency": avg_urgency,
        "avg_emotional_intensity": avg_emotional,
        "avg_commercial_potential": avg_commercial,
        "workaround_count": workaround_count,
        "pricing_complaint_count": pricing_count,
        "integration_gap_count": integration_count,
        "opportunity_score": score,
    }


def extraction(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("extraction")
    return value if isinstance(value, dict) else {}


def extract_score(item: dict[str, Any], key: str) -> int:
    try:
        value = int(extraction(item).get(key, 1))
    except (TypeError, ValueError):
        value = 1
    return max(1, min(5, value))


def average(values: list[int | float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)

