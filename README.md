# briefing

> Bloomberg-style terminal briefing. Primary sources, live market data,
> Claude analysis, searchable logs.
> ~$0.03/day in API costs.

---

## Quick start

```bash
# 1. Install
uv sync

# 2. Configure
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env

# 3. Run
python briefing.py generate
```

---

## Commands

```bash
python briefing.py generate             # Fetch articles, call Claude, store result
python briefing.py generate --no-market # Skip market ticker (faster)
python briefing.py generate --dry-run   # Fetch articles only, no Claude call
python briefing.py generate --no-log    # Don't save anything
python briefing.py generate --show      # Generate and display immediately

python briefing.py show                 # Display today's stored briefing (instant, no API call)
python briefing.py show --date 2026-04-28  # Show a specific past date
python briefing.py show --list          # List all available dates

python briefing.py query "fed rate"     # Search all past briefings
python briefing.py query "BoE" -n 20   # More results
```

---

## Configuration

All settings in `config.yaml` — no code changes needed for:

| Setting | What it controls |
|---|---|
| `articles_per_section` | How many articles fetched per section |
| `max_description_chars` | Article length fed to Claude (more = better analysis, more cost) |
| `claude.model` | Claude model — sonnet is the best balance |
| `market_tickers` | Which prices appear in the ticker bar |
| `sources` | RSS feeds per section — add/remove freely |
| `prompt` | The analyst instruction sent to Claude — edit tone/focus here |
| `urgent_keywords` | What triggers ⚠ flags |

---

## Sources

| Section | Sources |
|---|---|
| Macro & Central Banks | Fed · BoE · ECB · BIS · IMF · Calculated Risk · Econbrowser |
| AI & Technology | Hacker News (filtered RSS) |
| Equities & Markets | SEC 8-K filings · Alpha Architect · HN filtered |
| Geopolitics | HN filtered · World Bank · Marginal Revolution |
| Quant & Fintech | Alpha Architect · HN filtered |
| + Markets overlay | Alpaca News API (optional, free) |

---

## API keys

| Key | Required | Where |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | console.anthropic.com |
| `ALPACA_KEY_ID` + `ALPACA_SECRET_KEY` | No | alpaca.markets (free, no card) |

---

## Automation (home server)

```bash
# Cron: 07:00 Mon–Fri (generates and stores silently)
crontab -e
0 7 * * 1-5 cd /path/to/digest && python briefing.py generate >> ~/.briefing_logs/cron.log 2>&1

# Read it from anywhere via SSH (instant — reads from SQLite, no API call)
ssh yourserver 'cd /path/to/digest && python briefing.py show'

# Or alias on your local machine:
alias briefing="ssh yourserver 'cd /path/to/digest && python briefing.py show'"
```

---

## Storage

- **SQLite**: `~/.briefing_logs/briefings.db` — queryable via `briefing query`
- **Markdown**: `~/.briefing_logs/YYYY-MM-DD.md` — one file per day

---

## Cost

~$0.03/day with default settings (claude-sonnet-4-5, 6 articles/section).
Shown in the footer of each run.

Adjust `max_description_chars` and `articles_per_section` in config.yaml
to control cost vs quality tradeoff.
