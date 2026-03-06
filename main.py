"""
AI News Curator — Daily newsletter for content inspiration.
Fetches AI news from RSS feeds, summarizes in Chinese via Groq, emails via Brevo.
Designed to run as a Railway cron job.
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
EMAIL_TO = os.environ.get("EMAIL_TO", "kristie@giftio.online")
SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "noreply@giftio.online")
SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "Giftio AI News")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
HOURS_LOOKBACK = int(os.environ.get("HOURS_LOOKBACK", "26"))

# ---------------------------------------------------------------------------
# RSS Sources
# ---------------------------------------------------------------------------

FEEDS = {
    # --- Tech Media ---
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "Ars Technica AI": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "MIT Tech Review AI": "https://www.technologyreview.com/feed/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",

    # --- AI Company Blogs ---
    "OpenAI Blog": "https://openai.com/blog/rss.xml",
    "Anthropic News": "https://www.anthropic.com/rss.xml",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "Meta AI Blog": "https://ai.meta.com/blog/rss/",
    "HuggingFace Blog": "https://huggingface.co/blog/feed.xml",

    # --- YouTube Channels (via RSS) ---
    "Matt Wolfe": "https://www.youtube.com/feeds/videos.xml?channel_id=UCJifBHSDVMR0wIJnMYkq2gg",
    "AI Explained": "https://www.youtube.com/feeds/videos.xml?channel_id=UCNJ1Ymd5yFuUPtn21xtRbbw",
    "Two Minute Papers": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg",
    "Fireship": "https://www.youtube.com/feeds/videos.xml?channel_id=UCsBjURrPoezykLs9EqgamOA",
    "TheAIGRID": "https://www.youtube.com/feeds/videos.xml?channel_id=UCJHnlbGcJSQQDoXHRjgB2qg",
}


def fetch_recent_entries(hours: int = HOURS_LOOKBACK) -> list[dict]:
    """Fetch RSS entries published within the last N hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    entries = []

    for source, url in FEEDS.items():
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
    """Send entries to Groq for Chinese summarization."""
    if not entries:
        return "今日暫無重大 AI 新聞。"

    articles_text = ""
    for i, e in enumerate(entries, 1):
        articles_text += f"\n{i}. [{e['source']}] {e['title']}\n   URL: {e['url']}\n   摘要: {e['summary'][:200]}\n"

    prompt = f"""你是一位專注於 AI 領域的新聞編輯。請將以下今日 AI 新聞整理成一份簡潔的中文日報。

要求：
1. 用**香港書面中文**撰寫（繁體，不用簡體、不用大陸用語、不用台灣語氣）
2. 按重要性分類整理（重大發佈 > 產品更新 > 行業分析 > 教學資源）
3. 每則新聞用 1-2 句話概述重點，附上原文連結
4. 如有多則相關新聞，合併整理
5. 最後加一段「今日重點」（3 bullet points 總結最值得關注的動態）
6. YouTube 影片標註為「影片」方便辨識

今日新聞（{len(entries)} 則）：
{articles_text}

請直接輸出整理後的日報內容，不需要額外解釋。"""

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
    """Minimal markdown-to-HTML for email rendering."""
    import re
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

        # Headers
        if stripped.startswith("### "):
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h1>{stripped[2:]}</h1>")
        # List items
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
    # Convert markdown links to HTML
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
    # Bold
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)

    return f"""<div style="font-family: -apple-system, sans-serif; max-width: 640px;
                margin: 0 auto; padding: 20px; color: #333; line-height: 1.6;">
    {html}
    <hr style="margin-top: 32px; border: none; border-top: 1px solid #ddd;">
    <p style="color: #999; font-size: 12px;">
        Auto-generated by AI News Curator · {datetime.now().strftime('%Y-%m-%d %H:%M')} HKT
    </p>
    </div>"""


def main():
    today = datetime.now(timezone(timedelta(hours=8)))  # HKT
    date_str = today.strftime("%Y-%m-%d")
    log.info(f"=== AI News Curator — {date_str} ===")

    entries = fetch_recent_entries()

    if not entries:
        log.info("No recent entries found. Skipping.")
        return

    newsletter = summarize_with_groq(entries)
    log.info(f"Newsletter generated ({len(newsletter)} chars)")

    subject = f"AI 日報 — {date_str}（{len(entries)} 則新聞）"
    html = markdown_to_html(newsletter)
    send_email(subject, html)

    log.info("Done.")


if __name__ == "__main__":
    main()
