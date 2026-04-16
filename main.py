import os
import logging
import httpx
import time
import feedparser
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Configuration (GitHub Secrets)
# ---------------------------------------------------------------------------
def get_env(name: str, default: str = None):
    value = os.environ.get(name)
    if value is None:
        if default is not None:
            return default
        log.error(f"❌ MISSING ENVIRONMENT VARIABLE: {name}")
        raise KeyError(f"Missing required environment variable: {name}")
    return value

LLM_API_KEY = get_env("LLM_API_KEY")
LLM_BASE_URL = get_env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
LLM_MODEL = get_env("LLM_MODEL", "gemini-3-flash")

SMTP_HOST = get_env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get_env("SMTP_PORT", "587"))
SMTP_USER = get_env("SMTP_USER")
SMTP_PASS = get_env("SMTP_PASS")
EMAIL_TO = get_env("EMAIL_TO")
SENDER_EMAIL = get_env("SENDER_EMAIL")
SENDER_NAME = get_env("SENDER_NAME", "Northern Metropolis Digest")

LANGUAGE = get_env("LANGUAGE", "en")

# ---------------------------------------------------------------------------
# 2. Scraper Functions (The "Shopping")
# ---------------------------------------------------------------------------
def fetch_hksar_press_releases(lang_code="en"):
    """Fetches general press releases from GovHK RSS."""
    url = f"https://www.info.gov.hk/gia/rss/general_{lang_code}.xml"
    
    # We use a User-Agent to prevent the Gov site from blocking GitHub Actions
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            response = client.get(url, timeout=30.0)
            response.raise_for_status()
            
        feed = feedparser.parse(response.content)
        entries = []
        for entry in feed.entries:
            entries.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "description": entry.get("description", ""),
                "published": entry.get("published", "")
            })
        return entries
    except Exception as e:
        log.error(f"Error fetching HKSAR {lang_code}: {e}")
        return []

# ---------------------------------------------------------------------------
# 3. Summarization Logic (The "Chef")
# ---------------------------------------------------------------------------
LANGUAGE_PRESETS = {
    "en": {
        "prompt": "Write in professional English. Focus on technical construction and land development details.",
        "subject_template": "Northern Metropolis BD Digest — {date}",
        "empty_message": "No industry-relevant updates found in the Northern Metropolis this week.",
    },
}

def get_lang_config():
    return LANGUAGE_PRESETS.get(LANGUAGE, LANGUAGE_PRESETS["en"])

def summarize(entries: list[dict]) -> str:
    lang = get_lang_config()
    if not entries:
        return lang["empty_message"]

    articles_text = "\n".join(
        f"- {e.get('title', 'No Title')} | {e.get('link', 'No Link')} | {e.get('published', '')}"
        for e in entries
    )
    
    prompt = f"""You are a senior Business Development Manager for a Quantity Surveying (QS) firm.
Identify "Work-in-Hand" or "Future Lead" opportunities in the Northern Metropolis (NM).

SECTOR MAPPING:
- Transport and Infrastructure
- Residential / Public Housing
- Commercial / Retail / Hospitality
- Corporate Fitouts / A&A (Alterations and Addition)
- Healthcare / Life Sciences / Education
- Industrial / Data Centre / Distribution Center
- Civic / Government / Cultural
- Maintenance Contracts / Energy

STRICT FILTERING LOGIC:
1. Is this about physical development, land sale, funding, or a contract? 
2. If YES, and it's related to NM or major projects, INCLUDE.
3. If NO (crime, social, sports), DISCARD.

Format by Sector. Highlight PROJECT SCALE (GFA, cost) if mentioned.

Articles:
{articles_text}"""

    try:
        resp = httpx.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}"},
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            timeout=150,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error(f"AI Summary Error: {e}")
        return "Error generating summary."

# ---------------------------------------------------------------------------
# 4. Email & Main Execution
# ---------------------------------------------------------------------------
def send_email(content: str):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart()
    msg['From'] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg['To'] = EMAIL_TO
    msg['Subject'] = get_lang_config()["subject_template"].format(date=datetime.now().strftime('%Y-%m-%d'))
    msg.attach(MIMEText(content, 'plain'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
    log.info("✅ Email sent successfully!")

def main():
    log.info("=== Starting Northern Metropolis & HK Industry News Funnel ===")
    
    # FETCH NEWS
    all_entries = []
    all_entries.extend(fetch_hksar_press_releases("en"))
    all_entries.extend(fetch_hksar_press_releases("zh")) # zh is the code for TC
    
    log.info(f"Total entries scraped: {len(all_entries)}")

    # FILTER NEWS (Geographic or Tender-based)
    NM_MARKERS = ["Northern Metropolis", "北部都會區", "San Tin", "新田", "Hung Shui Kiu", "洪水橋", "Kwu Tung", "古洞", "Fanling", "粉嶺"]
    BIZ_MARKERS = ["Tender", "招標", "Contract", "合約", "GFA", "樓面面積", "Land Sale", "賣地", "Consultancy", "顧問"]

    filtered = []
    for e in all_entries:
        text = (e.get('title', '') + " " + e.get('description', '')).lower()
        if any(m.lower() in text for m in NM_MARKERS) or any(m.lower() in text for m in BIZ_MARKERS):
            filtered.append(e)
    
    log.info(f"Filtered leads: {len(filtered)}")

    # PROCESS & SEND
    if filtered:
        digest = summarize(filtered)
        send_email(digest)
    elif all_entries:
        status_msg = f"Scan completed. No NM leads or Tenders found among {len(all_entries)} articles today."
        send_email(status_msg)
    else:
        log.warning("No data scraped. Check internet or source URLs.")

if __name__ == "__main__":
    main()
