"""
tui.py — Interactive Textual TUI for browsing digest briefings.
"""

import re
from datetime import datetime, timezone

from rich.align import Align
from rich.console import Group
from rich.padding import Padding
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich import box
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer
from textual.theme import Theme
from textual.widgets import Label, ListItem, ListView, Static

from render import ASCII_TITLE, SECTION_COLOURS, _body_renderable, _split_sections

_TRANSPARENT_THEME = Theme(
    name="digest",
    primary="#ffffff",
    dark=True,
    variables={
        "background": "ansi_default",
        "surface": "ansi_default",
        "panel": "ansi_default",
        "block-cursor-blurred-background": "ansi_default",
        "block-hover-background": "ansi_default",
    },
)


# ─────────────────────────────────────────────
# RENDERING HELPERS
# ─────────────────────────────────────────────

_SENTINEL_OVERVIEW = "__overview__"


def _label_markup(header: str) -> str:
    if "ACTION" in header.upper() or "ATTENTION" in header.upper():
        return f"[bold red]⚠  {header}[/]"
    if "WATCH" in header.upper():
        return f"[bold yellow]●  {header}[/]"
    for key, colour in SECTION_COLOURS.items():
        if any(word in header.upper() for word in key.upper().split()):
            return f"[{colour}]{header}[/]"
    return header


def _section_renderable(header: str, body: str) -> Group:
    colour = "white"
    rule_title = f" {header} "

    if "ACTION" in header.upper() or "ATTENTION" in header.upper():
        colour = "bold red"
        rule_title = f" ⚠  {header.upper()}  ⚠ "
    elif "WATCH" in header.upper():
        colour = "bold yellow"
    else:
        for key, c in SECTION_COLOURS.items():
            if any(word in header.upper() for word in key.upper().split()):
                colour = c
                break

    cleaned = re.sub(r"\n\s*---+\s*$", "", body.strip())
    body_content = _body_renderable(cleaned) if cleaned else Text("")

    return Group(
        Rule(rule_title, style=colour),
        Padding(body_content, (1, 0, 0, 2)),
    )


def _overview_renderable(market_data: list[dict], story_body: str) -> Group:
    parts: list = []

    if market_data:
        cols = 3
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        for _ in range(cols):
            table.add_column()
        for i in range(0, len(market_data), cols):
            row = market_data[i : i + cols]
            cells = []
            for item in row:
                sign = "+" if item["change_pct"] >= 0 else ""
                colour = item["colour"]
                cell = Text()
                cell.append(f"{item['name']} ", style="dim white")
                cell.append(f"{item['price']} ", style=f"bold {colour}")
                cell.append(f"{item['direction']}{sign}{item['change_pct']:.2f}%", style=colour)
                cells.append(cell)
            while len(cells) < cols:
                cells.append(Text(""))
            table.add_row(*cells)
        parts += [Rule(" MARKETS ", style="dim white"), Padding(table, (1, 0, 0, 0))]

    if story_body:
        cleaned = re.sub(r"\n\s*---+\s*$", "", story_body.strip())
        spacer = Text("")
        parts += [
            spacer,
            Rule(" THE STORY TODAY ", style="bold white"),
            Padding(_body_renderable(cleaned), (1, 0, 0, 2)),
        ]

    return Group(*parts)


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────

class DigestTUI(App):
    CSS_PATH = None
    CSS = """
    Screen, Horizontal, Vertical, ScrollableContainer,
    ListView, ListItem, Static, Label {
        background: ansi_default;
    }

    #header {
        height: auto;
        border: heavy white;
        margin: 1 1 0 1;
        padding: 1 4;
    }

    #main {
        height: 1fr;
        margin: 1 1 0 1;
    }

    #nav {
        width: 28;
        border: round white;
        margin: 0 1 0 0;
        padding: 0;
    }

    #content {
        border: round white;
        padding: 1 2;
    }

    #footer {
        height: 1;
        margin: 0 1 1 1;
        content-align: center middle;
        color: $text-muted;
    }

    ListItem {
        padding: 0 1;
    }

    ListItem:hover {
        background: $primary 8%;
    }

    ListItem.--highlight {
        background: $primary 18%;
    }
    """
    TITLE = "Digest"

    BINDINGS = [
        Binding("j", "next", "Next", show=False),
        Binding("k", "prev", "Prev", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        briefing_text: str,
        market_data: list,
        model: str,
        article_count: int,
        stored_at: str | None = None,
    ) -> None:
        super().__init__()
        self._sections = [(h, b) for h, b in _split_sections(briefing_text) if h]
        self._market_data = market_data
        self._model = model
        self._article_count = article_count
        self._stored_at = stored_at

    def _story_body(self) -> str:
        for header, body in self._sections:
            if "STORY" in header.upper():
                return body
        return ""

    @property
    def _items(self) -> list[tuple[str, str]]:
        """Unified ordered list of (header, body) items for the nav.
        Overview is always first; Story Today is merged into it."""
        items: list[tuple[str, str]] = [(_SENTINEL_OVERVIEW, "")]
        items.extend((h, b) for h, b in self._sections if "STORY" not in h.upper())
        return items

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="main"):
            yield ListView(id="nav")
            with ScrollableContainer(id="content"):
                yield Static("", id="body")
        yield Static(id="footer")

    def on_mount(self) -> None:
        self.register_theme(_TRANSPARENT_THEME)
        self.theme = "digest"

        now = datetime.now(timezone.utc).strftime("%A %d %B %Y  ·  %H:%M UTC")
        meta_parts = [now, f"{self._article_count} articles", f"Claude/{self._model}"]
        if self._stored_at:
            gen_time = self._stored_at[:16].replace("T", " ")
            meta_parts.insert(1, f"Generated {gen_time} UTC")

        self.query_one("#header", Static).update(
            Group(
                Align.center(Text(ASCII_TITLE, style="bold white")),
                Align.center(Text("  ·  ".join(meta_parts), style="dim white")),
            )
        )

        nav = self.query_one("#nav", ListView)
        for header, _ in self._items:
            if header == _SENTINEL_OVERVIEW:
                nav.append(ListItem(Label("  [bold white]Overview[/]")))
            else:
                nav.append(ListItem(Label(f"  {_label_markup(header)}")))

        self.query_one("#footer", Static).update(
            "[dim]↑ ↓  /  j k   navigate      q   quit[/]"
        )

        self._show(0)

    def _show(self, idx: int) -> None:
        items = self._items
        if not items or idx >= len(items):
            return

        header, body = items[idx]
        if header == _SENTINEL_OVERVIEW:
            content = _overview_renderable(self._market_data, self._story_body())
        else:
            content = _section_renderable(header, body)

        self.query_one("#body", Static).update(content)
        self.query_one("#content", ScrollableContainer).scroll_home(animate=False)

    @on(ListView.Highlighted)
    def on_nav_highlighted(self, event: ListView.Highlighted) -> None:
        nav = self.query_one("#nav", ListView)
        if nav.index is not None:
            self._show(nav.index)

    def action_next(self) -> None:
        self.query_one("#nav", ListView).action_cursor_down()

    def action_prev(self) -> None:
        self.query_one("#nav", ListView).action_cursor_up()


def launch_tui(
    briefing_text: str,
    market_data: list,
    model: str,
    article_count: int,
    stored_at: str | None = None,
) -> None:
    DigestTUI(briefing_text, market_data, model, article_count, stored_at).run()
