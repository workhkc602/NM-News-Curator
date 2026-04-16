import os
import logging
import httpx
import feedparser
import re
from datetime import datetime
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

# ---------------------------------------------------------------------------
# 2. Date Filtering Logic (Smart Omission)
# ---------------------------------------------------------------------------
def is_expired(text):
    """
    Checks if a string contains a date that has already passed.
    This helps filter out 'Closing Dates' that are in the past.
    """
    # Look for common date formats like DD/MM/YYYY, YYYY-MM-DD, or DD Mon YYYY
    date_patterns = [
        r'(\d{1,2})[\/\-\. ](\d{1,2})[\/\-\. ](\d{4})',  # 28/04/2026
        r'(\d{4})[\/\-\. ](\d{1,2})[\/\-\. ](\d{1,2})',  # 2026-04-28
        r'(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})' # 28 Apr 2026
    ]
    
    today = datetime.now()
    
    for pattern in date_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                # Basic parsing attempt
                if len(match[1]) > 2: # Month name version
                    dt_str = f"{match[0]} {match[1]} {match[2]}"
                    found_date = datetime.strptime(dt_str, "%d %b %Y")
                elif len(match[0]) == 4: # YYYY-MM-DD
                    found_date = datetime.strptime(f"{match[0]}-{match[1]}-{match[2]}", "%Y-%m-%d")
                else: # DD/MM/YYYY
                    found_date = datetime.strptime(f"{match[0]}-{match[1]}-{match[2]}", "%d-%m-%Y")
                
                if found_date < today.replace(hour=0, minute=0, second=0, microsecond=0):
                    return True
            except:
                continue
    return False

# ---------------------------------------------------------------------------
# 3. Robust Scraper
# ---------------------------------------------------------------------------
def fetch_html_tenders(url, source_name):
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"}
    entries = []
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=30.0) as client:
            response = client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'lxml')
            
            # Focused search for QS/Tender keywords
            keywords = ["tender", "contract", "consultancy", "eoi", "forecast", "qs", "quantity surveyor", "招標", "顧問"]
            
            for link_tag in soup.find_all('a', href=True):
                text = link_tag.get_text().strip()
                href = link_tag['href']
                
                # Validation: Link must contain keyword and NOT be expired
                if any(k.lower() in text.lower() for k in keywords) and len(text) > 10:
                    if is_expired(text):
                        continue # Skip if date is in the past
                        
                    full_url = urljoin(url, href)
                    
                    # 404 Guard: Quick check if link is alive
                    try:
                        # We use HEAD request to save bandwidth
                        link_check = client.head(full_url, timeout=5.0)
                        if link_check.status_code >= 400: continue 
                    except:
                        continue

                    entries.append({
                        "title": text,
                        "link": full_url,
                        "source_type": "tender",
                        "source_name": source_name
                    })
        return entries
    except Exception as e:
        log.error(f"Error scraping {source_name}: {e}")
        return []

# ---------------------------------------------------------------------------
# 4. AI & Summary Logic
# ---------------------------------------------------------------------------
def summarize(entries: list[dict]) -> str:
    articles_text = ""
    for e in entries:
        articles_text += f"TYPE: {e['source_type'].upper()} | SRC: {e['source_name']}\nTITLE: {e['title']}\nURL: {e['link']}\n\n"

    prompt = f"""You are a senior Business Development Manager for a QS firm.
    
    START with Header:
    To: Senior Partners / Board of Directors
    From: NM News Curator
    Date: {datetime.now().strftime('%B %d, %Y')}
    Subject: Northern Metropolis (NM) & Major Projects: Opportunity Pipeline Report

    ---

    CATEGORIES:
    1. "### Upcoming Tenders & Consultancy Notices"
    2. "### HKSAR Gov Press Releases"
    3. "### NM Development News from Various Media"

    SECTOR GROUPING (For 2 & 3):
    - Transport and Infrastructure, Residential / Public Housing, Commercial / Corporate Fitouts, etc.

    READABILITY & QUALITY RULES:
    - Omit any entry that has a deadline/closing date in the past (before {datetime.now().strftime('%Y-%m-%d')}).
    - Summarize WHY it is a QS lead (cost planning, measurement, contract advisory).
    - FORCE A NEW LINE for the link. Format: [View Source Detail >](URL)
    - Add an empty line between bullets.
    
    Articles:
    {articles_text}"""

    try:
        resp = httpx.post(
            f"{LLM_BASE_URL.strip().rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=150
        )
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Error: {e}"

# ---------------------------------------------------------------------------
# 5. Execution
# ---------------------------------------------------------------------------
def main():
    all_entries = []
    
    # Updated HKHA Link and standard lists
    tender_targets = [
        ("CEDD NM", "https://www.cedd.gov.hk/eng/our-projects/northern-metropolis/index.html"),
        ("HKHA Commercial", "https://www.housingauthority.gov.hk/en/commercial-properties/tender-notices-and-awards/index.html"),
        ("ArchSD QS", "https://www.archsd.gov.hk/en/tenders-notices/consultancies/notices-of-invitation-for-technical-and-fee-proposal.html"),
        ("MTRC Tenders", "https://www.mtr.com.hk/en/corporate/tenders/new_projects.html"),
        ("HSITP Projects", "https://www.hsitp.org/en/tender-notices")
    ]
    
    for name, url in tender_targets:
        all_entries.extend(fetch_html_tenders(url, name))

    # Standard RSS Feeds
    # ... (Add your fetch_rss calls here as per previous versions) ...

    if all_entries:
        digest = summarize(all_entries)
        # Send Email logic here
        print(digest) # For local testing

if __name__ == "__main__":
    main()
