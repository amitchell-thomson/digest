"""
render.py — Bloomberg-style Rich terminal rendering.

Design language:
  - Dark terminal aesthetic with high-contrast accents
  - Market data ticker bar at top
  - Section headers as bold rules with colour coding
  - Markdown-aware briefing body rendering
  - Attention block prominently surfaced
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console(highlight=False, width=120)

# Colour scheme
C = {
    "header":    "bold white",
    "dim":       "dim white",
    "ticker_up": "bold green",
    "ticker_dn": "bold red",
    "ticker_nm": "dim white",
    "macro":     "bold yellow",
    "ai":        "bold cyan",
    "equities":  "bold blue",
    "geo":       "bold magenta",
    "quant":     "bold green",
    "attention": "bold red",
    "watchlist": "bold yellow",
    "neutral":   "white",
}

SECTION_COLOURS = {
    "MACRO & CENTRAL BANKS":   C["macro"],
    "AI & TECHNOLOGY":         C["ai"],
    "EQUITIES & MARKETS":      C["equities"],
    "GEOPOLITICS & MACRO RISK": C["geo"],
    "QUANT & FINTECH":         C["quant"],
}


# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────

def render_header(model: str, article_count: int, alpaca_active: bool) -> None:
    now     = datetime.now(timezone.utc).strftime("%A %d %B %Y  ·  %H:%M UTC")
    alpaca  = "[green]■ Alpaca[/]" if alpaca_active else "[dim]○ Alpaca[/]"
    sources = f"[dim]{article_count} articles[/]  {alpaca}  [dim]Claude/{model}[/]"

    console.print()
    console.print(
        Panel(
            f"[bold white]  ◈  DAILY BRIEFING  ◈[/]\n[dim]{now}[/]\n{sources}",
            border_style="white",
            box=box.DOUBLE,
            padding=(0, 4),
            expand=True,
        )
    )
    console.print()


# ─────────────────────────────────────────────
# MARKET TICKER BAR
# ─────────────────────────────────────────────

def render_market_ticker(market_data: list[dict]) -> None:
    if not market_data:
        return

    table = Table(
        box=box.SIMPLE,
        show_header=False,
        padding=(0, 2),
        expand=True,
    )

    # Split into two rows if many tickers
    mid = (len(market_data) + 1) // 2
    rows = [market_data[:mid], market_data[mid:]]

    for row in rows:
        cells = []
        for item in row:
            sign   = "+" if item["change_pct"] >= 0 else ""
            colour = item["colour"]
            cell   = Text()
            cell.append(f"{item['name']} ", style="dim white")
            cell.append(f"{item['price']} ", style=f"bold {colour}")
            cell.append(f"{item['direction']}{sign}{item['change_pct']:.2f}%", style=colour)
            cells.append(cell)

        if cells:
            table.add_row(*[c for c in cells])

    console.print(
        Panel(
            table,
            title="[dim]MARKETS[/]",
            title_align="left",
            border_style="dim",
            box=box.SIMPLE_HEAD,
            padding=(0, 1),
        )
    )
    console.print()


# ─────────────────────────────────────────────
# BRIEFING BODY
# ─────────────────────────────────────────────

def render_briefing(briefing_text: str) -> None:
    """
    Render the Claude briefing. Parse section headers to apply
    colour coding; render body as Markdown for bullet formatting.
    """
    if not briefing_text:
        console.print("[red]No briefing generated.[/]")
        return

    sections = _split_sections(briefing_text)

    for header, body in sections:
        _render_section(header, body)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown ## sections into (header, body) pairs."""
    import re
    parts   = re.split(r"^(##\s+.+)$", text, flags=re.MULTILINE)
    results = []

    if parts[0].strip():
        results.append(("", parts[0].strip()))

    for i in range(1, len(parts), 2):
        header = parts[i].lstrip("# ").strip()
        body   = parts[i + 1].strip() if i + 1 < len(parts) else ""
        results.append((header, body))

    return results


def _render_section(header: str, body: str) -> None:
    if not header:
        # Preamble text before first section
        if body:
            console.print(Markdown(body))
        return

    # Determine colour from section name match
    colour = C["neutral"]
    for key, c in SECTION_COLOURS.items():
        if any(word in header.upper() for word in key.upper().split()):
            colour = c
            break

    # Special sections
    if "ACTION" in header.upper() or "ATTENTION" in header.upper():
        colour = C["attention"]
        console.print(Rule(f"[{colour}]  ⚠  {header.upper()}  ⚠  [/]", style=colour))
    elif "WATCH" in header.upper():
        colour = C["watchlist"]
        console.print(Rule(f"[{colour}]  {header}  [/]", style=colour))
    elif "STORY" in header.upper():
        colour = C["header"]
        console.print(Rule(f"[{colour}]  {header}  [/]", style=colour))
    else:
        console.print(Rule(f"[{colour}]  {header}  [/]", style=colour))

    console.print()

    if body:
        # Render as markdown for bullet points, bold, etc.
        console.print(Markdown(body, justify="left"))

    console.print()


# ─────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────

def render_footer(log_path: Optional[Path], cost_estimate: float) -> None:
    parts = []
    if log_path:
        parts.append(f"[dim]log → {log_path}[/]")
    if cost_estimate > 0:
        parts.append(f"[dim]~${cost_estimate:.3f} API cost[/]")

    line = "  ·  ".join(parts) if parts else ""
    console.print(Rule(line, style="dim"))
    console.print()

