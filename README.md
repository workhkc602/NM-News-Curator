# AI News Curator

A lightweight daily AI news digest that runs as a cron job. It fetches articles from RSS feeds (tech media, AI company blogs, YouTube channels), summarizes them using any AI model, and emails you the digest.

**Total cost: $0.** Works with free tiers of Groq, Gemini, OpenRouter, and more.

## How It Works

```
Cron (daily) → Fetch RSS feeds → Summarize with AI → Email you
```

1. **Fetch**: Pulls the latest articles from all sources in `sources.json` (RSS feeds, including YouTube channels)
2. **Summarize**: Sends articles to any OpenAI-compatible LLM API for a concise digest
3. **Email**: Delivers the formatted newsletter to your inbox

## Quick Start

### 1. Get a free LLM API key

Pick **one** provider — all have free tiers:

| Provider | Free Tier | Base URL | Models |
|----------|-----------|----------|--------|
| **Groq** (recommended) | 6,000 req/day | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| **Google Gemini** | 1,500 req/day | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` |
| **OpenRouter** | Free models available | `https://openrouter.ai/api/v1` | Various (check free tier) |
| **Ollama** (local) | Unlimited | `http://localhost:11434/v1` | Any local model |

### 2. Get a free email service

Pick **one**:

| Provider | Free Tier | `EMAIL_PROVIDER` value |
|----------|-----------|----------------------|
| **Brevo** (recommended) | 300 emails/day | `brevo` |
| **Resend** | 100 emails/day | `resend` |
| **SendGrid** | 100 emails/day | `sendgrid` |
| **Any SMTP** (Gmail, Outlook, etc.) | Varies | `smtp` |

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` with your chosen providers:

```env
# LLM
LLM_API_KEY=your_key_here
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile

# Email
EMAIL_PROVIDER=brevo
EMAIL_API_KEY=your_email_key_here
EMAIL_TO=you@example.com
SENDER_EMAIL=noreply@yourdomain.com
SENDER_NAME=AI News
```

<details>
<summary>Using Gmail SMTP (no API key needed)</summary>

If you don't want to sign up for an email service, use Gmail directly:

1. Enable 2-Step Verification on your Google account
2. Go to myaccount.google.com → Security → App passwords
3. Generate an app password for "Mail"

```env
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your_app_password
SENDER_EMAIL=you@gmail.com
SENDER_NAME=AI News
```
</details>

### 4. Customize Sources

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

**To add a YouTube channel:** Find the channel ID (starts with `UC`) from the channel URL, then use:
```
https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID_HERE
```

### 5. Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### 6. Deploy

Pick any platform that supports cron jobs:

<details>
<summary>Railway (recommended)</summary>

1. Fork this repo to your GitHub account
2. Create a new project on [railway.com](https://railway.com)
3. Connect your GitHub repo (Railway auto-detects the Dockerfile)
4. Set the service type to **Cron Job** with schedule: `0 23 * * *` (= 7am HKT)
5. Add environment variables from your `.env`

</details>

<details>
<summary>Render</summary>

1. Fork this repo to your GitHub account
2. Create a new **Cron Job** on [render.com](https://render.com)
3. Connect your GitHub repo
4. Set the schedule to `0 23 * * *`
5. Set the command to `python main.py`
6. Add environment variables from your `.env`

</details>

<details>
<summary>GitHub Actions (no extra account needed)</summary>

Add `.github/workflows/news.yml` to your forked repo:

```yaml
name: AI News Curator
on:
  schedule:
    - cron: '0 23 * * *'  # 7am HKT
  workflow_dispatch: # manual trigger

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
          LLM_MODEL: ${{ secrets.LLM_MODEL }}
          EMAIL_PROVIDER: ${{ secrets.EMAIL_PROVIDER }}
          EMAIL_API_KEY: ${{ secrets.EMAIL_API_KEY }}
          EMAIL_TO: ${{ secrets.EMAIL_TO }}
          SENDER_EMAIL: ${{ secrets.SENDER_EMAIL }}
          SENDER_NAME: ${{ secrets.SENDER_NAME }}
          LANGUAGE: ${{ secrets.LANGUAGE }}
```

Then go to your repo → Settings → Secrets → add each variable.

</details>

<details>
<summary>Local Mac/Linux (crontab)</summary>

Add to your crontab (`crontab -e`):

```
0 7 * * * cd /path/to/AI-News-Curator && .venv/bin/python main.py
```

Make sure your `.env` is loaded — or export variables in the cron command.

</details>

## Configuration

All configuration is via environment variables:

### LLM

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_API_KEY` | Yes | — | API key for your LLM provider |
| `LLM_BASE_URL` | No | `https://api.groq.com/openai/v1` | OpenAI-compatible API base URL |
| `LLM_MODEL` | No | `llama-3.3-70b-versatile` | Model name |

### Email

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EMAIL_PROVIDER` | No | `brevo` | `brevo`, `resend`, `sendgrid`, or `smtp` |
| `EMAIL_API_KEY` | Yes* | — | API key (* not needed for `smtp`) |
| `EMAIL_TO` | Yes | — | Recipient email |
| `SENDER_EMAIL` | Yes | — | Verified sender email |
| `SENDER_NAME` | No | `AI News` | Sender display name |
| `SMTP_HOST` | smtp only | — | SMTP server hostname |
| `SMTP_PORT` | smtp only | `587` | SMTP port |
| `SMTP_USER` | smtp only | — | SMTP username |
| `SMTP_PASS` | smtp only | — | SMTP password |

### General

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANGUAGE` | No | `zh-HK` | Newsletter language (see below) |
| `HOURS_LOOKBACK` | No | `26` | How many hours back to fetch |
| `SOURCES_FILE` | No | `sources.json` | Path to sources file |
| `TZ_OFFSET` | No | `8` | Your timezone offset from UTC |

### Language Options

| Value | Language |
|-------|----------|
| `zh-HK` | 香港書面中文 (Hong Kong Written Chinese) |
| `zh-TW` | 繁體中文 (Traditional Chinese, Taiwan) |
| `zh-CN` | 简体中文 (Simplified Chinese) |
| `en` | English |
| `ja` | 日本語 (Japanese) |

Or set `LANGUAGE` to any custom value (e.g. `Korean`, `French`, `Thai`) and the LLM will write in that language.

## Project Structure

```
ai-news-curator/
├── main.py           # Main script
├── sources.json      # RSS feed sources (edit this!)
├── requirements.txt  # Python dependencies (just 2)
├── Dockerfile        # For Docker / Railway deployment
├── .env.example      # Environment variable template
└── README.md
```

## License

MIT — do whatever you want with it.
