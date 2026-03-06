"""
AI News Curator — Daily AI news digest delivered to your inbox.
Fetches from RSS feeds, summarizes via Groq (free), emails via Brevo (free).
Designed to run as a Railway cron job.
"""

import os
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (all from environment variables)
# ---------------------------------------------------------------------------

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
EMAIL_TO = os.environ.get("EMAIL_TO", "you@example.com")
SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "noreply@example.com")
SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "AI News")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
HOURS_LOOKBACK = int(os.environ.get("HOURS_LOOKBACK", "26"))
LANGUAGE = os.environ.get("LANGUAGE", "zh-HK")
SOURCES_FILE = os.environ.get("SOURCES_FILE", "sources.json")

# ---------------------------------------------------------------------------
# Language presets
# ---------------------------------------------------------------------------

LANGUAGE_PRESETS = {
    "zh-HK": {
        "name": "香港書面中文",
        "prompt": (
            "用**香港書面中文**撰寫（繁體中文，不用簡體、不用大陸用語、不用台灣語氣）。"
            "英文專有名詞保留原文。"
        ),
        "subject_template": "AI 日報 — {date}（{count} 則新聞）",
        "empty_message": "今日暫無重大 AI 新聞。",
    },
    "zh-TW": {
        "name": "繁體中文（台灣）",
        "prompt": "用**繁體中文（台灣用語）**撰寫。英文專有名詞保留原文。",
        "subject_template": "AI 日報 — {date}（{count} 則新聞）",
        "empty_message": "今日暫無重大 AI 新聞。",
    },
    "zh-CN": {
        "name": "简体中文",
        "prompt": "用**简体中文**撰写。英文专有名词保留原文。",
        "subject_template": "AI 日报 — {date}（{count} 条新闻）",
        "empty_message": "今日暂无重大 AI 新闻。",
    },
    "en": {
        "name": "English",
        "prompt": "Write in **English**.",
        "subject_template": "AI Daily — {date} ({count} articles)",
        "empty_message": "No major AI news today.",
    },
    "ja": {
        "name": "日本語",
        "prompt": "**日本語**で書いてください。英語の専門用語はそのまま使ってください。",
        "subject_template": "AI日報 — {date}（{count}件）",
        "empty_message": "本日の主要AIニュースはありません。",
    },
}


def get_lang_config() -> dict:
    """Get language config from preset or fallback to custom prompt."""
    if LANGUAGE in LANGUAGE_PRESETS:
        return LANGUAGE_PRESETS[LANGUAGE]
    # Treat LANGUAGE value as a custom language instruction
    return {
        "name": LANGUAGE,
        "prompt": f"Write in {LANGUAGE}.",
        "subject_template": "AI Daily — {date} ({count} articles)",
        "empty_message": "No major AI news today.",
    }


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def load_sources() -> dict[str, str]:
    """Load RSS sources from JSON file."""
    sources_path = Path(__file__).parent / SOURCES_FILE
    if not sources_path.exists():
        log.error(f"Sources file not found: {sources_path}")
        return {}

    with open(sources_path) as f:
        sources = json.load(f)

    return {s["name"]: s["url"] for s in sources}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def fetch_recent_entries(hours: int = HOURS_LOOKBACK) -> list[dict]:
    """Fetch RSS entries published within the last N hours."""
    feeds = load_sources()
    if not feeds:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    entries = []

    for source, url in feeds.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                published = None
                for date_field in ("published_parsed", "updated_parsed"):
                    parsed = getattr(entry, date_field, None)
                    if parsed:
                        published = datetime(*parsed[:6], tzinfo=timezone.utc)
                        break
                if not published:
                    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
                    if raw:
                        try:
                            published = parsedate_to_datetime(raw)
                        except Exception:
                            continue

                if published and published >= cutoff:
                    entries.append({
                        "source": source,
                        "title": entry.get("title", ""),
                        "url": entry.get("link", ""),
                        "summary": entry.get("summary", "")[:500],
                        "published": published.isoformat(),
                    })
            log.info(f"{source}: fetched {len(feed.entries)} entries")
        except Exception as e:
            log.warning(f"{source}: failed to fetch — {e}")

    entries.sort(key=lambda x: x["published"], reverse=True)
    log.info(f"Total recent entries: {len(entries)}")
    return entries


def summarize_with_groq(entries: list[dict]) -> str:
    """Send entries to Groq for summarization."""
    lang = get_lang_config()

    if not entries:
        return lang["empty_message"]

    articles_text = ""
    for i, e in enumerate(entries, 1):
        articles_text += (
            f"\n{i}. [{e['source']}] {e['title']}"
            f"\n   URL: {e['url']}"
            f"\n   Summary: {e['summary'][:200]}\n"
        )

    prompt = f"""You are an AI news editor. Compile the following articles into a concise daily digest.

Language: {lang['prompt']}

Requirements:
1. Start with a "Key Highlights" section — 3 bullet points summarizing the most important developments
2. Then organize remaining news by importance (Major Releases > Product Updates > Industry Analysis > Tutorials)
3. Summarize each item in 1-2 sentences with the source URL
4. Merge related articles into a single entry
5. Mark YouTube videos with a [Video] tag

Today's articles ({len(entries)} total):
{articles_text}

Output the digest directly, no preamble."""

    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4000,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str):
    """Send newsletter via Brevo transactional email API."""
    resp = httpx.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
            "to": [{"email": EMAIL_TO}],
            "subject": subject,
            "htmlContent": html_body,
        },
        timeout=30,
    )
    resp.raise_for_status()
    log.info(f"Email sent: {resp.json()}")


def markdown_to_html(md: str) -> str:
    """Convert markdown to simple HTML for email rendering."""
    lines = md.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<br>")
            continue

        if stripped.startswith("### "):
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{stripped[2:]}</h1>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{stripped[2:]}</li>")
        else:
            html_lines.append(f"<p>{stripped}</p>")

    if in_list:
        html_lines.append("</ul>")

    html = "\n".join(html_lines)
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)

    return f"""<div style="font-family: -apple-system, sans-serif; max-width: 640px;
                margin: 0 auto; padding: 20px; color: #333; line-height: 1.6;">
    {html}
    <hr style="margin-top: 32px; border: none; border-top: 1px solid #ddd;">
    <p style="color: #999; font-size: 12px;">
        Auto-generated by AI News Curator · {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC
    </p>
    </div>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    lang = get_lang_config()
    today = datetime.now(timezone(timedelta(hours=8)))  # HKT
    date_str = today.strftime("%Y-%m-%d")
    log.info(f"=== AI News Curator — {date_str} ({lang['name']}) ===")

    entries = fetch_recent_entries()

    if not entries:
        log.info("No recent entries found. Skipping.")
        return

    newsletter = summarize_with_groq(entries)
    log.info(f"Newsletter generated ({len(newsletter)} chars)")

    subject = lang["subject_template"].format(date=date_str, count=len(entries))
    html = markdown_to_html(newsletter)
    send_email(subject, html)

    log.info("Done.")


if __name__ == "__main__":
    main()
