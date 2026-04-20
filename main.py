import os
import logging
import httpx
import feedparser
import re
import json  # CRITICAL
import time  # CRITICAL
from datetime import datetime, timedelta, timezone # Added timedelta and timezone
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
HOURS_LOOKBACK = int(get_env("HOURS_LOOKBACK", "160"))
# ---------------------------------------------------------------------------
# 2. Advanced Keyword & Date Logic
# ---------------------------------------------------------------------------
NM_MARKERS = [
    # Strategic & High Level
    "Northern Metropolis", "北部都會區", "Four Zones", "NM Highway", "Metropolis Highway",
    # Specific Hubs & Functional Zones
    "San Tin Technopole", "新田科技城", "Innovation and Technology Zone", "I&T Zone",
    "High-end Professional Services", "Logistics Hub", "Boundary Commerce",
    "Blue and Green Recreation", "University Town", "大學城", "UniTown",
    # NDAs & Specific Locations
    "Kwu Tung", "古洞", "Fanling North", "粉嶺北", "Hung Shui Kiu", "洪水橋", "HSK",
    "Ha Tsuen", "廈村", "Yuen Long South", "元朗南", "Lok Ma Chau", "落馬洲", "Hetao", "河套",
    "HSITP", "Ngau Tam Mei", "牛潭尾", "Ma Tso Lung", "馬草壟", "Sandy Ridge", "沙嶺",
    "Lau Fau Shan", "流浮山", "New Territories North", "新界北",
    # Infrastructure & Project Codes
    "Northern Link", "北環綫", "NOL", "Central Rail Link", "中鐵綫", "Western Railway",
    "YL/20", "ND/20", "CE 16/20", "CE 8/20", "CE 9/20", "CE 14/20", "SS R50"
]

BIZ_MARKERS = [
   "Tender", "招標", "Contract", "合約", "Consultancy", "顧問", 
    "EOI", "Forecast", "Expression of Interest", "Technical and Fee Proposal", 
    "Land Sale", "賣地", "片區開發", "Fitting-out", "Fit-out", "Renovation", 
    "翻新", "Tenancy", "租賃", "License", "牌照", "Design and Build", "D&B", 
    "Alteration", "Addition", "A&A", "Maintenance", "Repair"
]

def is_expired(text):
    """Filters out leads with closing dates already in the past."""
    date_patterns = [
        r'(\d{1,2})[\/\-\. ](\d{1,2})[\/\-\. ](\d{4})',
        r'(\d{4})[\/\-\. ](\d{1,2})[\/\-\. ](\d{1,2})',
        r'(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})'
    ]
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for pattern in date_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                if len(match[1]) > 2: # Mon Name
                    dt = datetime.strptime(f"{match[0]} {match[1]} {match[2]}", "%d %b %Y")
                elif len(match[0]) == 4: # YYYY-MM-DD
                    dt = datetime.strptime(f"{match[0]}-{match[1]}-{match[2]}", "%Y-%m-%d")
                else: # DD-MM-YYYY
                    dt = datetime.strptime(f"{match[0]}-{match[1]}-{match[2]}", "%d-%m-%Y")
                if dt < today: return True
            except: continue
    return False

# ---------------------------------------------------------------------------
# 3. Scraping & Data Gathering
# ---------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone
import time

def fetch_rss(url, source_name, source_type):
    headers = {"User-Agent": "Mozilla/5.0"}
    entries = []
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=HOURS_LOOKBACK)
    # ... it will now filter for anything newer than 7 days ago ...

    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=20.0) as client:
            response = client.get(url)
            feed = feedparser.parse(response.content)
            
            for e in feed.entries:
                # Convert RSS time to a Python datetime object
                published_time = None
                if hasattr(e, 'published_parsed') and e.published_parsed:
                    published_time = datetime.fromtimestamp(time.mktime(e.published_parsed), timezone.utc)
                
                # If we have a date, check if it's within our HOURS_LOOKBACK
                if published_time and published_time < threshold:
                    continue 

                entries.append({
                    "title": e.get("title", ""), 
                    "link": e.get("link", ""), 
                    "source_type": source_type, 
                    "source_name": source_name
                })
        return entries
    except Exception as e:
        log.error(f"Error fetching {source_name}: {e}")
        return []

def fetch_html_tenders(url, source_name):
    headers = {"User-Agent": "Mozilla/5.0"}
    entries = []
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=30.0) as client:
            response = client.get(url)
            soup = BeautifulSoup(response.text, 'lxml')
            for tag in soup.find_all('a', href=True):
                text = tag.get_text().strip()
                href = tag['href']
                # Must be Tender-related AND NM-related
                if any(k.lower() in text.lower() for k in BIZ_MARKERS) and len(text) > 10:
                    if any(m.lower() in text.lower() for m in NM_MARKERS):
                        if is_expired(text): continue
                        
                        full_url = urljoin(url, href)
                        # 404 Guard
                        try:
                            check = client.head(full_url, timeout=5.0)
                            if check.status_code >= 400: continue
                        except: continue

                        entries.append({"title": text, "link": full_url, "source_type": "tender", "source_name": source_name})
        return entries
    except: return []

