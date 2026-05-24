"""Lightweight rule-based relevance filter."""

from __future__ import annotations

from dataclasses import dataclass


POSITIVE_KEYWORDS = [
    "wish",
    "hate",
    "annoying",
    "manual",
    "expensive",
    "alternative",
    "missing",
    "feature",
    "problem",
    "issue",
    "bug",
    "integrate",
    "integration",
    "workaround",
    "takes too long",
    "need a tool",
    "looking for",
    "recommend",
    "quote",
    "estimate",
    "rebate",
    "permit",
    "tax credit",
    "paperwork",
    "cost",
    "overcharg",
    "installer",
    "electrician",
    "panel upgrade",
    "warranty",
]

NEGATIVE_KEYWORDS = [
    "meme",
    "joke",
    "giveaway",
    "promo",
    "advertisement",
    "self-promotion",
    "spam",
]


@dataclass(frozen=True)
class FilterResult:
    passed: bool
    reason: str
    positive_hits: list[str]
    negative_hits: list[str]


def rule_based_relevance(text: str, title: str = "") -> FilterResult:
    """Filter obvious low-signal content before model extraction."""

    blob = f"{title}\n{text}".lower()
    positive_hits = [keyword for keyword in POSITIVE_KEYWORDS if keyword in blob]
    negative_hits = [keyword for keyword in NEGATIVE_KEYWORDS if keyword in blob]

    if negative_hits and not positive_hits:
        return FilterResult(
            passed=False,
            reason=f"negative filter only: {', '.join(negative_hits)}",
            positive_hits=positive_hits,
            negative_hits=negative_hits,
        )

    if positive_hits:
        return FilterResult(
            passed=True,
            reason=f"positive demand-signal hits: {', '.join(positive_hits)}",
            positive_hits=positive_hits,
            negative_hits=negative_hits,
        )

    return FilterResult(
        passed=False,
        reason="no lightweight demand-signal keywords found",
        positive_hits=positive_hits,
        negative_hits=negative_hits,
    )
