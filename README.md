# Northern Metropolis (NM) News Curator

A specialized Business Intelligence tool for **Quantity Surveyors** and **Construction Professionals**. This bot monitors the HKSAR Government, public institutions, and media to curate a daily pipeline of tender invitations, consultancy forecasts, and development news related to the **Northern Metropolis**.

**Total cost: $0.** Built to run on GitHub Actions free tier using Google Gemini (Free) or any OpenAI-compatible API.

## How It Works

```
Cron (Daily) → Scrape Tender Portals & RSS → AI Precision Filtering → Email Partners
```

1. **Scrape**: Monitors specific HTML tender portals (CEDD, HKHA, ArchSD, MTRC) and RSS feeds for "hidden" leads.
2. **Filter**: Automatically omits expired tenders and applies a strict **NM Geography Filter** (San Tin, Kwu Tung, etc.).
3. **AI Analyze**: Gemini processes the raw data through a **QS lens**, identifying cost planning and procurement opportunities.
4. **Digest**: Delivers a formatted "Opportunity Pipeline Report" to your Senior Partners' inbox.

## Quick Start

### 1. Get a Free LLM API Key

The bot is optimized for **Google Gemini 1.5 Flash** (1,500 requests/day for free).
* Get your key at: [Google AI Studio](https://aistudio.google.com/)
* **Base URL**: `https://generativelanguage.googleapis.com/v1beta/openai`
* **Model**: `gemini-1.5-flash`

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your details:

```env
# LLM Configuration
LLM_API_KEY=your_gemini_key
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL=gemini-1.5-flash

# Email (SMTP Recommended for Gmail)
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your_gmail_app_password
EMAIL_TO=partner1@firm.com, partner2@firm.com
SENDER_EMAIL=you@gmail.com
SENDER_NAME=NM News Curator
```

### 3. Customize the Pipeline
The bot uses hardcoded logic in `main.py` for high-precision scraping:
* **`NM_MARKERS`**: Includes districts like San Tin, Hung Shui Kiu, and project codes (YL/20, ND/20).
* **`BIZ_MARKERS`**: Catches "Tender," "Consultancy," "Fit-out," and "Tenancy."
* **`Strategic Focus Sectors`**: Organizes reports by Infrastructure, Housing, Healthcare, etc.

## Deployment

### GitHub Actions (Free & Automatic)
This is the recommended way to run the bot daily at no cost.

1. Fork this repo.
2. Go to **Settings > Secrets and Variables > Actions**.
3. Add the following **Secrets**:
   * `LLM_API_KEY`, `SMTP_PASS`, etc. (See Configuration section).
4. The bot will run automatically every day at 7:00 AM HKT (23:00 UTC).

## Sources Monitored

| Sector | Sources |
| :--- | :--- |
| **Government** | CEDD Northern Metropolis, ArchSD Consultancies, HKHA Business & Commercial |
| **Infrastructure** | MTRC New Extensions, HSITP (Lok Ma Chau Loop) |
| **Institutions** | HKBU, HSUHK, EdUHK (University Expansion Tenders) |
| **Media** | SCMP, Ming Pao, GovHK (Press Releases) |

## Configuration Options

| Variable | Description |
| :--- | :--- |
| `LANGUAGE` | Default: `en` (English). Also supports `zh-HK`. |
| `HOURS_LOOKBACK` | Default: `26`. Only fetches fresh news since the last run. |
| `NM_GEOGRAPHY` | (Internal) Filters for 20+ specific Northern Metropolis locations. |

## Project Structure
```text
nm-news-curator/
├── main.py           # Logic for Scraper, Expiry Filter, and AI Summarizer
├── requirements.txt  # Dependencies (httpx, beautifulsoup4, feedparser, lxml)
├── .github/          # GitHub Actions workflow for 7am HKT cron job
└── README.md
```

## License
MIT — For the advancement of the construction industry.
