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

BROWSER_SIGNAL_PHRASES = [
    "I wish",
    "looking for",
    "recommend",
    "any tool",
    "is there a tool",
    "alternative",
    "too expensive",
    "hate",
    "annoying",
    "manual",
    "takes too long",
    "missing feature",
    "workaround",
    "need software",
]

STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
    "students",
    "student",
    "college",
    "tools",
    "tool",
    "software",
    "apps",
    "app",
}


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


def build_browser_search_queries(
    topic: str,
    subreddits: Iterable[str],
    search_phrases: Iterable[str] | None = None,
    max_queries_per_subreddit: int = 12,
) -> list[RedditQuery]:
    """Build broader, less exact queries for Reddit's browser search UI."""

    phrases = browser_phrases(search_phrases)
    variants = build_browser_topic_variants(topic)
    queries: list[RedditQuery] = []
    seen: set[tuple[str, str]] = set()

    for subreddit in normalize_subreddits(subreddits):
        for variant in variants:
            add_query(queries, seen, subreddit, variant, None, max_queries_per_subreddit)

        phrase_variants = [variant for variant in variants if variant != topic.lower()][:3]
        if not phrase_variants:
            phrase_variants = variants[:1]
        for variant in phrase_variants:
            for phrase in phrases:
                if subreddit_query_count(queries, subreddit) >= max_queries_per_subreddit:
                    break
                query_text = f"{variant} {phrase}".strip()
                add_query(queries, seen, subreddit, query_text, phrase, max_queries_per_subreddit)

        for phrase in phrases:
            if subreddit_query_count(queries, subreddit) >= max_queries_per_subreddit:
                break
            add_query(queries, seen, subreddit, phrase, phrase, max_queries_per_subreddit)

    return interleave_subreddit_queries(queries)


def build_browser_topic_variants(topic: str) -> list[str]:
    """Create short, recall-oriented topic variants for browser search."""

    topic = " ".join(topic.split())
    lowered = topic.lower()
    variants: list[str] = []
    words = re.findall(r"[a-z0-9']+", lowered)
    content_words = [word for word in words if word not in STOP_WORDS]

    if "study" in words:
        variants.extend(["study tools", "study app", "studying", "homework", "notes"])
    if "video" in words or "editing" in words:
        variants.extend(["video editing", "editing software", "editing app"])
    if "ai" in words:
        variants.extend(["ai tool", "ai software"])

    if content_words:
        variants.append(" ".join(content_words[:3]))
    if len(words) >= 2:
        variants.append(" ".join(words[:2]))
    variants.append(lowered)

    return unique_ordered([variant for variant in variants if variant])


def browser_phrases(search_phrases: Iterable[str] | None = None) -> list[str]:
    """Prefer short phrase fragments that Reddit browser search can match."""

    source = list(search_phrases or BROWSER_SIGNAL_PHRASES)
    simplified: list[str] = []
    for phrase in source:
        lowered = phrase.lower().strip('" ')
        lowered = lowered.replace("doesn't", "doesnt").replace("can't", "cant")
        if lowered in {"why doesnt", "why cant", "how do you manage"}:
            continue
        simplified.append(lowered)
    simplified.extend(BROWSER_SIGNAL_PHRASES)
    return unique_ordered(simplified)


def add_query(
    queries: list[RedditQuery],
    seen: set[tuple[str, str]],
    subreddit: str,
    query: str,
    phrase: str | None,
    max_queries_per_subreddit: int,
) -> bool:
    count = len([item for item in queries if item.subreddit.lower() == subreddit.lower()])
    if count >= max_queries_per_subreddit:
        return True
    query = " ".join(query.replace('"', " ").split())
    if not query:
        return False
    key = (subreddit.lower(), query.lower())
    if key in seen:
        return False
    queries.append(RedditQuery(subreddit=subreddit, query=query, phrase=phrase))
    seen.add(key)
    return False


def subreddit_query_count(queries: list[RedditQuery], subreddit: str) -> int:
    return len([item for item in queries if item.subreddit.lower() == subreddit.lower()])


def unique_ordered(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value).split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def interleave_subreddit_queries(queries: list[RedditQuery]) -> list[RedditQuery]:
    """Schedule one query per subreddit per round for balanced browser collection."""

    order: list[str] = []
    grouped: dict[str, list[RedditQuery]] = {}
    for query in queries:
        key = query.subreddit.lower()
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(query)

    interleaved: list[RedditQuery] = []
    index = 0
    while True:
        added = False
        for key in order:
            bucket = grouped[key]
            if index < len(bucket):
                interleaved.append(bucket[index])
                added = True
        if not added:
            break
        index += 1
    return interleaved
