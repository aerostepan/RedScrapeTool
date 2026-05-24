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
data/exports/reddit_market_research_notebooklm.md
```

The workbook includes `Product Overview` and `Product Concepts` sheets generated from the collected demand signals. Product ideas, MVP features, revenue hypotheses, and validation experiments are hypotheses derived from the evidence rather than proof of market demand.

## NotebookLM Exploration

Each run creates a structured Markdown source for NotebookLM alongside the Excel workbook. The document organizes the same research as a hierarchy of product concepts, opportunity rankings, pain points, desired features, competitor weaknesses, and source evidence.

To explore it interactively in standard NotebookLM:

1. Open a notebook at `https://notebooklm.google.com/`.
2. Add the generated `_notebooklm.md` file as a source.
3. Generate a Mind Map in NotebookLM.
4. Expand branches or click a node to ask questions about the underlying evidence.

Standard NotebookLM does not expose an official API for automatically creating a personal notebook or generating its Mind Map. NotebookLM Enterprise has programmatic notebook and source management APIs; that can be added as an optional deployment integration if you use Google Cloud Enterprise access.

## Google And Telegram Publishing

The tool can publish the finished report as a Google Sheet, update a reusable Google Doc for NotebookLM, and send both links through a Telegram bot. For standard NotebookLM, the final Drive-source sync and Mind Map regeneration remain manual.

Set up Google once:

1. Enable the Google Sheets API, Google Docs API, and Google Drive API in your Google Cloud project.
2. Create an OAuth client with application type `Desktop app`.
3. Save the downloaded JSON file as `secrets/google_oauth_client.json`.
4. If the OAuth consent screen is in testing mode, add your Google account as a test user.

Add these values to `.env`:

```bash
GOOGLE_OAUTH_CLIENT_FILE="secrets/google_oauth_client.json"
GOOGLE_TOKEN_FILE="secrets/google_token.json"
GOOGLE_DRIVE_FOLDER_ID=""
GOOGLE_SHARE_WITH_LINK=true

TELEGRAM_BOT_TOKEN="paste-your-bot-token-here"
TELEGRAM_CHAT_ID="@your_channel_username"
NOTEBOOKLM_NOTEBOOK_URL="paste-your-shared-notebook-link-here"
```

For a private Telegram group or a channel without a public username, use its numeric chat ID instead of `@your_channel_username`. The bot must be an administrator with permission to post.

Enable publishing in the YAML config used for your run:

```yaml
local_exports:
  excel: false
  notebooklm_markdown: false

google_workspace:
  enabled: true
  reuse_notebook_source_doc: true
  share_with_link: true

telegram:
  enabled: true
```

On the first Google publish, a browser window asks you to authorize the local app. The generated token is saved to `secrets/google_token.json`, which is ignored by Git. The first run also creates a Google Doc for the NotebookLM source. Add that Doc to your NotebookLM notebook once, generate its Mind Map, and keep the notebook link in `NOTEBOOKLM_NOTEBOOK_URL`. Future runs update the same Doc automatically; in NotebookLM, select the Drive source sync action and regenerate the Mind Map.

To test publishing from an already collected raw checkpoint without running Selenium again:

```bash
python main.py \
  --config configs/ev_charger_rebate_research.yaml \
  --input-raw data/raw/raw_YYYYMMDD_HHMMSS.jsonl \
  --publish-google \
  --notify-telegram \
  --no-local-excel
```

## Browser Fallback Mode

If Reddit blocks RSS/API access, run the public Selenium fallback:

```bash
python main.py \
  --topic "study tools for college students" \
  --subreddits "college,GetStudying,GradSchool,students" \
  --limit 100 \
  --days 180 \
  --fetch-mode browser \
  --output data/exports/students_browser_test.xlsx
```

Browser mode opens public Reddit pages slowly, extracts only visible public content, saves partial progress continuously, and stops on CAPTCHA, login wall, rate limit, or block pages. It does not bypass access controls.

Browser mode intentionally uses broader searches than RSS mode. For example, instead of requiring an exact match like `"study tools for college students" "why doesn't"`, it searches shorter phrases such as `study tools`, `study app`, `looking for`, `too expensive`, and `manual`, then lets the filter/AI pipeline decide relevance.

Browser mode also rotates among configured subreddits before allowing one community to fill the result limit. Keep `browser_fallback.sample_all_subreddits_first: true` when you want coverage across all requested communities.

For a longer product-discovery run, the included EV charger installation preset tests demand around comparing electrician quotes and managing permits, rebates, tax credits, and receipts:

```bash
python main.py --config configs/ev_charger_rebate_research.yaml
```

The preset gathers up to 72 visible items across `r/evcharging`, `r/electricvehicles`, `r/homeowners`, and `r/HomeImprovement`. It is a market hypothesis to validate from the resulting evidence, not a conclusion that the niche is already proven.

If browser mode is interrupted after saving raw items, process the checkpoint without scraping again:

```bash
python main.py \
  --input-raw data/raw/raw_YYYYMMDD_HHMMSS.jsonl \
  --output data/exports/recovered_run.xlsx
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
--notebook-output
--no-local-excel
--config
--no-ai
--no-notebooklm
--publish-google
--notify-telegram
```

## Data Files

During each run, the tool writes partial progress:

- `data/raw/raw_YYYYMMDD_HHMMSS.jsonl`
- `data/processed/processed_YYYYMMDD_HHMMSS.jsonl`
- `logs/run_YYYYMMDD_HHMMSS.log`

These files make failed or interrupted runs easier to audit.
