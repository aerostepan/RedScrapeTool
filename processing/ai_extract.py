"""Strict JSON demand-signal extraction."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

ARRAY_FIELDS = [
    "pain_points",
    "fears",
    "needs",
    "objections",
    "desired_features",
    "disliked_competitor_features",
    "competitors_mentioned",
    "existing_workarounds",
    "pricing_complaints",
    "integration_gaps",
    "manual_workflows",
]

SCORE_FIELDS = [
    "emotional_intensity",
    "urgency",
    "commercial_potential",
    "confidence",
]

WTP_VALUES = {"none", "low", "medium", "high", "none_low_medium_high"}


class AIExtractor:
    """Model-backed extractor with a conservative local fallback."""

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4.1-mini",
        temperature: float = 0,
        prompt_path: str | Path = "prompts/extraction_prompt.md",
        enabled: bool = True,
    ) -> None:
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.prompt_path = Path(prompt_path)
        self.enabled = enabled and provider.lower() != "disabled"
        self.system_prompt = self._load_prompt()
        self.client = None

        if self.enabled and provider.lower() == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                LOGGER.warning("OPENAI_API_KEY is not set; using conservative local extractor")
                self.enabled = False
            else:
                try:
                    from openai import OpenAI

                    self.client = OpenAI(api_key=api_key)
                except ModuleNotFoundError:
                    LOGGER.warning("openai package is not installed; using conservative local extractor")
                    self.enabled = False

    def extract(self, title: str, text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        metadata = metadata or {}
        if not self.enabled:
            return heuristic_extract(title=title, text=text)

        if self.provider.lower() != "openai":
            LOGGER.warning("Unknown AI provider %s; using conservative local extractor", self.provider)
            return heuristic_extract(title=title, text=text)

        prompt = self._build_user_prompt(title=title, text=text, metadata=metadata)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content or "{}"
            parsed = parse_json_object(content)
            return normalize_extraction(parsed)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("AI extraction failed; using local fallback: %s", exc)
            fallback = heuristic_extract(title=title, text=text)
            fallback["relevance_reason"] = f"AI extraction failed; fallback used: {exc}"
            return fallback

    def _load_prompt(self) -> str:
        if self.prompt_path.exists():
            return self.prompt_path.read_text(encoding="utf-8")
        return (
            "You are analyzing Reddit posts and comments for startup market research. "
            "Return strict JSON only. Do not invent information."
        )

    def _build_user_prompt(self, title: str, text: str, metadata: dict[str, Any]) -> str:
        return json.dumps(
            {
                "metadata": metadata,
                "title": title,
                "text": text,
                "required_schema": default_extraction(),
            },
            ensure_ascii=False,
        )


def default_extraction() -> dict[str, Any]:
    return {
        "is_relevant": False,
        "relevance_reason": "",
        "user_segment": None,
        "context": "",
        "pain_points": [],
        "fears": [],
        "needs": [],
        "objections": [],
        "desired_features": [],
        "disliked_competitor_features": [],
        "competitors_mentioned": [],
        "existing_workarounds": [],
        "pricing_complaints": [],
        "integration_gaps": [],
        "manual_workflows": [],
        "emotional_intensity": 1,
        "urgency": 1,
        "willingness_to_pay_signal": "none",
        "commercial_potential": 1,
        "summary": "",
        "possible_startup_idea": None,
        "confidence": 1,
    }


def parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("AI response was not a JSON object")
    return parsed


def normalize_extraction(payload: dict[str, Any]) -> dict[str, Any]:
    result = default_extraction()
    result.update({key: value for key, value in payload.items() if key in result})

    result["is_relevant"] = bool(result.get("is_relevant"))
    for field in ARRAY_FIELDS:
        value = result.get(field)
        if value is None:
            result[field] = []
        elif isinstance(value, list):
            result[field] = [str(item).strip() for item in value if str(item).strip()]
        else:
            result[field] = [str(value).strip()] if str(value).strip() else []

    for field in SCORE_FIELDS:
        try:
            value = int(result.get(field, 1))
        except (TypeError, ValueError):
            value = 1
        result[field] = max(1, min(5, value))

    wtp = str(result.get("willingness_to_pay_signal") or "none").lower().strip()
    result["willingness_to_pay_signal"] = wtp if wtp in WTP_VALUES else "none"

    for field in ("relevance_reason", "context", "summary"):
        result[field] = str(result.get(field) or "").strip()
    for field in ("user_segment", "possible_startup_idea"):
        value = result.get(field)
        result[field] = str(value).strip() if value else None

    return result


def heuristic_extract(title: str, text: str) -> dict[str, Any]:
    """Conservative extractor for no-key smoke tests."""

    blob = f"{title}. {text}".strip()
    sentences = split_sentences(blob)
    result = default_extraction()

    result["pain_points"] = matching_sentences(
        sentences,
        ["hate", "annoying", "problem", "issue", "bug", "takes too long", "too expensive"],
    )
    result["needs"] = matching_sentences(
        sentences,
        ["need", "looking for", "recommend", "any tool", "is there a tool"],
    )
    result["desired_features"] = matching_sentences(
        sentences,
        ["wish", "missing feature", "feature request", "why doesn't", "why can't"],
    )
    result["existing_workarounds"] = matching_sentences(sentences, ["workaround", "manual"])
    result["pricing_complaints"] = matching_sentences(
        sentences,
        ["expensive", "price", "pricing", "cost", "subscription"],
    )
    result["integration_gaps"] = matching_sentences(
        sentences,
        ["integrate", "integration", "doesn't integrate", "no integration"],
    )
    result["manual_workflows"] = matching_sentences(
        sentences,
        ["manual", "spreadsheet", "copy paste", "copy-paste", "by hand"],
    )
    result["competitors_mentioned"] = extract_grounded_competitors(blob)
    result["disliked_competitor_features"] = matching_sentences(
        sentences,
        ["hate using", "annoying", "missing", "too expensive", "doesn't integrate"],
    )

    useful_fields = [
        "pain_points",
        "needs",
        "desired_features",
        "existing_workarounds",
        "pricing_complaints",
        "integration_gaps",
        "manual_workflows",
    ]
    result["is_relevant"] = any(result[field] for field in useful_fields)
    result["relevance_reason"] = (
        "Conservative local extraction found demand-signal keywords"
        if result["is_relevant"]
        else "No actionable demand signal found by local extractor"
    )
    result["context"] = first_nonempty(sentences)
    result["summary"] = summarize_sentences(sentences, useful_fields, result)
    result["emotional_intensity"] = infer_emotional_intensity(blob)
    result["urgency"] = infer_urgency(blob)
    result["commercial_potential"] = infer_commercial_potential(blob, result)
    result["willingness_to_pay_signal"] = infer_wtp(blob)
    result["possible_startup_idea"] = infer_startup_idea(result)
    result["confidence"] = 2 if result["is_relevant"] else 1
    return normalize_extraction(result)


def split_sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+", text)
    return [chunk.strip()[:500] for chunk in chunks if len(chunk.strip()) > 8]


def matching_sentences(sentences: list[str], keywords: list[str], limit: int = 5) -> list[str]:
    hits: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            hits.append(sentence)
            if len(hits) >= limit:
                break
    return hits


def extract_grounded_competitors(text: str) -> list[str]:
    competitors: list[str] = []
    patterns = [
        r"alternative to ([A-Z][A-Za-z0-9 .+\-]{1,40})",
        r"using ([A-Z][A-Za-z0-9 .+\-]{1,40})",
        r"from ([A-Z][A-Za-z0-9 .+\-]{1,40})",
        r"moved from ([A-Z][A-Za-z0-9 .+\-]{1,40})",
    ]
    stop_words = {"I", "The", "Reddit", "This", "Any", "Is", "Why"}
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = match.group(1).strip(" .,!?:;")
            candidate = re.split(r"\s+(?:but|and|because|for|with|that)\s+", candidate)[0]
            if candidate and candidate not in stop_words and candidate not in competitors:
                competitors.append(candidate)
    return competitors[:10]


def infer_emotional_intensity(text: str) -> int:
    lowered = text.lower()
    if any(token in lowered for token in ["hate", "awful", "terrible", "nightmare", "furious"]):
        return 4
    if any(token in lowered for token in ["annoying", "frustrating", "painful", "sucks"]):
        return 3
    return 2


def infer_urgency(text: str) -> int:
    lowered = text.lower()
    if any(token in lowered for token in ["urgent", "asap", "deadline", "need this now"]):
        return 5
    if any(token in lowered for token in ["need", "looking for", "is there a tool"]):
        return 4
    return 2


def infer_commercial_potential(text: str, result: dict[str, Any]) -> int:
    lowered = text.lower()
    score = 2
    if any(token in lowered for token in ["tool", "software", "subscription", "paid", "business"]):
        score += 1
    if result.get("pricing_complaints") or result.get("existing_workarounds"):
        score += 1
    return min(score, 5)


def infer_wtp(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ["would pay", "happy to pay", "budget"]):
        return "high"
    if any(token in lowered for token in ["paid", "subscription", "price", "pricing", "cost"]):
        return "medium"
    return "none"


def infer_startup_idea(result: dict[str, Any]) -> str | None:
    pain = first_from_arrays(result, ["pain_points", "needs", "desired_features"])
    if not pain:
        return None
    return f"Explore a focused tool or workflow improvement for: {pain[:180]}"


def summarize_sentences(
    sentences: list[str],
    useful_fields: list[str],
    result: dict[str, Any],
) -> str:
    for field in useful_fields:
        values = result.get(field) or []
        if values:
            return str(values[0])[:300]
    return first_nonempty(sentences)[:300]


def first_from_arrays(result: dict[str, Any], fields: list[str]) -> str:
    for field in fields:
        values = result.get(field) or []
        if values:
            return str(values[0])
    return ""


def first_nonempty(values: list[str]) -> str:
    return values[0] if values else ""

