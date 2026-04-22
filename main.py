import os
import logging
import httpx
import feedparser
import re
import json  
import time  
import markdown2  # Make sure to run 'pip install markdown2'
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone 
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Set up logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Configuration & Global Keywords
# ---------------------------------------------------------------------------
def get_env(name: str, default: str = None):
    value = os.environ.get(name)
    if value is None:
        if default is not None: return default
        raise KeyError(f"Missing environment variable: {name}")
    return value

# Global Keywords - Moved here so all functions can see them
BIZ_MARKERS = [
    "Tender", "招標", "Contract", "合約", "Consultancy", "顧問", 
    "EOI", "Forecast", "Expression of Interest", "Technical and Fee Proposal", 
    "Land Sale", "賣地", "片區開發", "Fitting-out", "Fit-out", "Renovation", 
    "翻新", "Tenancy", "租賃", "License", "牌照", "Design and Build", "D&B", 
    "Alteration", "Addition", "A&A", "Maintenance", "Repair"
]

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

LLM_API_KEY = get_env("LLM_API_KEY")
LLM_BASE_URL = get_env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
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
# 2. Helpers
# ---------------------------------------------------------------------------
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
# 3. Scraping Functions
# ---------------------------------------------------------------------------
def fetch_rss(url, source_name, source_type, timeout=20.0):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
    }
    entries = []
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=HOURS_LOOKBACK)
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout) as client:
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
                    "body": e.get("summary", ""), 
                    "link": e.get("link", ""), 
                    "source_type": source_type, 
                    "source_name": source_name
                })
        return entries
    except Exception as ex:
        log.error(f"Error fetching {source_name}: {ex}")
        return []

def fetch_html_tenders(url, source_name):
    headers = {"User-Agent": "Mozilla/5.0"}
    entries = []
    try:
        # Use a shorter timeout for individual sites so one slow site doesn't hang the whole bot
        with httpx.Client(headers=headers, follow_redirects=True, timeout=20.0) as client:
            response = client.get(url)
            
            # This is the key: it triggers the 'except' block if the site returns 404, 500, etc.
            response.raise_for_status() 
            
            soup = BeautifulSoup(response.text, 'lxml')
            for tag in soup.find_all('a', href=True):
                text = tag.get_text().strip()
                href = tag['href']
                
                # Filter logic remains untouched to maintain your current accuracy
                if any(k.lower() in text.lower() for k in BIZ_MARKERS) and len(text) > 10:
                    if any(m.lower() in text.lower() for m in NM_MARKERS):
                        if is_expired(text): 
                            continue
                        full_url = urljoin(url, href)
                        entries.append({
                            "title": text, 
                            "link": full_url, 
                            "source_type": "tender", 
                            "source_name": source_name
                        })
        return entries

    except httpx.HTTPStatusError as e:
        # Specifically catches 404/500 errors and logs which site failed
        log.warning(f"⚠️ Site unavailable ({e.response.status_code}) for {source_name}. Skipping...")
        return []
    except httpx.ConnectTimeout:
        log.warning(f"⏱️ Connection timed out for {source_name}. Skipping...")
        return []
    except Exception as ex:
        # Catches any other random errors (parsing, etc.)
        log.error(f"❌ Unexpected Scrape Error for {source_name}: {ex}")
        return []

