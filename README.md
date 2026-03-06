# AI News Curator

A lightweight daily AI news digest that runs as a cron job. It fetches articles from RSS feeds (tech media, AI company blogs, YouTube channels), summarizes them using a free LLM, and emails you the digest.

**Total cost: $0.** Uses free tiers of Groq (LLM) and Brevo (email).

## How It Works

```
Cron (daily) → Fetch RSS feeds → Summarize via Groq → Email via Brevo
```

1. **Fetch**: Pulls the latest articles from all sources in `sources.json` (RSS feeds, including YouTube channels)
2. **Summarize**: Sends articles to Groq's free API (Llama 3.3 70B) for a concise digest
3. **Email**: Delivers the formatted newsletter to your inbox via Brevo's transactional email API

## Quick Start

### 1. Get API Keys (both free)

| Service | Free Tier | Sign Up |
|---------|-----------|---------|
| **Groq** | 6,000 requests/day | [console.groq.com](https://console.groq.com) |
| **Brevo** | 300 emails/day | [brevo.com](https://www.brevo.com) |

Brevo requires a verified sender domain or email address. Follow their [setup guide](https://help.brevo.com/hc/en-us/articles/12163873498898) to verify your domain.

### 2. Configure

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```env
GROQ_API_KEY=gsk_your_key_here
BREVO_API_KEY=xkeysib-your_key_here
EMAIL_TO=you@example.com
BREVO_SENDER_EMAIL=noreply@yourdomain.com
BREVO_SENDER_NAME=AI News
```

### 3. Customize Sources

Edit `sources.json` to add or remove RSS feeds:

```json
[
  {
    "name": "TechCrunch AI",
    "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "category": "tech-media"
  }
]
```

**To add a YouTube channel:** Find the channel ID from the channel's page URL, then use:
```
https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID_HERE
```

### 4. Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### 5. Deploy (Railway)

1. Push this repo to GitHub
2. Create a new project on [Railway](https://railway.com)
3. Connect your GitHub repo
4. Set the service type to **Cron Job** with schedule: `0 23 * * *` (= 7am HKT / adjust for your timezone)
5. Add environment variables from your `.env`

That's it. You'll receive a daily AI digest in your inbox.

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROQ_API_KEY` | Yes | — | Groq API key |
| `BREVO_API_KEY` | Yes | — | Brevo API key |
| `EMAIL_TO` | Yes | — | Recipient email address |
| `BREVO_SENDER_EMAIL` | Yes | — | Verified sender email in Brevo |
| `BREVO_SENDER_NAME` | No | `AI News` | Sender display name |
| `GROQ_MODEL` | No | `llama-3.3-70b-versatile` | Groq model to use |
| `LANGUAGE` | No | `zh-HK` | Newsletter language (see below) |
| `HOURS_LOOKBACK` | No | `26` | How many hours back to fetch articles |
| `SOURCES_FILE` | No | `sources.json` | Path to sources file |

### Language Options

Set the `LANGUAGE` env var to one of:

| Value | Language |
|-------|----------|
| `zh-HK` | 香港書面中文 (Hong Kong Written Chinese) |
| `zh-TW` | 繁體中文 (Traditional Chinese, Taiwan) |
| `zh-CN` | 简体中文 (Simplified Chinese) |
| `en` | English |
| `ja` | 日本語 (Japanese) |

Or set `LANGUAGE` to any custom value (e.g. `Korean`, `French`) and the LLM will attempt to write in that language.

## Project Structure

```
ai-news-curator/
├── main.py           # Main script
├── sources.json      # RSS feed sources (edit this!)
├── requirements.txt  # Python dependencies
├── Dockerfile        # For Railway / Docker deployment
├── .env.example      # Environment variable template
└── README.md
```

## License

MIT — do whatever you want with it.
