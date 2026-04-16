import os
import logging
import httpx
import time
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup # Required for HTML scraping

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
LLM_BASE_URL = get_env("LLM_BASE_URL")
LLM_MODEL = get_env("LLM_MODEL", "gemini-3-flash-preview")

SMTP_HOST = get_env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(get_env("SMTP_PORT", "587"))
SMTP_USER = get_env("SMTP_USER")
SMTP_PASS = get_env("SMTP_PASS")
EMAIL_TO = get_env("EMAIL_TO")
SENDER_EMAIL = get_env("SENDER_EMAIL")
SENDER_NAME = get_env("SENDER_NAME", "NM News Curator")

# ---------------------------------------------------------------------------
# 2. Scrapers (RSS & HTML)
# ---------------------------------------------------------------------------

def fetch_rss(url, source_name, source_type):
    """Fetches and parses RSS feeds (Gov & Media)."""
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"}
    try:
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            response = client.get(url, timeout=30.0)
            response.raise_for_status()
        feed = feedparser.parse(response.content)
        return [{
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "description": e.get("description", ""),
            "source_type": source_type,
            "source_name": source_name
        } for e in feed.entries]
    except Exception as e:
        log.error(f"RSS Error {source_name}: {e}")
        return []

def fetch_html_tenders(url, source_name):
    """Scrapes HTML pages for tender links and titles."""
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"}
    try:
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            response = client.get(url, timeout=30.0)
            response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        entries = []
        
        # We look for all links (<a> tags) that might contain tender keywords
        keywords = ["tender", "contract", "consultancy", "expression of interest", "eoi", "forecast", "招標", "合約", "顧問"]
        
        for link in soup.find_all('a', href=True):
            text = link.get_text().strip()
            href = link['href']
            
            # Filter for links that have relevant text
            if any(k.lower() in text.lower() for k in keywords) and len(text) > 10:
                # Ensure link is absolute
                if not href.startswith('http'):
                    from urllib.parse import urljoin
                    href = urljoin(url, href)
                
                entries.append({
                    "title": text,
                    "link": href,
                    "description": "",
                    "source_type": "tender",
                    "source_name": source_name
                })
        
        log.info(f"{source_name}: Scraped {len(entries)} tender links")
        return entries
    except Exception as e:
        log.error(f"HTML Error {source_name}: {e}")
        return []

# ---------------------------------------------------------------------------
# 3. AI Summarization
# ---------------------------------------------------------------------------
def summarize(entries: list[dict]) -> str:
    if not entries: return "No relevant updates found."

    articles_text = ""
    for e in entries:
        articles_text += f"[{e['source_type'].upper()}] Source: {e['source_name']} | {e['title']} | {e['link']}\n"

    prompt = f"""You are a senior Business Development Manager for a QS firm.
    
    START your response with the standard Report Header (To/From/Date/Subject).
    From: NM News Curator
    Date: {datetime.now().strftime('%B %d, %Y')}
    Subject: Northern Metropolis (NM) & Major Projects: Opportunity Pipeline Report

    ---

    INSTRUCTIONS:
    Divide the report into THREE main categories:
    1. "### HKSAR Gov Press Releases" (Articles tagged [GOV])
    2. "### NM Development News from Various Media" (Articles tagged [MEDIA])
    3. "### Upcoming Tenders & Consultancy Notices" (Articles tagged [TENDER])

    Inside category 1 and 2, group leads by these Sectors:
    - Transport and Infrastructure
    - Residential / Public Housing
    - Commercial / Corporate Fitouts
    - Retail / Hospitality
    - Healthcare / Education
    - Industrial / Data Centre
    - Maintenance / Energy

    For each entry, summarize why it's a lead for a QS (e.g., project scale, cost, or land sale).
    
    For the TENDER section, specifically highlight any 'Forecasts' or 'Expression of Interest' dates.

    Articles:
    {articles_text}"""

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
# 4. Main Execution
# ---------------------------------------------------------------------------
def send_email(content: str):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart()
    msg['From'] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg['To'] = EMAIL_TO
    msg['Subject'] = f"NM Industry & Tender Digest — {datetime.now().strftime('%Y-%m-%d')}"
    msg.attach(MIMEText(content, 'plain'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
    log.info("✅ Full Multi-Source Digest with Tenders sent!")

def main():
    log.info("=== Starting Multi-Source NM News & Tender Funnel ===")
    all_entries = []
    
    # 1. GOV NEWS (RSS)
    all_entries.extend(fetch_rss("https://www.info.gov.hk/gia/rss/general_en.xml", "GovHK EN", "gov"))
    all_entries.extend(fetch_rss("https://www.info.gov.hk/gia/rss/general_zh.xml", "GovHK TC", "gov"))
    
    # 2. MEDIA NEWS (RSS)
    all_entries.extend(fetch_rss("https://www.scmp.com/rss/96/feed", "SCMP Property", "media"))
    all_entries.extend(fetch_rss("https://news.mingpao.com/rss/ins/all.xml", "Ming Pao", "media"))
    
    # 3. TENDER NOTICES (HTML Scraping)
    tender_urls = [
        ("CEDD Northern Metropolis", "https://www.cedd.gov.hk/eng/our-projects/northern-metropolis/index.html"),
        ("HK Housing Authority", "https://www.housingauthority.gov.hk/en/business-partnerships/tenders/index.html"),
        ("ArchSD Consultancy", "https://www.archsd.gov.hk/en/tenders-notices/consultancies/notices-of-invitation-for-expression-of-interest.html"),
        ("MTRC New Projects", "https://www.mtr.com.hk/en/corporate/tenders/new_projects.html"),
        ("HSITP Tenders", "https://www.hsitp.org/en/tender-notices"),
        ("HKBU Tenders", "https://fohome.hkbu.edu.hk/for-suppliers/information/tender-notice.html"),
        ("EdUHK Tenders", "https://www.eduhk.hk/tender_notice/"),
        ("Lingnan Tenders", "https://www.ln.edu.hk/fo/supplier"),
    ]
    
    for name, url in tender_urls:
        all_entries.extend(fetch_html_tenders(url, name))

    # FILTERING (Broadened to include Tender keywords)
    NM_MARKERS = ["Northern Metropolis", "北部都會區", "San Tin", "新田", "Hung Shui Kiu", "洪水橋", "Kwu Tung", "古洞", "Fanling", "粉嶺"]
    BIZ_MARKERS = ["Tender", "招標", "Contract", "合約", "GFA", "樓面面積", "Land Sale", "賣地", "Consultancy", "顧問", "EOI", "Expression of Interest", "Forecast"]

    filtered = []
    for e in all_entries:
        text = (e.get('title', '') + " " + e.get('description', '')).lower()
        if any(m.lower() in text for m in NM_MARKERS) or any(m.lower() in text for m in BIZ_MARKERS):
            filtered.append(e)
    
    log.info(f"Total entries: {len(all_entries)} | Filtered leads: {len(filtered)}")

    if filtered:
        digest = summarize(filtered)
        send_email(digest)
    else:
        log.warning("No data gathered.")

if __name__ == "__main__":
    main()
