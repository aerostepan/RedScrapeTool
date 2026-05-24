"""Reddit search query construction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


DEFAULT_SIGNAL_PHRASES = [
    "I wish",
    "why doesn't",
    "why cant",
    "why can't",
    "alternative to",
    "any tool for",
    "is there a tool",
    "looking for a tool",
    "missing feature",
    "too expensive",
    "hate using",
    "annoying",
    "manual",
    "takes too long",
    "doesn't integrate",
    "no integration",
    "workaround",
    "feature request",
    "need software for",
    "how do you manage",
]


@dataclass(frozen=True)
class RedditQuery:
    """A concrete query to run against one subreddit."""

    subreddit: str
    query: str
    phrase: str | None = None


def normalize_subreddits(value: str | Iterable[str]) -> list[str]:
    """Return clean subreddit names without leading r/."""

    if isinstance(value, str):
        parts = re.split(r"[,;\s]+", value)
    else:
        parts = list(value)
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        subreddit = str(part).strip().removeprefix("r/").strip("/")
        if subreddit and subreddit.lower() not in seen:
            cleaned.append(subreddit)
            seen.add(subreddit.lower())
    return cleaned


def build_topic_variants(topic: str) -> list[str]:
    """Create conservative topic variants without inventing competitors."""

    topic = " ".join(topic.split())
    variants = [topic]

    lowered = topic.lower()
    suffixes = (" tools", " tool", " software", " apps", " app")
    for suffix in suffixes:
        if lowered.endswith(suffix):
            trimmed = topic[: -len(suffix)].strip()
            if trimmed and trimmed.lower() != lowered:
                variants.append(trimmed)
            break

    deduped: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        key = variant.lower()
        if key not in seen:
            deduped.append(variant)
            seen.add(key)
    return deduped


def quote_query_term(term: str) -> str:
    """Quote multi-word terms for Reddit search."""

    term = " ".join(term.split())
    if not term:
        return term
    if " " in term and not (term.startswith('"') and term.endswith('"')):
        return f'"{term}"'
    return term


def build_search_queries(
    topic: str,
    subreddits: Iterable[str],
    search_phrases: Iterable[str] | None = None,
) -> list[RedditQuery]:
    """Build subreddit/query combinations from the topic and demand phrases."""

    phrases = list(search_phrases or DEFAULT_SIGNAL_PHRASES)
    variants = build_topic_variants(topic)
    queries: list[RedditQuery] = []
    seen: set[tuple[str, str]] = set()

    for subreddit in normalize_subreddits(subreddits):
        for variant in variants:
            for phrase in phrases:
                query = f"{quote_query_term(variant)} {quote_query_term(phrase)}"
                key = (subreddit.lower(), query.lower())
                if key not in seen:
                    queries.append(RedditQuery(subreddit=subreddit, query=query, phrase=phrase))
                    seen.add(key)

        topic_key = (subreddit.lower(), quote_query_term(topic).lower())
        if topic_key not in seen:
            queries.append(
                RedditQuery(subreddit=subreddit, query=quote_query_term(topic), phrase=None)
            )
            seen.add(topic_key)

    return queries

