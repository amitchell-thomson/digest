# briefing

> Daily global news digest in the terminal. World news, politics,
> economics, science & tech, business — with a quant research side
> stream. Live market ticker, Claude analysis, searchable logs.
> ~$0.05/day in API costs.

---

## What you get

A single Claude-written briefing every morning, structured as:

```
THE STORY TODAY        — the connecting narrative across the day
WORLD NEWS             — conflicts, diplomacy, major global events
POLITICS               — US, UK, EU and other political developments
ECONOMICS & POLICY     — central banks, macro data, trade
SCIENCE & TECHNOLOGY   — AI, climate, energy, space, health
BUSINESS & MARKETS     — earnings, M&A, sector moves
QUANT RESEARCH         — factor research, systematic strategy, microstructure
WATCH LIST             — developing situations to track
⚠ ACTION / ATTENTION   — only when something genuinely warrants it
```

Plus a live market ticker (S&P, NASDAQ, FTSE, DAX, FX, BTC, gold, oil, US 10Y) at the top of every read.

---

## Quick start

```bash
# 1. Install
uv sync

# 2. Configure
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env

# 3. Run
uv run python briefing.py generate --show
```

---

## Commands

```bash
uv run python briefing.py generate             # Fetch + Claude + store. Designed for cron.
uv run python briefing.py generate --no-market # Skip market ticker (faster)
uv run python briefing.py generate --dry-run   # Fetch articles only, no Claude call
uv run python briefing.py generate --no-log    # Don't save anything
uv run python briefing.py generate --show      # Generate and display immediately

uv run python briefing.py show                 # Display today's stored briefing — instant, no API call
uv run python briefing.py show --date 2026-04-28
uv run python briefing.py show --list          # List all stored dates

uv run python briefing.py query "fed rate"     # Search past briefings + article titles
uv run python briefing.py query "Mali" -n 20
```

---

## Configuration

All settings in `config.yaml` — no code changes needed.

| Setting | What it controls |
|---|---|
| `articles_per_section` | How many articles fetched per section |
| `max_description_chars` | Article body length fed to Claude (more = better analysis, more cost) |
| `max_article_age_days` | Skip articles older than this (default 3 — catches Mon-Wed on Friday) |
| `claude.model` | Claude model — `claude-sonnet-4-6` is the default |
| `claude.max_tokens` | Briefing length cap (default 3000) |
| `market_tickers` | Which prices appear in the ticker bar |
| `sources` | RSS feeds per section — add/remove freely |
| `prompt` | The analyst instruction sent to Claude — edit tone/focus/structure here |
| `urgent_keywords` | What triggers ⚠ flags in the urgency scan |
| `alpaca.topics` | Topic queries per section for the Alpaca News overlay |

---

## Sources

| Section | Sources |
|---|---|
| World News | BBC · Al Jazeera · Guardian · France 24 · SCMP · Reuters (via Google News) |
| Politics | BBC Politics · Guardian Politics · NPR Politics · Politico |
| Economics & Policy | Fed · BoE · ECB · BIS · IMF · Calculated Risk · Econbrowser |
| Science & Technology | Ars Technica · The Verge · MIT Tech Review · Wired · BBC Science |
| Business & Markets | BBC Business · Guardian Business · SEC 8-K · Google News (earnings/M&A) |
| Quant Research | Alpha Architect · Quantocracy · Robot Wealth |
| + Markets overlay | Alpaca News API (optional, free) |

Articles older than `max_article_age_days` are filtered out at the fetch stage so stale content can't pollute the briefing.

---

## API keys

| Key | Required | Where |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | console.anthropic.com |
| `ALPACA_KEY_ID` + `ALPACA_SECRET_KEY` | No | alpaca.markets (free, no card) |

---

## Automation (home server)

```bash
# Cron: 07:00 every day (generates and stores silently)
crontab -e
0 7 * * * cd /path/to/digest && /home/you/.local/bin/uv run python briefing.py generate >> ~/.briefing_logs/cron.log 2>&1

# Read it from anywhere — instant, reads from SQLite, no API call
uv run python briefing.py show

# Or via SSH from your laptop:
alias briefing="ssh yourserver 'cd /path/to/digest && uv run python briefing.py show'"
```

`generate` and `show` are deliberately split: cron generates silently overnight, you read instantly any time without waiting for network or API.

---

## Architecture

Linear pipeline, four modules, one orchestrator:

- `fetch.py` — RSS feeds (parallel) + Alpaca News API + yfinance market data. Date-filtered, deduplicated by title.
- `analyse.py` — single Claude call with all articles serialised into one structured context block.
- `store.py` — SQLite (`briefings` + `articles` tables) and per-day markdown.
- `render.py` — Rich terminal output with section colour coding.
- `briefing.py` — Click CLI orchestrator.

Re-running `generate` on the same date appends a new row to SQLite (history retained); the markdown file is overwritten.

---

## Storage

- **SQLite**: `~/.briefing_logs/briefings.db` — queryable via `briefing query`. Two tables: `briefings` (one row per run) and `articles` (one row per fetched article).
- **Markdown**: `~/.briefing_logs/YYYY-MM-DD.md` — one file per day, overwritten on re-run.

---

## Cost

~$0.05/day with default settings (claude-sonnet-4-6, 6 articles/section,
3000 max output tokens). Shown in the footer of each run.

Adjust `max_description_chars`, `articles_per_section`, and `claude.max_tokens` in `config.yaml` to control the cost/quality tradeoff.

---

## Lint / format

```bash
uv run ruff check .
uv run ruff format .
```
