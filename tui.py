"""
tui.py — Interactive Textual TUI for browsing digest briefings.
"""

import re
import sqlite3
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
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Label, ListItem, ListView, Static

from render import ASCII_TITLE, SECTION_COLOURS, _body_renderable, _split_sections
from store import load_briefing

_TRANSPARENT_THEME = Theme(
    name="digest",
    primary="#ffffff",
    dark=True,
    ansi=True,
    variables={
        "background": "ansi_default",
        "surface": "ansi_default",
        "panel": "ansi_default",
        "block-cursor-blurred-background": "ansi_default",
        "block-hover-background": "ansi_default",
        "ansi-background": "ansi_default",
        "ansi-foreground": "ansi_default",
    },
)


# ─────────────────────────────────────────────
# RENDERING HELPERS
# ─────────────────────────────────────────────

_SENTINEL_OVERVIEW = "__overview__"
_SENTINEL_SEPARATOR = "__separator__"


def _label_markup(header: str) -> str:
    if "ACTION" in header.upper() or "ATTENTION" in header.upper():
        return f"[bold red]⚠  {header}[/]"
    if "WATCH" in header.upper():
        return f"[dim white]●  {header}[/]"
    return f"[white]{header}[/]"


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
                cell.append(
                    f"{item['direction']}{sign}{item['change_pct']:.2f}%", style=colour
                )
                cells.append(cell)
            while len(cells) < cols:
                cells.append(Text(""))
            table.add_row(*cells)
        parts += [Rule(" MARKETS ", style="dim white"), Padding(table, (1, 0, 0, 0))]

    if story_body:
        cleaned = re.sub(r"\n\s*---+\s*$", "", story_body.strip())
        parts += [
            Text(""),
            Rule(" THE STORY TODAY ", style="bold white"),
            Padding(_body_renderable(cleaned), (1, 0, 0, 2)),
        ]

    return Group(*parts)


# ─────────────────────────────────────────────
# DATE PICKER MODAL
# ─────────────────────────────────────────────


