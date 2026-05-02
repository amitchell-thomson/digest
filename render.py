"""
render.py — Bloomberg-style Rich terminal rendering.

Design language:
  - Dark terminal aesthetic with high-contrast accents
  - Market data ticker bar at top
  - Section headers as bold rules with colour coding
  - Markdown-aware briefing body rendering
  - Attention block prominently surfaced
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich import box
from rich.align import Align
from rich.console import Console
from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console(highlight=False, width=120)

# Colour scheme
C = {
    "header": "bold white",
    "dim": "dim white",
    "ticker_up": "bold green",
    "ticker_dn": "bold red",
    "ticker_nm": "dim white",
    "macro": "bold yellow",
    "ai": "bold cyan",
    "equities": "bold blue",
    "geo": "bold magenta",
    "quant": "bold green",
    "attention": "bold red",
    "watchlist": "bold yellow",
    "neutral": "white",
}

SECTION_COLOURS = {
    "WORLD NEWS": C["geo"],
    "POLITICS": C["macro"],
    "ECONOMICS & POLICY": C["macro"],
    "SCIENCE & TECHNOLOGY": C["ai"],
    "BUSINESS & MARKETS": C["equities"],
    "QUANT RESEARCH": C["quant"],
}


# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────


ASCII_TITLE = """\
██████╗ ██╗ ██████╗ ███████╗███████╗████████╗
██╔══██╗██║██╔════╝ ██╔════╝██╔════╝╚══██╔══╝
██║  ██║██║██║  ███╗█████╗  ███████╗   ██║
██║  ██║██║██║   ██║██╔══╝  ╚════██║   ██║
██████╔╝██║╚██████╔╝███████╗███████║   ██║
╚═════╝ ╚═╝ ╚═════╝ ╚══════╝╚══════╝   ╚═╝   """


def render_header(
    model: str,
    article_count: int,
    alpaca_active: bool,
    stored_at: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).strftime("%A %d %B %Y  ·  %H:%M UTC")

    title_text = Align.center(Text(ASCII_TITLE, style="bold white"))

    meta_parts = [now, f"{article_count} articles", f"Claude/{model}"]
    if stored_at:
        gen_time = stored_at[:16].replace("T", " ")
        meta_parts.insert(1, f"Generated {gen_time} UTC")
    meta_line = Align.center(Text("  ·  ".join(meta_parts), style="dim white"))

    console.print()
    console.print(
        Panel(
            Group(title_text, meta_line),
            border_style="white",
            box=box.HEAVY,
            padding=(1, 4),
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

    cols = 4
    rows = [market_data[i : i + cols] for i in range(0, len(market_data), cols)]

    for row in rows:
        cells = []
        for item in row:
            sign = "+" if item["change_pct"] >= 0 else ""
            colour = item["colour"]
            cell = Text()
            cell.append(f"{item['name']} ", style="dim white")
            cell.append(f"{item['price']} ", style=f"bold {colour}")
            cell.append(
                f"{item['direction']}{sign}{item['change_pct']:.2f}%", style=colour
            )
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
    parts = re.split(r"^(##\s+.+)$", text, flags=re.MULTILINE)
    results = []

    if parts[0].strip():
        results.append(("", parts[0].strip()))

    for i in range(1, len(parts), 2):
        header = parts[i].lstrip("# ").strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        results.append((header, body))

    return results


def _body_renderable(text: str) -> Group:
    """Render body text, adding a blank line between consecutive bullet items."""
    bullet_re = re.compile(r"^\s*[-*]\s")
    lines = text.splitlines()
    renderables = []
    prose_buf: list[str] = []

    def flush_prose() -> None:
        if prose_buf:
            renderables.append(Markdown("\n".join(prose_buf), justify="left"))
            prose_buf.clear()

    for line in lines:
        if bullet_re.match(line):
            flush_prose()
            renderables.append(Padding(Markdown(line, justify="left"), (0, 0, 1, 0)))
        else:
            prose_buf.append(line)

    flush_prose()
    return Group(*renderables)


def _render_section(header: str, body: str) -> None:
    if not header:
        if body:
            console.print(Markdown(body))
        return

    colour = C["neutral"]
    for key, c in SECTION_COLOURS.items():
        if any(word in header.upper() for word in key.upper().split()):
            colour = c
            break

    cleaned = re.sub(r"\n\s*---+\s*$", "", body.strip())
    content = _body_renderable(cleaned) if cleaned else Text("")

    if "ACTION" in header.upper() or "ATTENTION" in header.upper():
        colour = C["attention"]
        console.print(
            Panel(
                content,
                title=f"[{colour}]  ⚠  {header.upper()}  ⚠  [/]",
                title_align="left",
                border_style="red",
                box=box.DOUBLE,
                padding=(1, 2),
                expand=True,
            )
        )
    elif "WATCH" in header.upper():
        colour = C["watchlist"]
        console.print(
            Panel(
                content,
                title=f"[{colour}]  {header}  [/]",
                title_align="left",
                border_style="yellow",
                box=box.ROUNDED,
                padding=(1, 2),
                expand=True,
            )
        )
    elif "STORY" in header.upper():
        console.print(
            Panel(
                content,
                title=f"[{C['header']}]  {header}  [/]",
                title_align="left",
                border_style="white",
                box=box.ROUNDED,
                padding=(1, 2),
                expand=True,
            )
        )
    else:
        border_style = colour.replace("bold ", "")
        console.print(
            Panel(
                content,
                title=f"[{colour}]  {header}  [/]",
                title_align="left",
                border_style=border_style,
                box=box.ROUNDED,
                padding=(1, 2),
                expand=True,
            )
        )

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
