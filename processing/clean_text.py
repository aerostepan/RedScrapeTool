"""Text cleaning for Reddit posts and comments."""

from __future__ import annotations

import html
import re


BOT_BOILERPLATE_PATTERNS = [
    r"i am a bot, and this action was performed automatically\..*",
    r"please contact the moderators of this subreddit.*",
    r"your post has been removed because.*",
    r"this thread has been locked by the moderators.*",
]


def clean_reddit_text(text: str, max_chars: int = 6000) -> dict[str, str]:
    """Clean text while preserving the original."""

    original = text or ""
    cleaned = html.unescape(original)
    cleaned = remove_html_tags(cleaned)
    cleaned = remove_markdown(cleaned)
    cleaned = remove_urls(cleaned)
    cleaned = remove_boilerplate(cleaned)
    cleaned = normalize_whitespace(cleaned)
    cleaned = truncate_safely(cleaned, max_chars=max_chars)
    return {"original_text": original, "cleaned_text": cleaned}


def remove_html_tags(text: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", " ", text)


def remove_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(^|\n)\s{0,3}#{1,6}\s+", r"\1", text)
    text = re.sub(r"(^|\n)\s{0,3}>\s?", r"\1", text)
    text = re.sub(r"(^|\n)\s*[-*+]\s+", r"\1", text)
    text = re.sub(r"[*_~]{1,3}", "", text)
    return text


def remove_urls(text: str) -> str:
    return re.sub(r"https?://\S+|www\.\S+", " ", text)


def remove_boilerplate(text: str) -> str:
    cleaned = text
    for pattern in BOT_BOILERPLATE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned


def normalize_whitespace(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def truncate_safely(text: str, max_chars: int = 6000) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    sentence_break = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if sentence_break > max_chars * 0.7:
        return truncated[: sentence_break + 1].strip()
    return truncated.rsplit(" ", 1)[0].strip()
