import os
import logging
import httpx
import feedparser
import re
import json  
import time  
from datetime import datetime, timedelta, timezone 
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Set up logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Configuration
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
# 2. Keywords
# ---------------------------------------------------------------------------
NM_MARKERS = [
    "Northern Metropolis", "北部都會區", "Four Zones", "NM Highway", "Metropolis Highway",
    "San Tin Technopole", "新田科技城", "Innovation and Technology Zone", "I&T Zone",
    "High-end Professional Services", "Logistics Hub", "Boundary Commerce",
    "Blue and Green Recreation", "University Town", "大學城", "UniTown",
    "Kwu Tung", "古洞", "Fanling North", "粉嶺北", "Hung Shui Kiu", "洪水橋", "HSK",
    "Ha Tsuen", "廈村", "Yuen Long South", "元朗南", "Lok Ma Chau", "落馬洲", "Hetao", "河套",
    "HSITP", "Ngau Tam Mei", "牛潭尾", "Ma Tso Lung", "馬草壟", "Sandy Ridge", "沙嶺",
    "Lau Fau Shan", "流浮山", "New Territories North", "新界北",
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
                if len(match[1]) > 2: 
                    dt = datetime.strptime(f"{match[0]} {match[1]} {match[2]}", "%d %b %Y")
                elif len(match[0]) == 4: 
                    dt = datetime.strptime(f"{match[0]}-{match[1]}-{match[2]}", "%Y-%m-%d")
                else: 
                    dt = datetime.strptime(f"{match[0]}-{match[1]}-{match[2]}", "%d-%m-%Y")
                if dt < today: return True
            except: continue
    return False

# ---------------------------------------------------------------------------
# 3. Scraping
# ---------------------------------------------------------------------------
def fetch_rss(url, source_name, source_type):
    headers = {"User-Agent": "Mozilla/5.0"}
    entries = []
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=HOURS_LOOKBACK)

    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=20.0) as client:
            response = client.get(url)
            feed = feedparser.parse(response.content)
            for e in feed.entries:
                published_time = None
                if hasattr(e, 'published_parsed') and e.published_parsed:
                    published_time = datetime.fromtimestamp(time.mktime(e.published_parsed), timezone.utc)
                
                if published_time and published_time < threshold:
                    continue 

                entries.append({
                    "title": e.get("title", ""), 
                    "body": e.get("summary", ""), # ADDED THIS FOR FILTERING
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
                if any(k.lower() in text.lower() for k in BIZ_MARKERS) and len(text) > 10:
                    if any(m.lower() in text.lower() for m in NM_MARKERS):
                        if is_expired(text): continue
                        full_url = urljoin(url, href)
                        try:
                            check = client.head(full_url, timeout=5.0)
                            if check.status_code >= 400: continue
                        except: continue
                        entries.append({"title": text, "link": full_url, "source_type": "tender", "source_name": source_name})
        return entries
    except: return []

# ---------------------------------------------------------------------------
# 4. AI & Email
# ---------------------------------------------------------------------------
def summarize(entries):
    # 1. FLATTEN AND VALIDATE (The Fix)
    # This ensures that even if a scraper accidentally returns [[dict]], we turn it into [dict]
    clean_entries = []
    for item in entries:
        if isinstance(item, list):
            # If it's a list, look inside it
            for sub_item in item:
                if isinstance(sub_item, dict):
                    clean_entries.append(sub_item)
        elif isinstance(item, dict):
            clean_entries.append(item)
    
    if not clean_entries:
        return "No valid data entries found to summarize."

    # 2. PREPARE TEXT
    articles_text = ""
    for i, e in enumerate(clean_entries):
        # Using .get() ensures that if a key is missing, it returns 'N/A' instead of crashing
        articles_text += f"ENTRY #{i+1}\n"
        articles_text += f"SOURCE: {e.get('source_name', 'Unknown')}\n"
        articles_text += f"TYPE: {e.get('source_type', 'news')}\n"
        articles_text += f"TITLE: {e.get('title', 'No Title')}\n"
        articles_text += f"URL: {e.get('link', '')}\n\n"

    # 3. THE PROMPT
    prompt = f"""You are a senior Business Development Manager for a QS firm.
    
    To: Senior Partners / Board of Directors
    From: NM News Curator
    Date: {datetime.now().strftime('%B %d, %Y')}
    Subject: Northern Metropolis (NM) & Major Projects: Opportunity Pipeline Report

    CRITICAL: Since this is a weekly digest, start with a 3-bullet point 'Executive Strategic Summary' highlighting the single most important tender, the most impactful policy change, and the biggest media trend from the past 7 days.

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
    1. "### Upcoming Tenders & Consultancy Notices" (Source type: tender)
    2. "### HKSAR Gov Press Releases" (Source name: Gov Press)
    3. "### NM Development News from Various Media" (Source type: news/youtube)

    RULES:
    - Analyze every entry through a QS lens (cost estimation, procurement, contract management, or tenancy valuation).
    - FORMATTING: Each bullet point MUST follow this structure:
      * **QS Lead:** [Detailed QS-specific insight]
        **Sector:** [Chosen Strategic Sector]
        [Detailed explanation of the technical components, e.g., MEP, cleanroom, or A&A value.]
        [View Source Detail >](URL)
    - Omit expired dates. Add an extra empty line between different bullet points.

    Articles:
    {articles_text}"""

   # 4. API CALL
    try:
        resp = httpx.post(f"{LLM_BASE_URL.strip().rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}, timeout=150)
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e: 
        log.error(f"AI Error: {e}")
        return f"Summarization Error: {e}"
        
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

       # --- PART C: Filter ---
        filtered = []
        for e in all_entries:
            if not isinstance(e, dict): continue
            
            if e.get('source_type') == 'tender':
                filtered.append(e)
                continue
            
            text_to_scan = (e.get('title', '') + " " + e.get('body', '')).lower()
            if any(m.lower() in text_to_scan for m in NM_MARKERS):
                e.pop('body', None) 
                filtered.append(e)

        # --- NEW LOGIC: Priority Sort & Cap at 25 ---
        if filtered:
            # Sort: Tenders first, then News/Media
            # lambda returns 0 for tender, 1 for everything else (0 comes before 1)
            filtered.sort(key=lambda x: 0 if x.get('source_type') == 'tender' else 1)
            
            # Keep only the top 25 items
            final_selection = filtered[:25]
            
            log.info(f"📊 Filtered {len(filtered)} items. Capping at top {len(final_selection)} for AI.")
            
            digest = summarize(final_selection) # Send the capped list
            send_email(digest)
            log.info(f"✅ Successfully sent email digest.")
        else:
            log.info("No relevant items found.")

    except Exception as e:
        log.error(f"CRITICAL SCRIPT ERROR: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()
