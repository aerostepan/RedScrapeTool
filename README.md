# Reddit Market Research Tool

Local Python CLI for collecting public Reddit demand signals, filtering them for startup-relevant pain points, extracting structured market research fields, clustering repeated signals, and exporting a multi-sheet Excel workbook.

The first version is intentionally Reddit-only. It prefers public RSS feeds, uses lightweight public-page fetching only as enrichment, and keeps Selenium/CDP behind explicit fallback abstractions.

## Install

```bash
cd reddit_market_research_tool
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For model-backed extraction, set an OpenAI API key:

```bash
export OPENAI_API_KEY="..."
```

If no API key is present, the CLI still produces a workbook using a conservative rule-based extractor with low confidence scores. That fallback is useful for smoke tests but the best results come from the configured AI provider.

If Reddit returns HTTP 403 for public RSS, add official Reddit app credentials to `.env`:

```bash
REDDIT_CLIENT_ID="..."
REDDIT_CLIENT_SECRET="..."
REDDIT_USER_AGENT="market-research-tool/0.1 by your-reddit-username"
```

Create the app at `https://www.reddit.com/prefs/apps` and use an app type that provides both a client ID and client secret. The tool uses application credentials only and does not need your Reddit password.

## Run

```bash
python main.py \
  --topic "AI video editing tools" \
  --subreddits "VideoEditing,NewTubers,ContentCreators,Entrepreneur" \
  --limit 500 \
  --days 90 \
  --output data/exports/reddit_market_research.xlsx
```

The script prints a final summary and writes:

```text
data/exports/reddit_market_research.xlsx
```

## Output Sheets

- Raw Reddit Data
- Filtered Signals
- Pain Point Clusters
- Desired Features
- Competitor Weaknesses
- Top Opportunities

Each sheet freezes the header row, enables filters, wraps long text, and uses readable column widths.

## Fetching Strategy

1. Reddit RSS search feeds are used first.
2. Official Reddit OAuth read API is used when public RSS returns access errors and app credentials are configured.
3. Public Reddit HTML fetching via `curl_cffi` is available for optional enrichment.
4. SeleniumBase and CDP are implemented as opt-in fallback layers, not the primary scraper.

The tool does not scrape private or login-only content, automate accounts, farm credentials, or attempt aggressive CAPTCHA bypassing.

## Configuration

Edit `config.yaml` to set the default topic, subreddits, search phrases, Reddit fetch settings, Redis settings, and AI model settings.

CLI flags override the config:

```text
--topic
--subreddits
--limit
--days
--output
--config
--no-ai
```

## Data Files

During each run, the tool writes partial progress:

- `data/raw/raw_YYYYMMDD_HHMMSS.jsonl`
- `data/processed/processed_YYYYMMDD_HHMMSS.jsonl`
- `logs/run_YYYYMMDD_HHMMSS.log`

These files make failed or interrupted runs easier to audit.
