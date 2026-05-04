"""
analyse.py — Single Claude API call across all sections.

Sends all articles as one context block, gets back a structured
analyst briefing. One call per run — not per article.
"""

import os

import anthropic
from anthropic.types import TextBlock


def build_article_context(
    sections: dict[str, list[dict]],
    run_date: str | None = None,
    market_data: list[dict] | None = None,
) -> str:
    """
    Serialise all fetched articles into a structured context block
    for Claude. Format is deliberately readable — Claude performs
    better with clean structured input than JSON blobs.
    """
    lines = ["# TODAY'S SOURCE MATERIAL\n"]
    if run_date:
        lines.append(f"**Briefing date: {run_date}**\n")

    if market_data:
        lines.append("## MARKET SNAPSHOT\n")
        for item in market_data:
            sign = "+" if item["change_pct"] >= 0 else ""
            lines.append(
                f"- {item['name']}: {item['price']} "
                f"({item['direction']}{sign}{item['change_pct']:.2f}%)"
            )
        lines.append("")

    total = 0

    for section, articles in sections.items():
        if not articles:
            continue
        lines.append(f"## {section}")
        for i, a in enumerate(articles, 1):
            lines.append(f"\n**[{i}] {a['title']}**")
            if a.get("published"):
                lines.append(f"*{a['published']}*")
            if a.get("description"):
                lines.append(a["description"])
            if a.get("url"):
                lines.append(f"Source: {a['url']}")
            total += 1
        lines.append("")

    lines.append(f"\n---\n*{total} articles across {len(sections)} sections*")
    return "\n".join(lines)


def run_analysis(
    sections: dict[str, list[dict]],
    prompt: str,
    model: str,
    max_tokens: int,
    run_date: str | None = None,
    market_data: list[dict] | None = None,
) -> str:
    """
    Send all articles to Claude in a single call.
    Returns the full briefing text, or None on failure.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in environment / .env")

    client = anthropic.Anthropic(api_key=api_key)
    context = build_article_context(sections, run_date, market_data)

    user_message = f"{context}\n\n---\n\n{prompt}"

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=(
                "You are a senior macro analyst. You write precise, blunt, "
                "high-signal briefings for sophisticated readers. You never pad, "
                "never hedge unnecessarily, and never invent data."
            ),
            messages=[{"role": "user", "content": user_message}],
        )
        block = resp.content[0]
        if not isinstance(block, TextBlock):
            raise RuntimeError(f"Unexpected response block type: {type(block)}")
        return block.text.strip()
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude API error: {e}") from e


def is_urgent(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in keywords)