# ---------------------------------------------------------------------------
# 4. AI Summarization & Email
# ---------------------------------------------------------------------------
def summarize(entries):
    articles_text = ""
    for e in entries:
        articles_text += f"[{e['source_type'].upper()}] {e['source_name']}: {e['title']}\nURL: {e['link']}\n\n"

    prompt = f"""You are a senior Business Development Manager for a QS firm.
  
    CRITICAL: Since this is a weekly digest, start with a 3-bullet point 'Executive Strategic Summary' highlighting the single most important tender, the most impactful policy change, and the biggest media trend from the past 7 days.
    
    START with Header:
    To: Senior Partners / Board of Directors
    From: NM News Curator
    Date: {datetime.now().strftime('%B %d, %Y')}
    Subject: Northern Metropolis (NM) & Major Projects: Opportunity Pipeline Report

    STRATEGIC FOCUS SECTORS:
    The firm actively pursues leads in these sectors. For any project found, categorize it under one of these:
    - Transport and Infrastructure
    - Residential / Public Housing
    - Commercial / Corporate Fitouts
    - Retail / Hospitality (Includes Canteens, Catering, and Tenancies)
    - Healthcare / Education
    - Industrial / Data Centre
    - Maintenance / Energy

    CATEGORIES TO ORGANIZE BY:
    1. "### Upcoming Tenders & Consultancy Notices" (Active bidding & Forecasts)
    2. "### HKSAR Gov Press Releases" (Policy & Funding)
    3. "### NM Development News from Various Media" (Market Trends)

    RULES:
    - Analyze every entry through a QS lens (e.g., cost estimation, procurement, contract management, or tenancy valuation).
    - CRITICAL: If a lead involves a "Tenancy," "License," or "Fit-out" in the NM area, highlight its value for A&A works and cost advisory.
    - Use bullet points for each entry. EACH BULLET POINT MUST BE A NEW LINE.
    - Summarize the QS Lead first.
    - MUST FORCE A NEW LINE after the summary text.
    - Omit expired dates. Suggest why it is a QS lead.
    - Place the link on its own line using this format: [View Source Detail >](URL)
    - Add an extra empty line** between different bullet points to prevent "text walls."

    Articles:
    {articles_text}"""

    try:
        resp = httpx.post(f"{LLM_BASE_URL.strip().rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}, timeout=150)
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e: return f"Error: {e}"

def send_email(content):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart(); msg['From'] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg['To'] = EMAIL_TO; msg['Subject'] = f"NM Industry Digest — {datetime.now().strftime('%Y-%m-%d')}"
    msg.attach(MIMEText(content, 'plain'))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(); server.login(SMTP_USER, SMTP_PASS); server.send_message(msg)

def main():
    try:
        log.info("Starting NM-Omni Scraper...")
        all_entries = []
        
        # --- PART A: Tenders ---
        tender_targets = [
            ("CEDD NM", "https://www.cedd.gov.hk/eng/our-projects/northern-metropolis/index.html"),
            ("HKHA Commercial", "https://www.housingauthority.gov.hk/en/commercial-properties/tender-notices-and-awards/index.html"),
            ("HKHA Business", "https://www.housingauthority.gov.hk/en/business-partnerships/tenders/index.html"),
            ("ArchSD", "https://www.archsd.gov.hk/en/tenders-notices/consultancies/notices-of-invitation-for-technical-and-fee-proposal.html"),
            ("MTRC", "https://www.mtr.com.hk/en/corporate/tenders/new_projects.html"),
            ("HSITP Loop", "https://www.hsitp.org/en/tender-notices"),
            ("HKBU", "https://fohome.hkbu.edu.hk/for-suppliers/information/tender-notice.html"),
            ("HSUHK", "https://fo.hsu.edu.hk/supplier/tender-notice/"),
            ("EdUHK", "https://www.eduhk.hk/tender_notice/")
        ]
        
        for name, url in tender_targets:
            all_entries.extend(fetch_html_tenders(url, name))

        # --- PART B: News (JSON) ---
        base_dir = os.path.dirname(os.path.abspath(__file__))
        sources_path = os.path.join(base_dir, 'sources.json')
        
        if os.path.exists(sources_path):
            with open(sources_path, 'r', encoding='utf-8') as f:
                rss_sources = json.load(f)
                for s in rss_sources:
                    all_entries.extend(fetch_rss(s['url'], s['name'], s['category']))
        else:
            log.error(f"Missing sources.json at {sources_path}")

        # --- PART C: Filter ---
        filtered = []
        for e in all_entries:
            if e['source_type'] == 'tender':
                filtered.append(e)
                continue
            search_text = (e.get('title', '') + " " + e.get('body', '')).lower()
            if any(m.lower() in search_text for m in NM_MARKERS):
                e.pop('body', None)
                filtered.append(e)

        if filtered:
            digest = summarize(filtered)
            send_email(digest)
            log.info(f"✅ Sent {len(filtered)} items.")
        else:
            log.info("No relevant items found.")

    except Exception as e:
        # This will force the error to appear in your GitHub log if the script fails
        log.error(f"CRITICAL SCRIPT ERROR: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()
