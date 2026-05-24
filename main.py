"""CLI entrypoint for the Reddit market research tool."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from export.excel_export import build_market_research_tables, export_market_research_workbook
from export.notebooklm_export import export_notebooklm_source, render_notebooklm_source
from integrations.google_workspace import GooglePublishResult, GoogleWorkspacePublisher
from integrations.telegram import TelegramNotifier
from processing.ai_extract import AIExtractor, default_extraction
from processing.clean_text import clean_reddit_text
from processing.cluster import (
    cluster_signals,
    competitor_weakness_summary,
    desired_feature_summary,
    top_opportunities,
)
from processing.product_synthesis import ProductSynthesizer
from processing.relevance_filter import rule_based_relevance
from scouting.reddit_api import RedditAPIError, RedditOAuthFetcher
from scouting.reddit_public import RedditPublicFetcher
from scouting.reddit_rss import RedditAccessError, RedditRSSFetcher
from scouting.reddit_search import build_browser_search_queries, build_search_queries, normalize_subreddits
from scouting.selenium_fallback import BrowserBlockedError, SeleniumFallback
from storage.dedupe import DedupeStore
from storage.json_store import append_jsonl, read_jsonl
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
    browser_config = config.get("browser_fallback", {}) or {}
    notebooklm_config = config.get("notebooklm", {}) or {}
    google_config = config.get("google_workspace", {}) or {}
    telegram_config = config.get("telegram", {}) or {}
    local_export_config = config.get("local_exports", {}) or {}
    search_phrases = config.get("search_phrases") or None
    fetch_mode = args.fetch_mode or config.get("fetch_mode", "auto")
    google_enabled = args.publish_google or bool(google_config.get("enabled", False))
    google_publisher: GoogleWorkspacePublisher | None = None
    if google_enabled:
        google_publisher = GoogleWorkspacePublisher(base_dir=base_dir, config=google_config)
        google_publisher.validate_configuration()
    telegram_enabled = args.notify_telegram or bool(telegram_config.get("enabled", False))
    telegram_notifier: TelegramNotifier | None = None
    if telegram_enabled:
        telegram_notifier = TelegramNotifier(telegram_config)
        telegram_notifier.validate_configuration()

    queries = build_search_queries(topic, subreddits, search_phrases)
    LOGGER.info("Generated %s Reddit search queries", len(queries))

    rss_fetcher = RedditRSSFetcher(user_agent=reddit_config.get("user_agent", "market-research-tool/0.1"))
    oauth_fetcher = RedditOAuthFetcher(user_agent=reddit_config.get("user_agent") or None)
    if reddit_config.get("use_oauth_fallback", True) and oauth_fetcher.is_configured:
        LOGGER.info("Reddit OAuth fallback is configured")
    elif reddit_config.get("use_oauth_fallback", True):
        LOGGER.info("Reddit OAuth fallback is enabled but missing REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET")

    raw_items: list[dict[str, Any]] = []
    used_browser = False

    if args.input_raw:
        input_raw_path = resolve_path(base_dir, args.input_raw)
        raw_items = read_jsonl(input_raw_path)
        LOGGER.info("Loaded %s raw items from checkpoint %s", len(raw_items), input_raw_path)
    elif fetch_mode in {"auto", "rss"}:
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

    browser_enabled = fetch_mode == "browser" or bool(browser_config.get("enabled", False))
    if not args.input_raw and browser_enabled and len(raw_items) < total_limit and fetch_mode in {"auto", "browser"}:
        used_browser = True
        browser_queries = build_browser_search_queries(
            topic=topic,
            subreddits=subreddits,
            search_phrases=search_phrases,
            max_queries_per_subreddit=int(browser_config.get("max_queries_per_subreddit", 12)),
        )
        LOGGER.info("Generated %s broad browser queries", len(browser_queries))
        browser_items = fetch_browser_items(
            queries=browser_queries,
            topic=topic,
            total_limit=total_limit - len(raw_items),
            limit_per_query=limit_per_query,
            days_back=days_back,
            raw_path=raw_path,
            browser_config=browser_config,
        )
        raw_items.extend(browser_items)

    if (
        not args.input_raw
        and not used_browser
        and reddit_config.get("use_public_pages", True)
        and len(raw_items) < total_limit
    ):
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
        local_path=(
            base_dir / "data" / "processed" / f"dedupe_import_{run_id}.json"
            if args.input_raw
            else base_dir / "data" / "processed" / "dedupe_seen.json"
        ),
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
    synthesizer = ProductSynthesizer(
        provider=ai_config.get("provider", "openai"),
        model=ai_config.get("synthesis_model", ai_config.get("model", "gpt-4.1-mini")),
        temperature=float(ai_config.get("temperature", 0)),
        prompt_path=base_dir / "prompts" / "product_synthesis_prompt.md",
        enabled=not args.no_ai,
    )
    product_synthesis = synthesizer.synthesize(
        topic=topic,
        subreddits=subreddits,
        processed_items=processed_items,
        clusters=clusters,
        desired_features=desired_features,
        competitor_weaknesses=competitor_weaknesses,
        opportunities=opportunities,
    )

    tables = build_market_research_tables(
        raw_items=raw_items,
        processed_items=processed_items,
        clusters=clusters,
        desired_features=desired_features,
        competitor_weaknesses=competitor_weaknesses,
        top_opportunities=opportunities,
        product_synthesis=product_synthesis,
    )
    local_excel_enabled = not args.no_local_excel and bool(local_export_config.get("excel", True))
    if local_excel_enabled:
        export_market_research_workbook(
            output_path=output_path,
            raw_items=raw_items,
            processed_items=processed_items,
            clusters=clusters,
            desired_features=desired_features,
            competitor_weaknesses=competitor_weaknesses,
            top_opportunities=opportunities,
            product_synthesis=product_synthesis,
        )
        LOGGER.info("Excel exported to %s", output_path)

    notebooklm_path: Path | None = None
    notebooklm_enabled = not args.no_notebooklm and bool(
        local_export_config.get("notebooklm_markdown", True)
    ) and (
        bool(notebooklm_config.get("enabled", True)) or bool(args.notebook_output)
    )
    notebook_source = ""
    if notebooklm_enabled or google_enabled:
        notebook_source = render_notebooklm_source(
            topic=topic,
            subreddits=subreddits,
            raw_items=raw_items,
            processed_items=processed_items,
            clusters=clusters,
            desired_features=desired_features,
            competitor_weaknesses=competitor_weaknesses,
            top_opportunities=opportunities,
            product_synthesis=product_synthesis,
        )
    if notebooklm_enabled:
        configured_notebook_path = args.notebook_output or notebooklm_config.get("output_path")
        notebooklm_path = (
            resolve_path(base_dir, configured_notebook_path)
            if configured_notebook_path
            else output_path.with_name(f"{output_path.stem}_notebooklm.md")
        )
        export_notebooklm_source(
            output_path=notebooklm_path,
            topic=topic,
            subreddits=subreddits,
            raw_items=raw_items,
            processed_items=processed_items,
            clusters=clusters,
            desired_features=desired_features,
            competitor_weaknesses=competitor_weaknesses,
            top_opportunities=opportunities,
            product_synthesis=product_synthesis,
        )
        LOGGER.info("NotebookLM source exported to %s", notebooklm_path)

    google_result: GooglePublishResult | None = None
    if google_publisher:
        google_result = google_publisher.publish_report(
            topic=topic,
            tables=tables,
            notebook_source=notebook_source,
        )
        LOGGER.info("Google Sheet report published to %s", google_result.spreadsheet_url)
        LOGGER.info("Google Doc NotebookLM source published to %s", google_result.notebook_source_doc_url)

    if telegram_notifier:
        notebook_url = str(
            os.getenv("NOTEBOOKLM_NOTEBOOK_URL")
            or telegram_config.get("notebooklm_notebook_url")
            or notebooklm_config.get("notebook_url")
            or ""
        ).strip()
        telegram_notifier.send_research_report(
            topic=topic,
            summary=str(product_synthesis.get("research_summary") or ""),
            spreadsheet_url=google_result.spreadsheet_url if google_result else None,
            source_doc_url=google_result.notebook_source_doc_url if google_result else None,
            notebook_url=notebook_url or None,
        )
        LOGGER.info("Telegram report notification sent")

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
    if local_excel_enabled:
        print(f"Excel exported to: {output_path}")
    if notebooklm_path:
        print(f"NotebookLM source exported to: {notebooklm_path}")
    if google_result:
        print(f"Google Sheet report: {google_result.spreadsheet_url}")
        print(f"Google Doc NotebookLM source: {google_result.notebook_source_doc_url}")
    if telegram_enabled:
        print("Telegram notification sent")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a Reddit-only market research Excel workbook.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--topic", help="Research topic")
    parser.add_argument("--subreddits", help="Comma-separated subreddit list")
    parser.add_argument("--limit", type=int, help="Maximum raw Reddit items to fetch")
    parser.add_argument("--days", type=int, help="Only keep posts from the last N days")
    parser.add_argument("--output", help="Output .xlsx path")
    parser.add_argument("--notebook-output", help="Output Markdown source file for NotebookLM")
    parser.add_argument("--no-local-excel", action="store_true", help="Skip local Excel export")
    parser.add_argument(
        "--input-raw",
        help="Process a saved raw JSONL checkpoint instead of collecting new Reddit items",
    )
    parser.add_argument(
        "--fetch-mode",
        choices=["auto", "rss", "browser"],
        help="Collection mode: auto tries RSS/API first; browser uses Selenium public pages",
    )
    parser.add_argument("--no-ai", action="store_true", help="Use conservative local extraction only")
    parser.add_argument("--no-notebooklm", action="store_true", help="Skip NotebookLM Markdown export")
    parser.add_argument("--publish-google", action="store_true", help="Publish report to Google Sheets and Docs")
    parser.add_argument("--notify-telegram", action="store_true", help="Send published report links to Telegram")
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
    logging.getLogger("google_auth_oauthlib.flow").setLevel(logging.WARNING)

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


def fetch_browser_items(
    queries: list[Any],
    topic: str,
    total_limit: int,
    limit_per_query: int,
    days_back: int,
    raw_path: Path,
    browser_config: dict[str, Any],
) -> list[dict[str, Any]]:
    if total_limit <= 0:
        return []

    fetcher = SeleniumFallback(
        enabled=True,
        timeout=int(browser_config.get("timeout", 30)),
        headless=bool(browser_config.get("headless", False)),
        min_delay_seconds=float(browser_config.get("min_delay_seconds", 45)),
        max_delay_seconds=float(browser_config.get("max_delay_seconds", 120)),
        scrolls_per_search=int(browser_config.get("scrolls_per_search", 2)),
        open_post_pages=bool(browser_config.get("open_post_pages", True)),
        max_comments_per_post=int(browser_config.get("max_comments_per_post", 5)),
        sample_all_subreddits_first=bool(browser_config.get("sample_all_subreddits_first", True)),
    )
    try:
        return fetcher.fetch_queries(
            queries=queries,
            topic=topic,
            total_limit=total_limit,
            limit_per_query=limit_per_query,
            days_back=days_back,
            on_items=lambda items: append_jsonl(raw_path, items),
        )
    except BrowserBlockedError as exc:
        LOGGER.error("Browser fallback stopped because Reddit challenged or blocked the session: %s", exc)
        return []


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
                        "research_topic": item.get("topic"),
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