# ---------------------------------------------------------------------------
# 4. AI & Email Logic
# ---------------------------------------------------------------------------
def summarize(entries):
    clean_entries = []
    for item in entries:
        if isinstance(item, list):
            clean_entries.extend([i for i in item if isinstance(i, dict)])
        elif isinstance(item, dict):
            clean_entries.append(item)
    
    if not clean_entries:
        return "No valid data found to summarize."

    articles_text = ""
    for i, e in enumerate(clean_entries):
        articles_text += f"ENTRY #{i+1}\n"
        articles_text += f"SOURCE: {e.get('source_name', 'Unknown')}\n"
        articles_text += f"TYPE: {e.get('source_type', 'news')}\n"
        articles_text += f"TITLE: {e.get('title', 'No Title')}\n"
        articles_text += f"URL: {e.get('link', '')}\n\n"

    prompt = f"""You are a senior Business Development Manager for a QS firm.
    
    Start with the following:
    To: Senior Partners / Board of Directors
    From: NM News Curator
    Date: {datetime.now().strftime('%B %d, %Y')}
    Subject: Northern Metropolis (NM) & Major Projects: Opportunity Pipeline Report

    CRITICAL: Since this is a weekly digest, include a 3-bullet point 'Executive Strategic Summary' before listing the entries. Add an extra empty line before this summary.

    STRATEGIC FOCUS SECTORS:
    - Transport and Infrastructure
    - Residential / Public Housing
    - Commercial / Corporate Fitouts
    - Retail / Hospitality
    - Healthcare / Education
    - Industrial / Data Centre
    - Maintenance / Energy

    CATEGORIES TO ORGANIZE BY:
    1. "### Upcoming Tenders & Consultancy Notices"
    2. "### HKSAR Gov Press Releases"
    3. "### NM Development News from Various Media"

    RULES:
    - Analyze every entry through a QS lens (cost estimation, procurement, contract management, or tenancy valuation).
    - FORMATTING: Each bullet point MUST follow this structure:
      * *QS Lead:* [Detailed QS-specific insight]
        *Sector:* [Chosen Strategic Sector]
        [Detailed explanation of the technical components, e.g., MEP, cleanroom, or A&A value.]
        [View Source Detail >](URL)
    - Omit expired dates. Add an extra empty line between different bullet points.

    Articles:
    {articles_text}"""

    max_retries = 3
    for attempt in range(max_retries):
        try:
            url = f"{LLM_BASE_URL}/chat/completions"
            log.info(f"AI Attempt {attempt + 1}. Routing via: {LLM_MODEL}")
            
            resp = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "system", "content": "You are a helpful assistant."},
                                 {"role": "user", "content": prompt}],
                    "temperature": 0.1
                }, 
                timeout=300
            )
            
            if resp.status_code != 200:
                log.error(f"AI Error {resp.status_code}: {resp.text}")
                if attempt < max_retries - 1:
                    time.sleep(15)
                    continue
                return f"Summarization Error: {resp.status_code}"

            data = resp.json()
            return data['choices'][0]['message']['content'].strip()

        except Exception as e:
            log.error(f"AI Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(10)
                continue
            return f"System Error: {e}"

def send_email(content):
    if not content or "Error" in str(content):
        log.error("Invalid content. Skipping email.")
        return
    
    try:
        msg = MIMEMultipart()
        msg['From'] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
        msg['To'] = EMAIL_TO
        msg['Subject'] = f"NM Weekly Digest — {datetime.now().strftime('%Y-%m-%d')}"

        # --- THE MAGIC SECTION: Convert Markdown to HTML ---
        # We use 'extras' to ensure links and line breaks look right
        html_body = markdown2.markdown(str(content), extras=["break-on-newline", "links"])

        # Add some CSS styling to make it look premium for the Board
        full_html = f"""
        <html>
          <head>
            <style>
              body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; }}
              h3 {{ color: #004a99; border-bottom: 2px solid #004a99; padding-bottom: 5px; margin-top: 25px; }}
              b {{ color: #000; }}
              ul {{ padding-left: 20px; }}
              li {{ margin-bottom: 12px; }}
              a {{ color: #0066cc; text-decoration: none; font-weight: bold; }}
              .footer {{ font-size: 12px; color: #777; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px; }}
            </style>
          </head>
          <body>
            {html_body}
            <div class="footer">
              This report was generated by the NM News Curator AI. Confidential for Senior Partners / Board of Directors.
            </div>
          </body>
        </html>
        """
        
        # Change 'plain' to 'html' here
        msg.attach(MIMEText(full_html, 'html'))
        
        # --- SMTP SENDING ---
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
            log.info("Email sent successfully in HTML format!")
            
    except Exception as e:
        log.error(f"SMTP Error: {e}")

# ---------------------------------------------------------------------------
# 5. Main Execution
# ---------------------------------------------------------------------------
def main():
    try:
        log.info("Starting NM-Omni Scraper...")
        all_entries = []
        
        tender_targets = [
            # --- PUBLIC SECTOR ---
            ("NM Portal", "https://www.nm.gov.hk/en/tender-contracts"),
            ("CEDD NM Projects", "https://www.cedd.gov.hk/eng/our-projects/northern-metropolis/index.html"),
            ("HKHA Business", "https://www.housingauthority.gov.hk/en/business-partnerships/tenders/index.html"),
            ("ArchSD Forecast", "https://www.archsd.gov.hk/en/newforecastconsultancies.html"),
            ("ArchSD EOI", "https://www.archsd.gov.hk/en/tenders-notices/consultancies/notices-of-invitation-for-expression-of-interest.html"),
            ("ArchSD Tech Proposals", "https://www.archsd.gov.hk/en/tenders-notices/consultancies/notices-of-invitation-for-technical-and-fee-proposal.html"),
            ("MTRC New Projects", "https://www.mtr.com.hk/en/corporate/tenders/new_projects.html"),
            ("MTRC Operating", "https://www.mtr.com.hk/en/corporate/tenders/or.html"),
            ("MTRC Property", "https://www.mtr.com.hk/en/corporate/tenders/property_services.html"),

            # --- INSTITUTIONS ---
            ("HSITP Loop", "https://www.hsitp.org/en/tender-notices"),
            ("EdUHK Tenders", "https://www.eduhk.hk/tender_notice/"),
            ("HSUHK Tenders", "https://fo.hsu.edu.hk/supplier/tender-notice/"),
            ("HKBU Tenders", "https://fohome.hkbu.edu.hk/for-suppliers/information/tender-notice.html"),
            ("PolyU Tenders", "https://tendering.polyu.edu.hk/Guest/en/index.htm"),
            ("CityU Tenders", "https://etender.cityu.edu.hk/Guest/en/GeneralInfo.aspx"),
            ("HKUST Tenders", "https://puro.hkust.edu.hk/etendering")
        ]

        # 1. Scrape Tenders
        for name, url in tender_targets:
            all_entries.extend(fetch_html_tenders(url, name))

        # 2. Start News Scrape
        sources_path = os.path.join(os.path.dirname(__file__), 'sources.json')
        
        if os.path.exists(sources_path):
            with open(sources_path, 'r', encoding='utf-8') as f:
                rss_sources = json.load(f)
                
                # Define your anchors
                primary_anchors = ["SCMP - Hong Kong News", "HK01 - Community News"]
                
                for s in rss_sources:
                    # Logic Switch: Use BeautifulSoup for broken RSS, use fetch_rss for others
                    if "Wen Wei Po" in s['name'] or "Ta Kung Pao" in s['name']:
                        log.info(f"Using Manual Scraper for: {s['name']}")
                        fetched_news = fetch_web_headlines(s['url'], s['name'], s['category'])
                    else:
                        # Anchor check for timeout
                        current_timeout = 30.0 if s['name'] in primary_anchors else 15.0
                        if s['name'] in primary_anchors:
                            log.info(f"Checking primary anchor: {s['name']} (Extended Timeout)")
                        
                        fetched_news = fetch_rss(s['url'], s['name'], s['category'], timeout=current_timeout)
                    
                    # Label and store
                    for item in fetched_news:
                        item['source_type'] = 'news'
                        all_entries.append(item) 

        # 3. Processing
        def flatten(items):
            flat = []
            for i in items:
                if isinstance(i, list): flat.extend(flatten(i))
                elif isinstance(i, dict): flat.append(i)
            return flat

        clean_list = flatten(all_entries)
        log.info(f"Total entries scraped: {len(clean_list)}")
        
        filtered = []
        for e in clean_list:
            title = str(e.get('title', ''))
            body = str(e.get('body', e.get('summary', '')))
            search_text = f"{title} {body}".lower()
            
            is_nm_relevant = any(m.lower() in search_text for m in NM_MARKERS)
            if is_nm_relevant:
                filtered.append(e)

        log.info(f"Entries matching NM markers: {len(filtered)}")

        # 4. Sorting and Emailing
        if filtered:
            govt_portals = [t[0] for t in tender_targets]
            filtered.sort(key=lambda x: 0 if x.get('source_name') in govt_portals else 1)
            
            final_selection = filtered[:35] 
            log.info(f"Sending {len(final_selection)} items to AI.")
            
            digest = summarize(final_selection)
            if digest and "Error" not in str(digest):
                send_email(digest)
                log.info("Process complete: Email sent.")
            else:
                log.warning("AI Summary returned nothing or an error.")
        else:
            log.warning("Workflow finished: 0 items matched NM markers.")

    except Exception as e:
        log.error(f"CRITICAL SCRIPT ERROR: {str(e)}", exc_info=True)
