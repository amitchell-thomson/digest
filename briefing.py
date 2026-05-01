#!/usr/bin/env python3
"""
digest — Daily executive news briefing for the terminal.

Usage:
    digest                          # Show most recent briefing (instant)
    digest --date 2026-04-28        # Show a specific date
    digest --list                   # List all stored briefing dates
    digest --query "fed rate"       # Search past briefings
    digest --generate               # Fetch + analyse + store (for cron)
    digest --generate --dry-run     # Fetch only, skip Claude call

Typical cron setup:
    0 7 * * 1-5 cd /path/to/digest && uv run digest --generate
"""

import os
import sys
from datetime import datetime, timedelta
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
    get_recent_article_keys,
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


@click.command()
@click.option("--generate", is_flag=True, help="Fetch news, call Claude, store result. For cron.")
@click.option("--dry-run", is_flag=True, help="With --generate: fetch only, skip Claude call.")
@click.option("--no-market", is_flag=True, help="With --generate: skip live market data.")
@click.option("--no-log", is_flag=True, help="With --generate: don't save to disk.")
@click.option("--query", "-q", default=None, metavar="TEXT", help="Search past briefings.")
@click.option("--date", "-d", default=None, metavar="YYYY-MM-DD", help="Show briefing for a specific date.")
@click.option("--list", "list_dates", is_flag=True, help="List all stored briefing dates.")
@click.option("--config", "-c", default=str(Path(__file__).parent / "config.yaml"), hidden=True)
@click.option("--limit", "-n", default=10, show_default=True, help="Max results for --query.")
def cli(
    generate: bool,
    dry_run: bool,
    no_market: bool,
    no_log: bool,
    query: str | None,
    date: str | None,
    list_dates: bool,
    config: str,
    limit: int,
):
    """Daily executive news briefing."""

    cfg = load_config(Path(config))

    # ── GENERATE ──────────────────────────────────────────────────
    if generate:
        _run_generate(cfg, no_market=no_market, no_log=no_log, dry_run=dry_run)
        return

    log_dir = get_log_dir(cfg)
    conn = get_db(log_dir)

    # ── QUERY ─────────────────────────────────────────────────────
    if query:
        _run_query(log_dir, query, limit)
        return

    # ── LIST ──────────────────────────────────────────────────────
    if list_dates:
        dates = list_briefing_dates(conn)
        if not dates:
            console.print("[yellow]No briefings stored yet. Run: digest --generate[/]")
            return
        console.print()
        for d in dates:
            console.print(f"  [dim]{d}[/]  →  digest --date {d}")
        console.print()
        return

    # ── SHOW ──────────────────────────────────────────────────────
    if date:
        date_str = date
        row = load_briefing(conn, date_str)
        if not row:
            dates = list_briefing_dates(conn)
            if dates:
                console.print(f"[yellow]No briefing found for {date_str}.[/]")
                console.print(f"[dim]Most recent: {dates[0]}  →  digest --date {dates[0]}[/]")
            else:
                console.print("[yellow]No briefings stored yet. Run: digest --generate[/]")
        else:
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
        return

    # ── DEFAULT: most recent briefing ─────────────────────────────
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    row = load_briefing(conn, today)
    display_date = today

    if not row:
        row = load_briefing(conn, yesterday)
        display_date = yesterday

    if not row:
        dates = list_briefing_dates(conn)
        if dates:
            row = load_briefing(conn, dates[0])
            display_date = dates[0]

    if not row:
        console.print("[yellow]No briefings stored yet. Run: digest --generate[/]")
        return

    _display_briefing(
        briefing_text=row["briefing"],
        market_data=row["market_data"],
        model=row["model"],
        article_count=row["article_count"],
        log_path=None,
        cost=0.0,
        stored_at=row["run_time"],
        date_str=display_date,
    )


# ─────────────────────────────────────────────
# GENERATE IMPLEMENTATION
# ─────────────────────────────────────────────


def _run_generate(cfg: dict, no_market: bool, no_log: bool, dry_run: bool) -> None:
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

    market_data = []
    if not no_market and tickers:
        with console.status("[dim]Fetching market data…[/]"):
            market_data = fetch_market_snapshot(tickers)

    conn = None
    if not no_log:
        conn = get_db(log_dir)
        recent_keys = get_recent_article_keys(conn, today=date_str)
        sections = {
            section: [
                a
                for a in arts
                if " ".join(a["title"].lower().split()[:5]) not in recent_keys
            ]
            for section, arts in sections.items()
        }
        total_articles = sum(len(v) for v in sections.values())

    all_titles = [a["title"] for arts in sections.values() for a in arts]
    urgent_titles = [t for t in all_titles if is_urgent(t, urgent_kws)]

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
                market_data=market_data,
            )
        except Exception as e:
            err_console.print(f"[red]Claude API error:[/] {e}")
            sys.exit(1)

    log_path = None
    if not no_log and conn:
        save_briefing(conn, date_str, briefing_text, sections, urgent_titles, model, market_data)
        log_path = save_markdown(log_dir, date_str, briefing_text, market_data)

    console.print(
        f"[green]✓[/] Generated briefing for {date_str} "
        f"({total_articles} articles, ~${cost:.3f})"
    )
    if log_path:
        console.print(f"  [dim]saved → {log_path}[/]")


# ─────────────────────────────────────────────
# QUERY IMPLEMENTATION
# ─────────────────────────────────────────────


def _run_query(log_dir: Path, query_text: str, limit: int) -> None:
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


# ─────────────────────────────────────────────
# DISPLAY
# ─────────────────────────────────────────────


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
    alpaca_active = bool(os.getenv("ALPACA_KEY_ID"))

    render_header(model, article_count, alpaca_active, stored_at=stored_at)

    if market_data:
        render_market_ticker(market_data)

    render_briefing(briefing_text)
    render_footer(log_path, cost)


if __name__ == "__main__":
    cli()
