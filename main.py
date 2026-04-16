import os
import logging
import httpx
import time
import feedparser
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin

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
# 2. Scrapers
# ---------------------------------------------------------------------------

def fetch_rss(url, source_name, source_type):
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"}
    try:
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            response = client.get(url, timeout=30.0)
            response.raise_for_status()
        feed = feedparser.parse(response.content)
        return [{"title": e.get("title", ""), "link": e.get("link", ""), "source_type": source_type, "source_name": source_name} for e in feed.entries]
    except Exception as e:
        log.error(f"RSS Error {source_name}: {e}")
        return []

def fetch_html_tenders(url, source_name):
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"}
    try:
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            response = client.get(url, timeout=30.0)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        entries = []
        keywords = ["tender", "contract", "consultancy", "expression of interest", "eoi", "forecast", "招標", "合約", "顧問"]
        for link in soup.find_all('a', href=True):
            text = link.get_text().strip()
            if any(k.lower() in text.lower() for k in keywords) and len(text) > 10:
                entries.append({"title": text, "link": urljoin(url, link['href']), "source_type": "tender", "source_name": source_name})
        return entries
    except Exception as e:
        log.error(f"HTML Error {source_name}: {e}")
        return []

# ---------------------------------------------------------------------------
# 3. AI Summarization (Enhanced Formatting)
# ---------------------------------------------------------------------------
def summarize(entries: list[dict]) -> str:
    if not entries: return "No relevant updates found."

    articles_text = ""
    for e in entries:
        articles_text += f"TYPE: {e['source_type'].upper()} | SRC: {e['source_name']}\nTITLE: {e['title']}\nURL: {e['link']}\n\n"

    prompt = f"""You are a senior Business Development Manager for a QS firm.
    
    START your response with:
    To: Senior Partners / Board of Directors
    From: NM News Curator
    Date: {datetime.now().strftime('%B %d, %Y')}
    Subject: Northern Metropolis (NM) & Major Projects: Opportunity Pipeline Report

    ---

    INSTRUCTIONS:
    Divide into THREE main categories:
    1. "### Upcoming Tenders & Consultancy Notices"
    2. "### HKSAR Gov Press Releases"
    3. "### NM Development News from Various Media"

    For Categories 2 and 3, group leads by:
    - Transport and Infrastructure
    - Residential / Public Housing
    - Commercial / Corporate Fitouts
    - Retail / Hospitality
    - Healthcare / Education
    - Industrial / Data Centre
    - Maintenance / Energy

    READABILITY RULES (CRITICAL):
    - Use bullet points for every entry.
    - Each bullet point must contain a concise summary of the QS Lead.
    - THE LINK MUST BE ON A NEW LINE directly below its bullet point.
    - Format links as: [Source Link >](URL) 
    - This ensures the text isn't a solid block of characters.

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
                "temperature": 0.1,
            },
            timeout=150,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Error generating summary: {e}"

# ---------------------------------------------------------------------------
# 4. Email & Main
# ---------------------------------------------------------------------------
def send_email(content: str):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart()
    msg['From'] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg['To'] = EMAIL_TO
    msg['Subject'] = f"NM Industry Digest — {datetime.now().strftime('%Y-%m-%d')}"
    
    # We send as HTML so the [Read More] links are clickable
    msg.attach(MIMEText(content, 'plain'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

def main():
    all_entries = []
    # Gov
    all_entries.extend(fetch_rss("https://www.info.gov.hk/gia/rss/general_en.xml", "GovHK", "gov"))
    # Media
    all_entries.extend(fetch_rss("https://www.scmp.com/rss/96/feed", "SCMP", "media"))
    all_entries.extend(fetch_rss("https://news.mingpao.com/rss/ins/all.xml", "Ming Pao", "media"))
    # Tenders
    tender_urls = [
        ("CEDD NM", "https://www.cedd.gov.hk/eng/our-projects/northern-metropolis/index.html"),
        ("HKHA", "https://www.housingauthority.gov.hk/en/business-partnerships/tenders/index.html"),
        ("ArchSD", "https://www.archsd.gov.hk/en/tenders-notices/consultancies/notices-of-invitation-for-expression-of-interest.html"),
        ("MTRC", "https://www.mtr.com.hk/en/corporate/tenders/new_projects.html")
    ]
    for name, url in tender_urls:
        all_entries.extend(fetch_html_tenders(url, name))

    NM_MARKERS = ["Northern Metropolis", "北部都會區", "San Tin", "新田", "Hung Shui Kiu", "洪水橋", "Kwu Tung", "古洞", "Fanling", "粉嶺"]
    BIZ_MARKERS = ["Tender", "招標", "Contract", "合約", "GFA", "樓面面積", "Land Sale", "Consultancy", "顾问", "EOI", "Forecast"]

    filtered = [e for e in all_entries if any(m.lower() in e['title'].lower() for m in NM_MARKERS + BIZ_MARKERS)]
    
    if filtered:
        digest = summarize(filtered)
        send_email(digest)

if __name__ == "__main__":
    main()
