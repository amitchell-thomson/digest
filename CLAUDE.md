# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run briefing commands
uv run python briefing.py generate            # Fetch articles, call Claude, store result
uv run python briefing.py generate --dry-run  # Fetch only, skip Claude call
uv run python briefing.py generate --show     # Generate then display immediately
uv run python briefing.py show                # Display today's stored briefing (no API call)
uv run python briefing.py show --date 2026-04-28
uv run python briefing.py show --list
uv run python briefing.py query "fed rate"

# Lint
uv run ruff check .
uv run ruff format .
```

## Architecture

The pipeline is strictly linear: **fetch → analyse → store → render**. Each stage is its own module with no cross-dependencies except through `briefing.py`, which orchestrates the full flow.

**`briefing.py`** — CLI entry point (Click). Three commands:
- `generate`: runs the full pipeline, saves to SQLite + markdown. Designed for cron — it does not display by default.
- `show`: reads from SQLite only. Zero network calls, zero API cost. This is the normal "read my briefing" path.
- `query`: full-text search across stored briefings and article titles/descriptions.

**`fetch.py`** — Data acquisition. Three sources merged per section:
1. RSS feeds (RSS 2.0 and Atom, fetched in parallel via `ThreadPoolExecutor`)
2. Alpaca News API (optional; silently skipped if keys absent)
3. yfinance (batched ticker download for market snapshot)

Deduplication is done via a shared `seen` set (first 5 title words) across all sources and sections in a single run.

**`analyse.py`** — Single Claude API call per run. All sections are serialised into one structured context block (`build_article_context`), then sent with the `config.yaml` prompt. One call regardless of section count.

**`render.py`** — Rich terminal rendering. The `console` object is exported and used by `briefing.py`. Section headers are colour-coded by matching keywords against `SECTION_COLOURS`. The `⚠ ACTION / ATTENTION` and `WATCH LIST` sections get special styling.

**`store.py`** — Two outputs: SQLite (`~/.briefing_logs/briefings.db`) with `briefings` and `articles` tables, and a daily markdown file (`~/.briefing_logs/YYYY-MM-DD.md`). Search runs LIKE queries across both tables.

## Configuration

All tunable settings live in `config.yaml` — no code changes needed for sources, Claude model, prompt, tickers, or urgency keywords. The prompt in `config.yaml` defines the exact briefing structure Claude must follow.

**Environment variables** (in `.env`):
- `ANTHROPIC_API_KEY` — required
- `ALPACA_KEY_ID` + `ALPACA_SECRET_KEY` — optional, free tier

## Key design decisions

- `generate` and `show` are deliberately split so cron can generate silently and the user reads instantly at any time without waiting for network or API.
- Article deduplication uses the first 5 words of the title (not URL) to catch cross-source reposts.
- RSS parsing handles both RSS 2.0 (`<item>`) and Atom (`<entry>`) with namespace-aware fallbacks.
- Cost is estimated before the Claude call using a token approximation, shown in the status spinner.
