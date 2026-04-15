import os
import logging
import httpx
import time
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
TZ_OFFSET = int(get_env("TZ_OFFSET", "8"))
HOURS_LOOKBACK = int(get_env("HOURS_LOOKBACK", "168"))

# ---------------------------------------------------------------------------
# 2. QS Sector & Geographic Context (The "Smart Funnel" Markers)
# ---------------------------------------------------------------------------
# We use these markers to catch news that might be relevant before sending to AI
BROAD_MARKERS = [
    "Northern Metropolis", "北部都會區", "San Tin", "新田", "Lok Ma Chau", "落馬洲",
    "Kwu Tung", "古洞", "Hung Shui Kiu", "洪水橋", "Ha Tsuen", "廈村", 
    "Yuen Long", "元朗", "Fanling", "粉嶺", "Sheung Shui", "上水", "Ping Che", "打鼓嶺",
    "Tender", "招標", "Contract", "合約", "Public Works", "工程", "Land Sale", "賣地",
    "Housing", "房屋", "Infrastructure", "基建", "LegCo", "財委會", "GFA", "樓面面積"
]

LANGUAGE_PRESETS = {
    "en": {
        "prompt": "Write in professional English. Focus on technical construction and land development details.",
        "subject_template": "Northern Metropolis BD Digest — {date}",
        "empty_message": "No industry-relevant updates found in the Northern Metropolis this week.",
    },
}

def get_lang_config():
    return LANGUAGE_PRESETS.get(LANGUAGE, LANGUAGE_PRESETS["en"])

# ---------------------------------------------------------------------------
# 3. The "Summarize" Function (Semantic Intelligence)
# ---------------------------------------------------------------------------
def summarize(entries: list[dict]) -> str:
    lang = get_lang_config()
    if not entries:
        return lang["empty_message"]

    articles_text = "\n".join(
        f"- {e.get('title', 'No Title')} | {e.get('link', 'No Link')} | {e.get('published', '')}"
        for e in entries
    )
    
    prompt = f"""You are a senior Business Development Manager for a Quantity Surveying (QS) firm.
Your task is to identify "Work-in-Hand" or "Future Lead" opportunities in the Northern Metropolis (NM).

SECTOR MAPPING (Categorize news into these headers):
- Transport and Infrastructure
- Residential / Public Housing
- Commercial / Retail / Hospitality
- Corporate Fitouts / A&A (Alterations and Addition)
- Healthcare / Life Sciences / Education
- Industrial / Data Centre / Distribution Center
- Civic / Government / Cultural
- Maintenance Contracts / Energy

STRICT FILTERING LOGIC:
1. Is this about a physical development, land sale, funding approval, or a construction contract? 
2. If YES, and it's related to the NM or major urban projects, INCLUDE it.
3. If NO (e.g., crime, national security, general politics, arts, sports), DISCARD it.

Language: {lang['prompt']}

FORMAT:
Group by Sector. For each entry, highlight the PROJECT SCALE (GFA, estimated cost, or units) if mentioned. Use Markdown.

Articles to process:
{articles_text}"""

    for attempt in range(5):
        try:
            log.info(f"Analyzing {len(entries)} articles for QS leads (Attempt {attempt+1})...")
            resp = httpx.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2, # Lower temperature for higher factual accuracy
                },
                timeout=150,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise e

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
    msg['Subject'] = get_lang_config()["subject_template"].format(date=datetime.now().strftime('%Y-%m-%d'))
    msg.attach(MIMEText(content, 'plain'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
    log.info("✅ QS Industry Digest sent successfully!")

def main():
    log.info("=== Starting Northern Metropolis QS News Funnel ===")
    
    # 1. Assume entries are fetched here (Keep your existing scrapers below or in your script)
    # For this example, I'm assuming 'all_entries' is the list of everything scraped.
    all_entries = [] 
    
    # --- [INSERT YOUR SCRAPER FUNCTIONS HERE] ---
    # Example: all_entries.extend(fetch_gov_news())
    
    # 2. Loose Filtering (Contextual Catch)
    filtered = [
        e for e in all_entries 
        if any(m.lower() in e.get('title', '').lower() for m in BROAD_MARKERS)
    ]
    
    log.info(f"Scraped {len(all_entries)} total. Found {len(filtered)} potentially relevant to QS.")

    # 3. Summarize and Send
    if filtered:
        digest = summarize(filtered)
        send_email(digest)
    else:
        log.info("No industry-relevant news to report this week.")

if __name__ == "__main__":
    main()
