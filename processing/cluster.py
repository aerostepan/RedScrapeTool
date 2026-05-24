"""Fuzzy clustering for extracted market signals."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any

from processing.score import score_components

SIGNAL_CATEGORIES = {
    "pain_points": "pain_point",
    "desired_features": "desired_feature",
    "disliked_competitor_features": "competitor_weakness",
}


def cluster_signals(records: list[dict[str, Any]], threshold: int = 86) -> list[dict[str, Any]]:
    """Cluster repeated extracted signals by fuzzy normalized labels."""

    clusters: list[dict[str, Any]] = []
    for field, category in SIGNAL_CATEGORIES.items():
        for record in relevant_records(records):
            for signal in extraction(record).get(field, []) or []:
                add_to_cluster(clusters, record, signal, category, threshold)

    finalized: list[dict[str, Any]] = []
    for cluster in clusters:
        items = cluster["items"]
        components = score_components(items)
        competitors = sorted(
            {
                competitor
                for item in items
                for competitor in extraction(item).get("competitors_mentioned", []) or []
            }
        )
        finalized.append(
            {
                "cluster_name": cluster["label"],
                "category": cluster["category"],
                "frequency": components["frequency"],
                "subreddits": sorted({str(item.get("subreddit") or "") for item in items if item.get("subreddit")}),
                "competitors": competitors,
                "representative_quotes": unique_list(cluster["quotes"])[:3],
                "avg_urgency": components["avg_urgency"],
                "avg_emotional_intensity": components["avg_emotional_intensity"],
                "avg_commercial_potential": components["avg_commercial_potential"],
                "workaround_count": components["workaround_count"],
                "pricing_complaint_count": components["pricing_complaint_count"],
                "integration_gap_count": components["integration_gap_count"],
                "opportunity_score": components["opportunity_score"],
                "items": items,
            }
        )
    return sorted(finalized, key=lambda item: item["opportunity_score"], reverse=True)


def desired_feature_summary(records: list[dict[str, Any]], threshold: int = 86) -> list[dict[str, Any]]:
    features = [
        cluster for cluster in cluster_signals(records, threshold=threshold) if cluster["category"] == "desired_feature"
    ]
    rows: list[dict[str, Any]] = []
    for cluster in features:
        related = Counter()
        for item in cluster["items"]:
            for pain in extraction(item).get("pain_points", []) or []:
                related[normalize_label(pain)] += 1
        rows.append(
            {
                "feature": cluster["cluster_name"],
                "frequency": cluster["frequency"],
                "related_pain_point": related.most_common(1)[0][0] if related else "",
                "mentioned_competitors": cluster["competitors"],
                "representative_quotes": cluster["representative_quotes"],
                "opportunity_score": cluster["opportunity_score"],
            }
        )
    return rows


def competitor_weakness_summary(records: list[dict[str, Any]], threshold: int = 86) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for record in relevant_records(records):
        competitors = extraction(record).get("competitors_mentioned", []) or [""]
        weaknesses = extraction(record).get("disliked_competitor_features", []) or []
        for competitor in competitors:
            for weakness in weaknesses:
                key = f"{competitor}|{normalize_label(weakness)}"
                existing = find_group(grouped, key, threshold)
                if existing is None:
                    grouped.append(
                        {
                            "key": key,
                            "competitor": competitor,
                            "weakness": normalize_label(weakness),
                            "items": [record],
                            "quotes": [weakness],
                        }
                    )
                else:
                    existing["items"].append(record)
                    existing["quotes"].append(weakness)

    rows: list[dict[str, Any]] = []
    for group in grouped:
        components = score_components(group["items"])
        segments = sorted(
            {
                str(extraction(item).get("user_segment") or "")
                for item in group["items"]
                if extraction(item).get("user_segment")
            }
        )
        rows.append(
            {
                "competitor": group["competitor"],
                "weakness": group["weakness"],
                "frequency": components["frequency"],
                "related_user_segment": segments,
                "representative_quotes": unique_list(group["quotes"])[:3],
                "opportunity_score": components["opportunity_score"],
            }
        )
    return sorted(rows, key=lambda item: item["opportunity_score"], reverse=True)


def top_opportunities(records: list[dict[str, Any]], clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pain_clusters = [cluster for cluster in clusters if cluster["category"] == "pain_point"]
    for index, cluster in enumerate(pain_clusters, start=1):
        items = cluster["items"]
        desired = sorted(
            {
                feature
                for item in items
                for feature in extraction(item).get("desired_features", []) or []
            }
        )
        workarounds = sorted(
            {
                workaround
                for item in items
                for workaround in extraction(item).get("existing_workarounds", []) or []
            }
        )
        pricing = sorted(
            {
                complaint
                for item in items
                for complaint in extraction(item).get("pricing_complaints", []) or []
            }
        )
        integrations = sorted(
            {
                gap
                for item in items
                for gap in extraction(item).get("integration_gaps", []) or []
            }
        )
        confidence_values = [
            int(extraction(item).get("confidence", 1))
            for item in items
            if extraction(item).get("confidence")
        ]
        segments = Counter(
            str(extraction(item).get("user_segment") or "")
            for item in items
            if extraction(item).get("user_segment")
        )
        rows.append(
            {
                "rank": index,
                "opportunity": cluster["cluster_name"],
                "main_pain_point": cluster["cluster_name"],
                "target_user_segment": segments.most_common(1)[0][0] if segments else "",
                "evidence_count": cluster["frequency"],
                "relevant_subreddits": cluster["subreddits"],
                "competitors_mentioned": cluster["competitors"],
                "desired_features": desired[:5],
                "workarounds_found": workarounds[:5],
                "pricing_signals": pricing[:5],
                "integration_gaps": integrations[:5],
                "opportunity_score": cluster["opportunity_score"],
                "confidence": round(sum(confidence_values) / len(confidence_values), 2)
                if confidence_values
                else 0,
                "representative_evidence_urls": unique_list(
                    [str(item.get("url") or "") for item in items if item.get("url")]
                )[:5],
            }
        )
    return sorted(rows, key=lambda item: item["opportunity_score"], reverse=True)


def add_to_cluster(
    clusters: list[dict[str, Any]],
    record: dict[str, Any],
    signal: str,
    category: str,
    threshold: int,
) -> None:
    label = normalize_label(signal)
    if not label:
        return
    best_cluster = None
    best_score = 0
    for cluster in clusters:
        if cluster["category"] != category:
            continue
        similarity = fuzzy_ratio(label, cluster["normalized_label"])
        if similarity > best_score:
            best_score = similarity
            best_cluster = cluster
    if best_cluster is None or best_score < threshold:
        clusters.append(
            {
                "label": label,
                "normalized_label": label,
                "category": category,
                "items": [record],
                "quotes": [signal],
            }
        )
    else:
        best_cluster["items"].append(record)
        best_cluster["quotes"].append(signal)


def find_group(groups: list[dict[str, Any]], key: str, threshold: int) -> dict[str, Any] | None:
    competitor, weakness = key.split("|", 1)
    for group in groups:
        if group["competitor"] != competitor:
            continue
        if fuzzy_ratio(weakness, group["weakness"]) >= threshold:
            return group
    return None


def relevant_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if extraction(record).get("is_relevant")]


def extraction(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("extraction")
    return value if isinstance(value, dict) else {}


def normalize_label(value: str) -> str:
    value = value.lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^a-z0-9' ]+", " ", value)
    value = re.sub(
        r"\b(i|we|they|you|a|an|the|really|very|just|that|this|there|with|for|to|of|and)\b",
        " ",
        value,
    )
    return " ".join(value.split())[:180]


def fuzzy_ratio(left: str, right: str) -> int:
    try:
        from rapidfuzz import fuzz

        return int(fuzz.ratio(left, right))
    except ModuleNotFoundError:
        return int(SequenceMatcher(None, left, right).ratio() * 100)


def unique_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        result.append(cleaned)
        seen.add(cleaned)
    return result