class DatePickerScreen(ModalScreen):
    DEFAULT_CSS = """
    DatePickerScreen {
        align: center middle;
        background: ansi_default;
    }

    #date-dialog {
        width: 30;
        height: auto;
        max-height: 22;
        border: round #89b4fa;
        background: ansi_default;
        padding: 1 2;
    }

    #date-dialog-title {
        text-align: center;
        padding-bottom: 1;
    }

    #date-list {
        height: auto;
        max-height: 16;
        background: ansi_default;
    }

    #date-list > ListItem {
        background: ansi_default;
        padding: 0 1;
    }

    #date-list > ListItem:hover {
        background: #89b4fa 20%;
    }

    #date-list > ListItem.--highlight {
        background: #89b4fa;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_modal", "Cancel")]

    def __init__(self, dates: list[str], current: str) -> None:
        super().__init__()
        self._dates = dates
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="date-dialog"):
            yield Static("[bold white]select date[/]", id="date-dialog-title")
            yield ListView(id="date-list")

    def on_mount(self) -> None:
        lv = self.query_one("#date-list", ListView)
        for d in self._dates:
            if d == self._current:
                lv.append(ListItem(Label(f"  [bold white]● {d}[/]")))
            else:
                lv.append(ListItem(Label(f"    [white]{d}[/]")))
        if self._current in self._dates:
            lv.index = self._dates.index(self._current)
        lv.focus()

    @on(ListView.Selected)
    def date_selected(self, event: ListView.Selected) -> None:
        lv = self.query_one("#date-list", ListView)
        if lv.index is not None:
            self.dismiss(self._dates[lv.index])

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────


class DigestTUI(App):
    CSS = """
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
        background: #89b4fa;
    }

    ListItem.separator {
        padding: 0 1;
        height: 1;
    }

    ListItem.separator:hover {
        background: ansi_default;
    }

    ListItem.separator.--highlight {
        background: ansi_default;
    }
    """
    TITLE = "Digest"

    BINDINGS = [
        Binding("j", "next", "Next", show=False),
        Binding("k", "prev", "Prev", show=False),
        Binding("d", "pick_date", "Date"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        briefing_text: str,
        market_data: list,
        model: str,
        article_count: int,
        stored_at: str | None = None,
        conn: sqlite3.Connection | None = None,
        dates: list[str] | None = None,
        date_str: str | None = None,
    ) -> None:
        super().__init__()
        self._sections = [(h, b) for h, b in _split_sections(briefing_text) if h]
        self._market_data = market_data
        self._model = model
        self._article_count = article_count
        self._stored_at = stored_at
        self._conn = conn
        self._dates = dates or []
        self._current_date = date_str or ""

    def _story_body(self) -> str:
        for header, body in self._sections:
            if "STORY" in header.upper():
                return body
        return ""

    @property
    def _items(self) -> list[tuple[str, str]]:
        """Nav items with separator sentinels injected between groups.
        Overview is always first; Story Today is merged into it."""

        def _is_special(h: str) -> bool:
            u = h.upper()
            return "WATCH" in u or "ACTION" in u or "ATTENTION" in u

        regular = [
            (h, b)
            for h, b in self._sections
            if "STORY" not in h.upper() and not _is_special(h)
        ]
        special = [
            (h, b)
            for h, b in self._sections
            if "STORY" not in h.upper() and _is_special(h)
        ]

        items: list[tuple[str, str]] = [(_SENTINEL_OVERVIEW, "")]
        if regular:
            items.append((_SENTINEL_SEPARATOR, ""))
            items.extend(regular)
        if special:
            items.append((_SENTINEL_SEPARATOR, ""))
            items.extend(special)
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
        self._update_header()
        self._rebuild_nav()
        self.query_one("#footer", Static).update(
            "[dim]↑ ↓  j k   navigate   d   date   q   quit[/]"
        )
        self._show(0)

    def _update_header(self) -> None:
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

    def _rebuild_nav(self) -> None:
        nav = self.query_one("#nav", ListView)
        nav.clear()
        for header, _ in self._items:
            if header == _SENTINEL_OVERVIEW:
                nav.append(ListItem(Label("  [bold white]Overview[/]")))
            elif header == _SENTINEL_SEPARATOR:
                sep = ListItem(Static("[dim]  ──────────────────────[/]"))
                sep.add_class("separator")
                nav.append(sep)
            else:
                nav.append(ListItem(Label(f"  {_label_markup(header)}")))

    def _load_date(self, date_str: str) -> None:
        row = load_briefing(self._conn, date_str)
        if not row:
            return
        self._sections = [(h, b) for h, b in _split_sections(row["briefing"]) if h]
        self._market_data = row["market_data"]
        self._model = row["model"]
        self._article_count = row["article_count"]
        self._stored_at = row["run_time"]
        self._current_date = date_str
        self._update_header()
        self._rebuild_nav()
        self._show(0)

    def _show(self, idx: int) -> None:
        items = self._items
        if not items or idx >= len(items):
            return

        header, body = items[idx]
        if header == _SENTINEL_SEPARATOR:
            return
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
        nav = self.query_one("#nav", ListView)
        items = self._items
        nav.action_cursor_down()
        while nav.index is not None and items[nav.index][0] == _SENTINEL_SEPARATOR:
            nav.action_cursor_down()

    def action_prev(self) -> None:
        nav = self.query_one("#nav", ListView)
        items = self._items
        nav.action_cursor_up()
        while nav.index is not None and items[nav.index][0] == _SENTINEL_SEPARATOR:
            nav.action_cursor_up()

    def action_pick_date(self) -> None:
        if not self._conn or not self._dates:
            return

        def on_result(date_str: str | None) -> None:
            if date_str and date_str != self._current_date:
                self._load_date(date_str)

        self.push_screen(DatePickerScreen(self._dates, self._current_date), on_result)


def launch_tui(
    briefing_text: str,
    market_data: list,
    model: str,
    article_count: int,
    stored_at: str | None = None,
    conn: sqlite3.Connection | None = None,
    dates: list[str] | None = None,
    date_str: str | None = None,
) -> None:
    DigestTUI(
        briefing_text,
        market_data,
        model,
        article_count,
        stored_at,
        conn,
        dates,
        date_str,
    ).run()
