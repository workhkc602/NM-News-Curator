"""
AI News Curator — Daily AI news digest delivered to your inbox.
Fetches from RSS feeds, summarizes with any OpenAI-compatible LLM, emails via
Brevo, Resend, SendGrid, or SMTP. Designed to run as a cron job.
"""

import os
import json
import logging
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime
from pathlib import Path

import feedparser
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (all from environment variables)
# ---------------------------------------------------------------------------

# LLM (any OpenAI-compatible API)
LLM_API_KEY = os.environ["LLM_API_KEY"]
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")

# Email
EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "brevo").lower()
EMAIL_API_KEY = os.environ.get("EMAIL_API_KEY", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "you@example.com")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "noreply@example.com")
SENDER_NAME = os.environ.get("SENDER_NAME", "AI News")

# SMTP (only needed if EMAIL_PROVIDER=smtp)
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

# General
HOURS_LOOKBACK = int(os.environ.get("HOURS_LOOKBACK", "26"))
LANGUAGE = os.environ.get("LANGUAGE", "zh-HK")
SOURCES_FILE = os.environ.get("SOURCES_FILE", "sources.json")
TZ_OFFSET = int(os.environ.get("TZ_OFFSET", "8"))

# ---------------------------------------------------------------------------
# Language presets (NOW FORCED TO ENGLISH + NORTHERN METROPOLIS FOCUS)
# ---------------------------------------------------------------------------

LANGUAGE_PRESETS = {
    "en": {
        "name": "English",
        "prompt": (
            "Write in **clear, professional English**. "
            "Translate any Chinese news content into natural English. "
            "Keep all Hong Kong-specific terms (e.g. Northern Metropolis, Hung Shui Kiu, New Territories North) in their original English form."
        ),
        "subject_template": "Northern Metropolis Development Digest — {date} ({count} updates)",
        "empty_message": "No Northern Metropolis development updates this week.",
    },
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


def summarize(entries: list[dict]) -> str:
    """Send entries to LLM for summarization via OpenAI-compatible API."""
    lang = get_lang_config()

    if not entries:
        return lang["empty_message"]

    articles_text = ""
    for i, e in enumerate(entries, 1):
        articles_text += f"\n{i}. [{e['source']}] {e['title']} — {e['url']}"

        prompt = f"""You are a news editor specializing in Hong Kong urban development and the Northern Metropolis (北部都會區).

Language: {lang['prompt']}

Rules:
1. Group articles about the same news topic into ONE entry. More sources on the same topic = more important = listed first.
2. Translate any Chinese-language titles or content into natural, professional English.
3. For each source URL, display as a markdown link using the source name: [HKSAR Gov](url) [SCMP](url) [CEDD](url)
4. Mark YouTube videos with **[Video]** tag.

You MUST use EXACTLY this markdown format (no deviations):

## Key Highlights

- **Topic sentence here** — 1 sentence explanation. [Source1](url) [Source2](url)

## Policy Updates

- **Topic** — 1-2 sentence summary. [Source1](url)

## Infrastructure & Projects

- **Topic** — 1-2 sentence summary. [Source1](url) [Source2](url)

## Land Development & Planning

- **Topic** — 1-2 sentence summary. [Source1](url)

## Other Related

- **Topic** — 1 sentence summary. [Source1](url)

Today's articles ({len(entries)} total):
{articles_text}

Output the digest directly using the exact format above. Use ## for section headers. Use - for every bullet. Use **bold** for every topic. Use [name](url) for every link. Skip sections that have no relevant articles. No other formats."""
    resp = httpx.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        json={
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4000,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Email (multi-provider)
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str):
    """Send email via the configured provider."""
    providers = {
        "brevo": _send_brevo,
        "resend": _send_resend,
        "sendgrid": _send_sendgrid,
        "smtp": _send_smtp,
    }
    provider_fn = providers.get(EMAIL_PROVIDER)
    if not provider_fn:
        raise ValueError(
            f"Unknown EMAIL_PROVIDER: '{EMAIL_PROVIDER}'. "
            f"Supported: {', '.join(providers)}"
        )
    provider_fn(subject, html_body)


def _send_brevo(subject: str, html_body: str):
    resp = httpx.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": EMAIL_API_KEY, "Content-Type": "application/json"},
        json={
            "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
            "to": [{"email": EMAIL_TO}],
            "subject": subject,
            "htmlContent": html_body,
        },
        timeout=30,
    )
    resp.raise_for_status()
    log.info(f"Email sent via Brevo: {resp.json()}")


def _send_resend(subject: str, html_body: str):
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {EMAIL_API_KEY}"},
        json={
            "from": f"{SENDER_NAME} <{SENDER_EMAIL}>",
            "to": [EMAIL_TO],
            "subject": subject,
            "html": html_body,
        },
        timeout=30,
    )
    resp.raise_for_status()
    log.info(f"Email sent via Resend: {resp.json()}")


def _send_sendgrid(subject: str, html_body: str):
    resp = httpx.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={
            "Authorization": f"Bearer {EMAIL_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "personalizations": [{"to": [{"email": EMAIL_TO}]}],
            "from": {"email": SENDER_EMAIL, "name": SENDER_NAME},
            "subject": subject,
            "content": [{"type": "text/html", "value": html_body}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    log.info("Email sent via SendGrid")


def _send_smtp(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SENDER_EMAIL, [EMAIL_TO], msg.as_string())
    log.info("Email sent via SMTP")


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

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
    today = datetime.now(timezone(timedelta(hours=TZ_OFFSET)))
    date_str = today.strftime("%Y-%m-%d")
    log.info(f"=== AI News Curator — {date_str} ({lang['name']}) ===")

    entries = fetch_recent_entries()

    if not entries:
        log.info("No recent entries found. Skipping.")
        return

    newsletter = summarize(entries)
    log.info(f"Newsletter generated ({len(newsletter)} chars)")

    subject = lang["subject_template"].format(date=date_str, count=len(entries))
    html = markdown_to_html(newsletter)
    send_email(subject, html)

    log.info("Done.")


if __name__ == "__main__":
    main()
