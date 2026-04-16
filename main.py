import os
import logging
import httpx
import time
import feedparser
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Configuration (GitHub Secrets)
# ---------------------------------------------------------------------------
def get_env(name: str, default: str = None):
    value = os.environ.get(name)
    if value is None:
        if default is not None: return default
        raise KeyError(f"Missing environment variable: {name}")
    return value

LLM_API_KEY = get_env("LLM_API_KEY")
LLM_BASE_URL = get_env("LLM_BASE_URL") # Set to: https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL = get_env("LLM_MODEL", "gemini-3-flash-preview")

SMTP_HOST = get_env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get_env("SMTP_PORT", "587"))
SMTP_USER = get_env("SMTP_USER")
SMTP_PASS = get_env("SMTP_PASS")
EMAIL_TO = get_env("EMAIL_TO")
SENDER_EMAIL = get_env("SENDER_EMAIL")
SENDER_NAME = get_env("SENDER_NAME", "NM News Curator")

# ---------------------------------------------------------------------------
# 2. Scrapers (The "Gatherers")
# ---------------------------------------------------------------------------
def fetch_news(url, source_name, source_type="media"):
    """Generic scraper for Gov RSS and Media RSS."""
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"}
    try:
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            response = client.get(url, timeout=30.0)
            response.raise_for_status()
        feed = feedparser.parse(response.content)
        log.info(f"{source_name}: Scraped {len(feed.entries)}")
        
        return [{
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "description": e.get("description", ""),
            "source_type": source_type, # This tag is critical for the AI grouping
            "source_name": source_name
        } for e in feed.entries]
    except Exception as e:
        log.error(f"Error fetching {source_name}: {e}")
        return []

# ---------------------------------------------------------------------------
# 3. AI Summarization (The "Editor")
# ---------------------------------------------------------------------------
def summarize(entries: list[dict]) -> str:
    if not entries: return "No leads found."

    # We format the text so the AI knows the source of each article
    articles_text = ""
    for e in entries:
        articles_text += f"[{e['source_type'].upper()}] {e['title']} | {e['link']}\n"

    prompt = f"""You are a senior Business Development Manager for a Quantity Surveying (QS) firm.
    
    START your response with this header:
    To: Senior Partners / Board of Directors
    From: NM News Curator
    Date: {datetime.now().strftime('%B %d, %Y')}
    Subject: Northern Metropolis (NM) & Major Projects: Opportunity Pipeline Report

    ---

    INSTRUCTIONS:
    Divide your report into TWO distinct main sections:
    1. "### HKSAR Gov Press Releases" (For articles tagged [GOV])
    2. "### NM Development News from Various Media" (For articles tagged [MEDIA])

    Within each section, group leads by these Sectors:
    - Transport and Infrastructure
    - Residential / Public Housing
    - Commercial / Corporate Fitouts
    - Retail / Hospitality
    - Healthcare / Education
    - Industrial / Data Centre
    - Maintenance / Energy

    For each entry, summarize why it's a lead for a QS (e.g., project scale, cost, or land sale).

    Articles to analyze:
    {articles_text}"""

    # URL Logic Fix (Google v1beta)
    api_url = f"{LLM_BASE_URL.strip().rstrip('/')}/chat/completions"

    try:
        resp = httpx.post(
            api_url,
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
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
        return f"Error generating summary: {e}"

# ---------------------------------------------------------------------------
# 4. Email & Execution
# ---------------------------------------------------------------------------
def send_email(content: str):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart()
    msg['From'] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg['To'] = EMAIL_TO
    msg['Subject'] = f"NM Industry Digest — {datetime.now().strftime('%Y-%m-%d')}"
    msg.attach(MIMEText(content, 'plain'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
    log.info("✅ Full Multi-Source Digest sent!")

def main():
    log.info("=== Starting Multi-Source NM Industry News Funnel ===")
    
    all_entries = []
    
    # --- GOV SOURCES ---
    all_entries.extend(fetch_news("https://www.info.gov.hk/gia/rss/general_en.xml", "GovHK EN", source_type="gov"))
    all_entries.extend(fetch_news("https://www.info.gov.hk/gia/rss/general_zh.xml", "GovHK TC", source_type="gov"))
    
    # --- MEDIA SOURCES ---
    all_entries.extend(fetch_news("https://www.scmp.com/rss/96/feed", "SCMP Property", source_type="media"))
    all_entries.extend(fetch_news("https://news.mingpao.com/rss/ins/all.xml", "Ming Pao", source_type="media"))
    all_entries.extend(fetch_news("https://www.thestandard.com.hk/rss/news_section/1", "The Standard", source_type="media"))

    # FILTERING LOGIC
    NM_MARKERS = ["Northern Metropolis", "北部都會區", "San Tin", "新田", "Hung Shui Kiu", "洪水橋", "Kwu Tung", "古洞", "Fanling", "粉嶺"]
    BIZ_MARKERS = ["Tender", "招標", "Contract", "合約", "GFA", "樓面面積", "Land Sale", "賣地", "Consultancy", "顧問"]

    filtered = []
    for e in all_entries:
        text = (e.get('title', '') + " " + e.get('description', '')).lower()
        if any(m.lower() in text for m in NM_MARKERS) or any(m.lower() in text for m in BIZ_MARKERS):
            filtered.append(e)
    
    log.info(f"Total Scraped: {len(all_entries)} | Filtered Leads: {len(filtered)}")

    if filtered:
        digest = summarize(filtered)
        send_email(digest)
    elif all_entries:
        send_email(f"System Status: No leads found among {len(all_entries)} articles today.")
    else:
        log.warning("No data gathered from any source.")

if __name__ == "__main__":
    main()
