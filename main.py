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
LLM_BASE_URL = get_env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")

# Change from 'gemini-3-flash-preview' to the stable version
LLM_MODEL = get_env("LLM_MODEL", "gemini-1.5-flash")

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
    # 1. Flatten and Validate Input
    clean_entries = []
    for item in entries:
        if isinstance(item, list):
            clean_entries.extend([i for i in item if isinstance(i, dict)])
        elif isinstance(item, dict):
            clean_entries.append(item)
    
    if not clean_entries:
        return "No valid data found to summarize."

    # 2. Build the Articles Text
    articles_text = ""
    for i, e in enumerate(clean_entries):
        articles_text += f"ENTRY #{i+1}\n"
        articles_text += f"SOURCE: {e.get('source_name', 'Unknown')}\n"
        articles_text += f"TYPE: {e.get('source_type', 'news')}\n"
        articles_text += f"TITLE: {e.get('title', 'No Title')}\n"
        articles_text += f"URL: {e.get('link', '')}\n\n"

    # 3. The Prompt (Preserving your full Strategic structure)
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

   # 4. API Call with Retry Logic
    max_retries = 3
    for attempt in range(max_retries):
        try:
            log.info(f"DEBUG: Attempt {attempt + 1}. Using Base URL: {LLM_BASE_URL}")
            log.info(f"AI Attempt {attempt + 1} for {len(clean_entries)} items...")
            resp = httpx.post(
                f"{LLM_BASE_URL.strip().rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
                timeout=300
            )
            
            data = resp.json()

            # If we get a list (the error you just saw), check if it's a 503 to retry
            if isinstance(data, list) and len(data) > 0:
                error_code = data[0].get('error', {}).get('code')
                if error_code == 503 and attempt < max_retries - 1:
                    log.warning("Gemini is busy (503). Retrying in 15 seconds...")
                    time.sleep(15)
                    continue
            
            # Standard success path
            if isinstance(data, dict) and "choices" in data:
                return data["choices"][0]["message"]["content"].strip()
            
            # If it's not a retryable error, return the error message
            return f"Summarization Error: {str(data)}"

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
            return f"Summarization System Error after {max_retries} attempts: {e}"
        
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

    # --- PART C: The Recursive Flattener (The Nuke Fix) ---
        def flatten(items):
            flat = []
            for i in items:
                if isinstance(i, list):
                    flat.extend(flatten(i))
                elif isinstance(i, dict):
                    flat.append(i)
            return flat

        # Force all_entries to be a flat list of dictionaries
        clean_list = flatten(all_entries)

        filtered = []
        for e in clean_list:
            # We now know 'e' MUST be a dictionary because of the flattener
            s_type = e.get('source_type', 'news')
            
            if s_type == 'tender':
                filtered.append(e)
            else:
                title = str(e.get('title', ''))
                # Handle cases where 'body' might be missing entirely
                body = str(e.get('body', e.get('summary', ''))) 
                search_text = (title + " " + body).lower()
                
                if any(m.lower() in search_text for m in NM_MARKERS):
                    # Create a clean version for the AI without the bulky body
                    ai_entry = {
                        "title": e.get('title'),
                        "link": e.get('link'),
                        "source_name": e.get('source_name'),
                        "source_type": s_type
                    }
                    filtered.append(ai_entry)

        # --- PART D: Priority Sort, Cap, and Summarize ---
        if filtered:
            # Sort: Tenders first
            filtered.sort(key=lambda x: 0 if x.get('source_type') == 'tender' else 1)
            
            # Keep only the top 25 items
            final_selection = filtered[:25]
            
            log.info(f"📊 Sending top {len(final_selection)} of {len(filtered)} items to AI.")
            
            digest = summarize(final_selection)
            send_email(digest)
            log.info("✅ Weekly Digest Processed.")
        else:
            log.info("No relevant items found in the 160-hour window.")

    except Exception as e:
        log.error(f"CRITICAL SCRIPT ERROR: {str(e)}", exc_info=True)

if __name__ == "__main__":
    main()
