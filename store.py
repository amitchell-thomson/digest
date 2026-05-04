"""
store.py — Persistent storage.

Outputs:
  1. SQLite database  — fully relational, briefings + articles, searchable
  2. Markdown file    — human-readable daily log
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


# ─────────────────────────────────────────────
# SQLITE
# ─────────────────────────────────────────────


def get_db(log_dir: Path) -> sqlite3.Connection:
    db_path = log_dir / "briefings.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS briefings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT NOT NULL,
            run_time      TEXT NOT NULL,
            briefing      TEXT NOT NULL,
            model         TEXT,
            article_count INTEGER,
            market_data   TEXT
        );

        CREATE TABLE IF NOT EXISTS articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            briefing_id INTEGER REFERENCES briefings(id),
            section     TEXT,
            title       TEXT,
            description TEXT,
            url         TEXT,
            published   TEXT,
            source      TEXT,
            urgent      INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_briefings_date ON briefings(date);
        CREATE INDEX IF NOT EXISTS idx_articles_section ON articles(section);
        CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(title);
    """)
    # Migrate existing DBs silently
    try:
        conn.execute("ALTER TABLE briefings ADD COLUMN market_data TEXT")
    except Exception:
        pass
    conn.commit()


def save_briefing(
    conn: sqlite3.Connection,
    date_str: str,
    briefing_text: str,
    sections: dict,
    urgent_titles: list[str],
    model: str,
    market_data: list[dict] | None = None,
) -> int:
    """Save briefing + articles to SQLite. Returns briefing row ID."""
    article_count = sum(len(v) for v in sections.values())
    run_time = datetime.utcnow().isoformat()
    market_data_json = json.dumps(market_data or [])

    cur = conn.execute(
        """INSERT INTO briefings
           (date, run_time, briefing, model, article_count, market_data)
           VALUES (?,?,?,?,?,?)""",
        (date_str, run_time, briefing_text, model, article_count, market_data_json),
    )
    assert cur.lastrowid is not None
    briefing_id = cur.lastrowid

    for section, articles in sections.items():
        for a in articles:
            conn.execute(
                """INSERT INTO articles
                   (briefing_id, section, title, description, url, published, source, urgent)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    briefing_id,
                    section,
                    a.get("title", ""),
                    a.get("description", ""),
                    a.get("url", ""),
                    a.get("published", ""),
                    a.get("source", ""),
                    1 if a.get("title", "") in urgent_titles else 0,
                ),
            )
    conn.commit()
    return briefing_id


def load_briefing(conn: sqlite3.Connection, date_str: str) -> dict | None:
    """
    Load a stored briefing for a given date.
    Returns dict with briefing text, market_data, model, run_time.
    Returns None if no briefing exists for that date.
    """
    row = conn.execute(
        """SELECT briefing, market_data, model, run_time, article_count
           FROM briefings WHERE date = ?
           ORDER BY id DESC LIMIT 1""",
        (date_str,),
    ).fetchone()

    if not row:
        return None

    return {
        "briefing": row["briefing"],
        "market_data": json.loads(row["market_data"] or "[]"),
        "model": row["model"] or "unknown",
        "run_time": row["run_time"],
        "article_count": row["article_count"] or 0,
    }


def list_briefing_dates(conn: sqlite3.Connection) -> list[str]:
    """Return all dates that have stored briefings, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT date FROM briefings ORDER BY date DESC"
    ).fetchall()
    return [r["date"] for r in rows]


def get_recent_article_keys(
    conn: sqlite3.Connection, today: str, days: int = 1
) -> set[str]:
    """
    Return title fingerprints (first 5 words, lowercase) of articles that
    appeared in any briefing in the `days` preceding today. Excludes today's
    own runs so same-day re-generation sees the full article set.
    """
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )
    rows = conn.execute(
        """SELECT a.title FROM articles a
           JOIN briefings b ON a.briefing_id = b.id
           WHERE b.date >= ? AND b.date < ?""",
        (cutoff, today),
    ).fetchall()
    return {" ".join((r["title"] or "").lower().split()[:5]) for r in rows}


# ─────────────────────────────────────────────
# MARKDOWN LOG
# ─────────────────────────────────────────────


def save_markdown(
    log_dir: Path,
    date_str: str,
    briefing_text: str,
    market_data: list[dict],
) -> Path:
    path = log_dir / f"{date_str}.md"
    lines = [f"# Daily Briefing — {date_str}\n"]

    if market_data:
        lines.append("## Market Snapshot\n")
        for item in market_data:
            sign = "+" if item["change_pct"] >= 0 else ""
            lines.append(
                f"- **{item['name']}**: {item['price']} "
                f"({item['direction']}{sign}{item['change_pct']:.2f}%)"
            )
        lines.append("")

    lines.append(briefing_text)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ─────────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────────


def search_briefings(log_dir: Path, query: str, limit: int = 10) -> list[dict]:
    """Full-text search across stored briefing text and article titles."""
    conn = get_db(log_dir)
    pattern = f"%{query}%"

    article_rows = conn.execute(
        """
        SELECT b.date, a.section, a.title, a.url
        FROM articles a JOIN briefings b ON a.briefing_id = b.id
        WHERE a.title LIKE ? OR a.description LIKE ?
        ORDER BY b.date DESC LIMIT ?
    """,
        (pattern, pattern, limit),
    ).fetchall()

    briefing_rows = conn.execute(
        """
        SELECT date, briefing FROM briefings
        WHERE briefing LIKE ?
        ORDER BY date DESC LIMIT ?
    """,
        (pattern, limit),
    ).fetchall()

    results = []
    for r in article_rows:
        results.append(
            {
                "type": "article",
                "date": r["date"],
                "section": r["section"],
                "title": r["title"],
                "url": r["url"],
            }
        )
    for r in briefing_rows:
        for line in r["briefing"].splitlines():
            if query.lower() in line.lower():
                results.append(
                    {"type": "briefing", "date": r["date"], "excerpt": line.strip()}
                )
                break

    return results
