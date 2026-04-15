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
# Configuration - ROBUST VERSION (GitHub Actions)
# ---------------------------------------------------------------------------

def get_env(name: str, default: str = None):
    """Safe environment variable loader with clear error."""
    value = os.environ.get(name)
    if value is None:
        if default is not None:
            return default
        log.error(f"❌ MISSING ENVIRONMENT VARIABLE: {name}")
        log.error("Please check GitHub → Settings → Secrets and variables → Actions")
        raise KeyError(f"Missing required environment variable: {name}")
    return value

# LLM (Gemini)
LLM_API_KEY = get_env("LLM_API_KEY")
LLM_BASE_URL = get_env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
LLM_MODEL = get_env("LLM_MODEL", "gemini-2.5-flash")

# Email (Gmail SMTP)
EMAIL_PROVIDER = get_env("EMAIL_PROVIDER", "smtp").lower()
EMAIL_API_KEY = get_env("EMAIL_API_KEY", "")          # not needed for smtp
EMAIL_TO = get_env("EMAIL_TO")
SENDER_EMAIL = get_env("SENDER_EMAIL")
SENDER_NAME = get_env("SENDER_NAME", "Northern Metropolis Digest")

# SMTP (Gmail)
SMTP_HOST = get_env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get_env("SMTP_PORT", "587"))
SMTP_USER = get_env("SMTP_USER")
SMTP_PASS = get_env("SMTP_PASS")

# General
HOURS_LOOKBACK = int(get_env("HOURS_LOOKBACK", "168"))
LANGUAGE = get_env("LANGUAGE", "en")
SOURCES_FILE = get_env("SOURCES_FILE", "sources.json")
TZ_OFFSET = int(get_env("TZ_OFFSET", "8"))
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

def get_lang_config():
    """Return language settings for the requested language."""
    lang_code = LANGUAGE
    if lang_code not in LANGUAGE_PRESETS:
        log.warning(f"Language '{lang_code}' not found, falling back to English")
        lang_code = "en"
    return LANGUAGE_PRESETS[lang_code]
    
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


import time

def summarize(entries: list[dict]) -> str:
    if not entries:
        return lang.get("empty_message", "No updates this week.")

    articles_text = "\n".join(
        f"- {e['title']} | {e['link']} | {e.get('published', '')}"
        for e in entries
    )

    lang = get_lang_config()

    prompt = f"""You are a news editor specializing in Hong Kong urban development and the Northern Metropolis (北部都會區).

Language: {lang['prompt']}

Rules:
1. Group articles about the same news topic into ONE entry.
2. Translate any Chinese content into natural, professional English.
3. Use markdown links: [HKSAR Gov](url) [SCMP](url) etc.
4. Mark videos with **[Video]**.

Use EXACTLY this format:

## Key Highlights
- **Topic** — 1 sentence explanation. [Source](url)

## Policy Updates
- **Topic** — 1-2 sentence summary. [Source](url)

## Infrastructure & Projects
- **Topic** — 1-2 sentence summary. [Source](url)

## Land Development & Planning
- **Topic** — 1-2 sentence summary. [Source](url)

## Other Related
- **Topic** — 1 sentence summary. [Source](url)

Today's articles ({len(entries)} total):
{articles_text}

Output only the markdown digest. Skip empty sections."""
    
    # Retry logic for Gemini 503 errors
    for attempt in range(5):
        try:
            resp = httpx.post(
                LLM_BASE_URL + "/chat/completions",
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
                timeout=120,
            )
            resp.raise_for_status()
            break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 503 and attempt < 4:
                wait = 2 ** attempt  # 1s, 2s, 4s, 8s
                log.warning(f"Gemini 503 (attempt {attempt+1}/5) — retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            log.error(f"Unexpected error: {e}")
            raise

    result = resp.json()
    return result["choices"][0]["message"]["content"].strip()

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
