#!/usr/bin/env python3
"""
briefing.py — Daily executive news briefing for the terminal.

Commands:
    python briefing.py generate         # Fetch + analyse + store. Run via cron.
    python briefing.py show             # Display today's stored briefing. Instant.
    python briefing.py show --date 2026-04-28   # Show a specific past date
    python briefing.py show --list      # List all available dates
    python briefing.py query "fed rate" # Search past briefings
    python briefing.py generate --dry-run       # Fetch only, skip Claude

Typical setup:
    # Cron at 07:00 Mon-Fri (generates and stores)
    0 7 * * 1-5 cd /path/to/briefing_v3 && python briefing.py generate

    # Whenever you want to read it (instant, no API call)
    python briefing.py show

    # From local machine via SSH alias
    alias briefing="ssh yourserver 'cd /path/to/briefing_v3 && python briefing.py show'"
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import click
import yaml
from dotenv import load_dotenv
from rich.console import Console

load_dotenv(Path(__file__).parent / ".env")

from analyse import is_urgent, run_analysis  # noqa: E402
from fetch import fetch_all_sections, fetch_market_snapshot  # noqa: E402
from render import (  # noqa: E402
    console,
    render_briefing,
    render_footer,
    render_header,
    render_market_ticker,
)
from store import (  # noqa: E402
    get_db,
    list_briefing_dates,
    load_briefing,
    save_briefing,
    save_markdown,
    search_briefings,
)

err_console = Console(stderr=True)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        err_console.print(f"[red]Config not found:[/] {config_path}")
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def estimate_cost(article_count: int, max_chars: int, max_tokens: int) -> float:
    input_tokens = (article_count * max_chars) / 4
    output_tokens = max_tokens * 0.6
    return (input_tokens * 3 + output_tokens * 15) / 1_000_000


def get_log_dir(cfg: dict) -> Path:
    log_dir = Path(cfg.get("log", {}).get("directory", "~/.briefing_logs")).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────


@click.group()
def cli():
    """Daily executive news briefing."""
    pass


# ── GENERATE ──────────────────────────────────────────────────
# Fetches, calls Claude, stores result. Designed for cron.
# Does NOT display — just generates and saves silently.
# Use `show` to display.


@cli.command()
@click.option("--config", "-c", default="config.yaml")
@click.option("--no-market", is_flag=True, help="Skip live market data")
@click.option("--no-log", is_flag=True, help="Don't save to disk")
@click.option("--dry-run", is_flag=True, help="Fetch articles only, skip Claude call")
@click.option("--show", is_flag=True, help="Display immediately after generating")
def generate(
    config: str,
    no_market: bool,
    no_log: bool,
    dry_run: bool,
    show: bool,
):
    """Fetch news, call Claude, store the result. Designed for cron at 07:00."""

    cfg = load_config(Path(config))
    articles_n = cfg.get("articles_per_section", 6)
    max_chars = cfg.get("max_description_chars", 1500)
    claude_cfg = cfg.get("claude", {})
    model = claude_cfg.get("model", "claude-sonnet-4-5")
    max_tokens = claude_cfg.get("max_tokens", 2000)
    prompt = cfg.get("prompt", "Summarise today's news.")
    sources = cfg.get("sources", {})
    alpaca_cfg = cfg.get("alpaca", {})
    urgent_kws = cfg.get("urgent_keywords", [])
    tickers = cfg.get("market_tickers", {})
    log_dir = get_log_dir(cfg)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # ── 1. Fetch articles ──────────────────────────────────────
    max_age_days = cfg.get("max_article_age_days", 0)
    with console.status("[dim]Fetching articles…[/]"):
        sections = fetch_all_sections(
            sources=sources,
            alpaca_config=alpaca_cfg,
            articles_per_section=articles_n,
            max_chars=max_chars,
            max_age_days=max_age_days,
        )

    total_articles = sum(len(v) for v in sections.values())

    # ── 2. Market data ─────────────────────────────────────────
    market_data = []
    if not no_market and tickers:
        with console.status("[dim]Fetching market data…[/]"):
            market_data = fetch_market_snapshot(tickers)

    # ── 3. Urgency scan ────────────────────────────────────────
    all_titles = [a["title"] for arts in sections.values() for a in arts]
    urgent_titles = [t for t in all_titles if is_urgent(t, urgent_kws)]

    # ── 4. Claude ──────────────────────────────────────────────
    briefing_text = ""
    cost = 0.0

    if dry_run:
        console.print("[yellow]--dry-run: skipping Claude call.[/]")
        for section, articles in sections.items():
            console.print(f"\n[bold]{section}[/] ({len(articles)} articles)")
            for a in articles:
                console.print(f"  • {a['title'][:90]}")
        return

    cost = estimate_cost(total_articles, max_chars, max_tokens)
    with console.status(f"[dim]Calling Claude (~${cost:.3f})…[/]"):
        try:
            briefing_text = run_analysis(
                sections=sections,
                prompt=prompt,
                model=model,
                max_tokens=max_tokens,
                run_date=date_str,
            )
        except Exception as e:
            err_console.print(f"[red]Claude API error:[/] {e}")
            sys.exit(1)

    # ── 5. Store ───────────────────────────────────────────────
    log_path = None

    if not no_log:
        conn = get_db(log_dir)
        save_briefing(
            conn,
            date_str,
            briefing_text,
            sections,
            urgent_titles,
            model,
            market_data,
        )
        log_path = save_markdown(log_dir, date_str, briefing_text, market_data)

    console.print(
        f"[green]✓[/] Generated briefing for {date_str} "
        f"({total_articles} articles, ~${cost:.3f})"
    )
    if log_path:
        console.print(f"  [dim]saved → {log_path}[/]")

    # ── 6. Optional immediate display ──────────────────────────
    if show:
        _display_briefing(
            briefing_text, market_data, model, total_articles, log_path, cost
        )


# ── SHOW ──────────────────────────────────────────────────────
# Reads from SQLite. Zero network calls. Zero API cost. Instant.


@cli.command()
@click.option("--config", "-c", default="config.yaml")
@click.option(
    "--date", "-d", default=None, help="Date to show (YYYY-MM-DD). Defaults to today."
)
@click.option(
    "--list", "list_dates", is_flag=True, help="List all available briefing dates."
)
def show(config: str, date: str, list_dates: bool):
    """Display a stored briefing. Instant — reads from SQLite, no API calls."""

    cfg = load_config(Path(config))
    log_dir = get_log_dir(cfg)
    conn = get_db(log_dir)

    # ── List mode ──────────────────────────────────────────────
    if list_dates:
        dates = list_briefing_dates(conn)
        if not dates:
            console.print(
                "[yellow]No briefings stored yet. Run: python briefing.py generate[/]"
            )
            return
        console.print()
        for d in dates:
            console.print(f"  [dim]{d}[/]  →  briefing.py show --date {d}")
        console.print()
        return

    # ── Load briefing ──────────────────────────────────────────
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    row = load_briefing(conn, date_str)

    if not row:
        # Helpful fallback: tell them what dates exist
        dates = list_briefing_dates(conn)
        if dates:
            console.print(f"[yellow]No briefing found for {date_str}.[/]")
            console.print(
                f"[dim]Most recent: {dates[0]}  →  briefing.py show --date {dates[0]}[/]"
            )
        else:
            console.print(
                "[yellow]No briefings stored yet. Run: python briefing.py generate[/]"
            )
        return

    _display_briefing(
        briefing_text=row["briefing"],
        market_data=row["market_data"],
        model=row["model"],
        article_count=row["article_count"],
        log_path=None,
        cost=0.0,
        stored_at=row["run_time"],
        date_str=date_str,
    )


def _display_briefing(
    briefing_text: str,
    market_data: list,
    model: str,
    article_count: int,
    log_path,
    cost: float,
    stored_at: str | None = None,
    date_str: str | None = None,
) -> None:
    """Shared display logic for both generate --show and show."""
    alpaca_active = bool(os.getenv("ALPACA_KEY_ID"))

    render_header(model, article_count, alpaca_active)

    if stored_at and date_str:
        from rich.text import Text

        t = Text()
        t.append(f"  Generated {stored_at[:16].replace('T', ' ')} UTC", style="dim")
        console.print(t)
        console.print()

    if market_data:
        render_market_ticker(market_data)

    render_briefing(briefing_text)
    render_footer(log_path, cost)


# ── QUERY ─────────────────────────────────────────────────────


@cli.command()
@click.argument("query_text")
@click.option("--config", "-c", default="config.yaml")
@click.option("--limit", "-n", default=10, show_default=True)
def query(query_text: str, config: str, limit: int):
    """Search past briefings. Example: briefing.py query "fed rate cut" """
    cfg = load_config(Path(config))
    log_dir = get_log_dir(cfg)

    results = search_briefings(log_dir, query_text, limit)

    if not results:
        console.print(f"[dim]No results for:[/] {query_text}")
        return

    from rich import box as rbox
    from rich.table import Table

    table = Table(box=rbox.SIMPLE, show_header=True, header_style="bold white")
    table.add_column("Date", style="dim", width=12)
    table.add_column("Type", style="dim", width=10)
    table.add_column("Section", style="cyan", width=24)
    table.add_column("Content", style="white")

    for r in results:
        if r["type"] == "article":
            table.add_row(
                r["date"],
                "article",
                r.get("section", ""),
                f"{r['title'][:70]}\n[dim]{r.get('url', '')}[/dim]",
            )
        else:
            table.add_row(r["date"], "briefing", "", r.get("excerpt", "")[:80])

    console.print(table)


if __name__ == "__main__":
    cli()
