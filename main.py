"""CLI entrypoint for the Reddit market research tool."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from export.excel_export import export_market_research_workbook
from processing.ai_extract import AIExtractor, default_extraction
from processing.clean_text import clean_reddit_text
from processing.cluster import (
    cluster_signals,
    competitor_weakness_summary,
    desired_feature_summary,
    top_opportunities,
)
from processing.relevance_filter import rule_based_relevance
from scouting.reddit_api import RedditAPIError, RedditOAuthFetcher
from scouting.reddit_public import RedditPublicFetcher
from scouting.reddit_rss import RedditAccessError, RedditRSSFetcher
from scouting.reddit_search import build_search_queries, normalize_subreddits
from storage.dedupe import DedupeStore
from storage.json_store import append_jsonl
from storage.redis_queue import RedisQueue

LOGGER = logging.getLogger(__name__)


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    args = parse_args()
    config_path = resolve_path(base_dir, args.config)
    config = load_config(config_path)
    load_dotenv_if_available(base_dir)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = setup_logging(base_dir, run_id)
    LOGGER.info("Loaded config from %s", config_path)

    topic = args.topic or config.get("topic") or ""
    if not topic:
        raise SystemExit("--topic is required when config.yaml does not define topic")

    subreddits = normalize_subreddits(args.subreddits or config.get("subreddits") or [])
    if not subreddits:
        raise SystemExit("--subreddits is required when config.yaml does not define subreddits")

    days_back = args.days if args.days is not None else int(config.get("days_back", 90))
    total_limit = args.limit if args.limit is not None else int(config.get("max_total_items", 500))
    limit_per_query = int(config.get("limit_per_query", 50))
    output_path = resolve_path(base_dir, args.output or config.get("output_path"))

    raw_path = base_dir / "data" / "raw" / f"raw_{run_id}.jsonl"
    processed_path = base_dir / "data" / "processed" / f"processed_{run_id}.jsonl"

    reddit_config = config.get("reddit", {}) or {}
    redis_config = config.get("redis", {}) or {}
    ai_config = config.get("ai", {}) or {}
    search_phrases = config.get("search_phrases") or None

    queries = build_search_queries(topic, subreddits, search_phrases)
    LOGGER.info("Generated %s Reddit search queries", len(queries))

    rss_fetcher = RedditRSSFetcher(user_agent=reddit_config.get("user_agent", "market-research-tool/0.1"))
    oauth_fetcher = RedditOAuthFetcher(user_agent=reddit_config.get("user_agent") or None)
    if reddit_config.get("use_oauth_fallback", True) and oauth_fetcher.is_configured:
        LOGGER.info("Reddit OAuth fallback is configured")
    elif reddit_config.get("use_oauth_fallback", True):
        LOGGER.info("Reddit OAuth fallback is enabled but missing REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET")

    raw_items = fetch_raw_items(
        rss_fetcher=rss_fetcher,
        oauth_fetcher=oauth_fetcher if reddit_config.get("use_oauth_fallback", True) else None,
        queries=queries,
        topic=topic,
        total_limit=total_limit,
        limit_per_query=limit_per_query,
        days_back=days_back,
        raw_path=raw_path,
        use_rss=bool(reddit_config.get("use_rss", True)),
    )

    if reddit_config.get("use_public_pages", True) and len(raw_items) < total_limit:
        raw_items.extend(
            fetch_public_comment_fallback(
                raw_items=raw_items,
                topic=topic,
                remaining=total_limit - len(raw_items),
                raw_path=raw_path,
                user_agent=reddit_config.get("user_agent", "market-research-tool/0.1"),
            )
        )

    redis_queue = RedisQueue(
        host=redis_config.get("host", "localhost"),
        port=int(redis_config.get("port", 6379)),
        db=int(redis_config.get("db", 0)),
    )
    dedupe = DedupeStore(
        redis_queue=redis_queue,
        local_path=base_dir / "data" / "processed" / "dedupe_seen.json",
    )
    deduped_items, duplicate_count = dedupe.filter_new(raw_items)

    extractor = AIExtractor(
        provider=ai_config.get("provider", "openai"),
        model=ai_config.get("model", "gpt-4.1-mini"),
        temperature=float(ai_config.get("temperature", 0)),
        prompt_path=base_dir / "prompts" / "extraction_prompt.md",
        enabled=not args.no_ai,
    )

    processed_items = process_items(deduped_items, extractor, processed_path)
    clusters = cluster_signals(processed_items)
    desired_features = desired_feature_summary(processed_items)
    competitor_weaknesses = competitor_weakness_summary(processed_items)
    opportunities = top_opportunities(processed_items, clusters)

    export_market_research_workbook(
        output_path=output_path,
        raw_items=raw_items,
        processed_items=processed_items,
        clusters=clusters,
        desired_features=desired_features,
        competitor_weaknesses=competitor_weaknesses,
        top_opportunities=opportunities,
    )
    LOGGER.info("Excel exported to %s", output_path)
    LOGGER.info("Run log written to %s", log_path)

    passed_rule_filter = sum(1 for item in processed_items if item.get("rule_filter", {}).get("passed"))
    ai_relevant = sum(1 for item in processed_items if item.get("extraction", {}).get("is_relevant"))
    pain_point_clusters = sum(1 for cluster in clusters if cluster.get("category") == "pain_point")
    top_score = opportunities[0]["opportunity_score"] if opportunities else 0

    print(f"Fetched: {len(raw_items)} items")
    print(f"Deduplicated: {len(deduped_items)} items ({duplicate_count} duplicates removed)")
    print(f"Passed rule filter: {passed_rule_filter} items")
    print(f"AI relevant: {ai_relevant} items")
    print(f"Pain point clusters: {pain_point_clusters}")
    print(f"Top opportunity score: {top_score}")
    print(f"Excel exported to: {output_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Reddit-only market research Excel workbook.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--topic", help="Research topic")
    parser.add_argument("--subreddits", help="Comma-separated subreddit list")
    parser.add_argument("--limit", type=int, help="Maximum raw Reddit items to fetch")
    parser.add_argument("--days", type=int, help="Only keep posts from the last N days")
    parser.add_argument("--output", help="Output .xlsx path")
    parser.add_argument("--no-ai", action="store_true", help="Use conservative local extraction only")
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError("pyyaml is required. Run pip install -r requirements.txt") from exc

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a YAML mapping")
    return data


def setup_logging(base_dir: Path, run_id: str) -> Path:
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"run_{run_id}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    root.addHandler(file_handler)
    root.addHandler(console_handler)
    return log_path


def load_dotenv_if_available(base_dir: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv(base_dir / ".env")


def resolve_path(base_dir: Path, value: str | Path | None) -> Path:
    if value is None:
        raise ValueError("Path value is required")
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def fetch_raw_items(
    rss_fetcher: RedditRSSFetcher,
    oauth_fetcher: RedditOAuthFetcher | None,
    queries: list[Any],
    topic: str,
    total_limit: int,
    limit_per_query: int,
    days_back: int,
    raw_path: Path,
    use_rss: bool,
) -> list[dict[str, Any]]:
    if not use_rss:
        LOGGER.warning("RSS fetching is disabled; no primary Reddit collection will run")
        return []

    raw_items: list[dict[str, Any]] = []
    access_blocks = 0
    for query in queries:
        remaining = total_limit - len(raw_items)
        if remaining <= 0:
            break
        try:
            items = rss_fetcher.fetch_search(
                subreddit=query.subreddit,
                query=query.query,
                topic=topic,
                limit=min(limit_per_query, remaining),
                days_back=days_back,
            )
        except RedditAccessError as exc:
            access_blocks += 1
            LOGGER.error("Reddit RSS access error for r/%s %r: %s", query.subreddit, query.query, exc)
            fallback_items = fetch_oauth_search_fallback(
                oauth_fetcher=oauth_fetcher,
                subreddit=query.subreddit,
                query=query.query,
                topic=topic,
                limit=min(limit_per_query, remaining),
                days_back=days_back,
            )
            if fallback_items:
                raw_items.extend(fallback_items)
                append_jsonl(raw_path, fallback_items)
                access_blocks = 0
                continue
            if exc.status_code == 403 and access_blocks >= 3:
                LOGGER.error("Stopping RSS fetch after %s consecutive Reddit access blocks", access_blocks)
                break
            continue
        except Exception as exc:  # noqa: BLE001
            access_blocks = 0
            LOGGER.exception("RSS query failed for r/%s %r: %s", query.subreddit, query.query, exc)
            continue
        raw_items.extend(items)
        append_jsonl(raw_path, items)
        access_blocks = 0
    return raw_items


def fetch_oauth_search_fallback(
    oauth_fetcher: RedditOAuthFetcher | None,
    subreddit: str,
    query: str,
    topic: str,
    limit: int,
    days_back: int,
) -> list[dict[str, Any]]:
    if oauth_fetcher is None:
        return []
    if not oauth_fetcher.is_configured:
        LOGGER.warning(
            "Reddit OAuth fallback is not configured. Add REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to .env."
        )
        return []
    try:
        LOGGER.info("Trying Reddit OAuth fallback for r/%s %r", subreddit, query)
        return oauth_fetcher.fetch_search(
            subreddit=subreddit,
            query=query,
            topic=topic,
            limit=limit,
            days_back=days_back,
        )
    except RedditAPIError as exc:
        LOGGER.error("Reddit OAuth fallback failed for r/%s %r: %s", subreddit, query, exc)
        return []


def fetch_public_comment_fallback(
    raw_items: list[dict[str, Any]],
    topic: str,
    remaining: int,
    raw_path: Path,
    user_agent: str,
) -> list[dict[str, Any]]:
    """Try a small public HTML comment enrichment when RSS under-fills."""

    if remaining <= 0:
        return []
    fetcher = RedditPublicFetcher(user_agent=user_agent)
    comment_items: list[dict[str, Any]] = []

    for item in raw_items[: min(len(raw_items), 10)]:
        if len(comment_items) >= remaining:
            break
        url = str(item.get("url") or "")
        if not url:
            continue
        try:
            comments = fetcher.extract_comments(
                url=url,
                subreddit=str(item.get("subreddit") or ""),
                topic=topic,
                query=str(item.get("query") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Public comment fallback failed for %s: %s", url, exc)
            continue
        comments = comments[: remaining - len(comment_items)]
        comment_items.extend(comments)
        append_jsonl(raw_path, comments)
    return comment_items


def process_items(
    items: list[dict[str, Any]],
    extractor: AIExtractor,
    processed_path: Path,
) -> list[dict[str, Any]]:
    processed_items: list[dict[str, Any]] = []
    for item in items:
        try:
            cleaned = clean_reddit_text(str(item.get("text") or ""))
            title = str(item.get("title") or "")
            filter_result = rule_based_relevance(cleaned["cleaned_text"], title=title)

            processed = dict(item)
            processed.update(cleaned)
            processed["rule_filter"] = asdict(filter_result)

            if filter_result.passed:
                processed["extraction"] = extractor.extract(
                    title=title,
                    text=cleaned["cleaned_text"],
                    metadata={
                        "url": item.get("url"),
                        "subreddit": item.get("subreddit"),
                        "query": item.get("query"),
                    },
                )
            else:
                extraction = default_extraction()
                extraction["relevance_reason"] = filter_result.reason
                processed["extraction"] = extraction

            processed_items.append(processed)
            append_jsonl(processed_path, [processed])
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Processing failed for item %s: %s", item.get("id"), exc)
            continue
    return processed_items


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
