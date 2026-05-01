# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run briefing commands
uv run digest                          # Show most recent briefing — instant, no API call
uv run digest --date 2026-04-28        # Show a specific past date
uv run digest --list                   # List all stored briefing dates
uv run digest --query "fed rate"       # Search past briefings + article titles
uv run digest --query "Mali" -n 20     # Search with a result limit

uv run digest --generate               # Fetch + Claude + store. Designed for cron.
uv run digest --generate --dry-run     # Fetch articles only, no Claude call
uv run digest --generate --no-market   # Skip market ticker
uv run digest --generate --no-log      # Don't save anything to disk

# Lint
uv run ruff check .
uv run ruff format .
```

## Architecture

The pipeline is strictly linear: **fetch → analyse → store → render**. Each stage is its own module with no cross-dependencies except through `briefing.py`, which orchestrates the full flow.

**`briefing.py`** — Click CLI, installed as the `digest` command. Two modes:
- `--generate`: runs the full pipeline, saves to SQLite + markdown. Designed for cron — does not display by default.
- Default (no flags): reads from SQLite only. Zero network calls, zero API cost. This is the normal "read my briefing" path. Falls back to yesterday, then the most recent stored date, if today has no briefing.

**`fetch.py`** — Data acquisition. Three sources merged per section:
1. RSS feeds (RSS 2.0 and Atom, fetched in parallel via `ThreadPoolExecutor`)
2. Alpaca News API (optional; silently skipped if keys absent)
3. yfinance (batched ticker download for market snapshot)

Two levels of deduplication:
- **Within-run**: a shared `seen` set (first 5 title words, lowercased) is passed across all sources and all sections in a single run. First occurrence wins.
- **Cross-day**: before fetching, `get_recent_article_keys` loads title fingerprints of articles stored in previous days' briefings (not today's) from SQLite. Articles matching those fingerprints are filtered out before Claude sees them. Same-day re-runs are intentionally not filtered — re-running on the same day gives a full, unfiltered article set.

**`analyse.py`** — Single Claude API call per run. All sections are serialised into one structured context block (`build_article_context`), then sent with the `config.yaml` prompt. One call regardless of section count.

**`render.py`** — Rich terminal rendering. The `console` object is exported and used by `briefing.py`. Section headers are colour-coded by matching keywords against `SECTION_COLOURS`. The `⚠ ACTION / ATTENTION` and `WATCH LIST` sections get special styling.

**`store.py`** — Two outputs: SQLite (`~/.briefing_logs/briefings.db`) with `briefings` and `articles` tables, and a daily markdown file (`~/.briefing_logs/YYYY-MM-DD.md`). Re-running `--generate` on the same date appends a new row to SQLite (full history retained) and overwrites the markdown file. `load_briefing` always returns the most recent row for a given date. Search runs LIKE queries across both tables, returning article matches and briefing text excerpts.

## Configuration

All tunable settings live in `config.yaml` — no code changes needed for sources, Claude model, prompt, tickers, or urgency keywords. The prompt in `config.yaml` defines the exact briefing structure Claude must follow.

**Environment variables** (in `.env`):
- `ANTHROPIC_API_KEY` — required
- `ALPACA_KEY_ID` + `ALPACA_SECRET_KEY` — optional, free tier

## Key design decisions

- `--generate` and the default read are deliberately split: cron generates silently, the user reads instantly at any time without waiting for network or API.
- Cross-day deduplication is scoped to previous dates only, so same-day re-runs (e.g. after a cron failure) see the full article set rather than an empty one.
- Article deduplication uses the first 5 words of the title (not URL) to catch cross-source reposts of the same story.
- RSS parsing handles both RSS 2.0 (`<item>`) and Atom (`<entry>`) with namespace-aware fallbacks.
- Cost is estimated before the Claude call using a token approximation, shown in the status spinner.
- `max_article_age_days: 3` in config ensures Monday's briefing catches Friday afternoon articles that wouldn't have appeared in the Friday run.
