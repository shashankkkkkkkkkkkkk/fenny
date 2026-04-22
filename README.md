# 🎙️ AI Voice Receptionist

An AI-powered inbound/outbound voice receptionist that handles calls in 10 Indian languages, books appointments on Cal.com, and logs everything to Supabase.

**Stack:** LiveKit (voice) · VoBiz (SIP/telephony) · Sarvam AI (STT + TTS) · Gemini / Groq (LLM) · Cal.com (calendar) · Supabase (database + storage) · FastAPI (dashboard)

---

## 🚀 Deploy on Railway

1. Push this repo to GitHub
2. Go to [Railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**
3. Connect your repository.
4. Railway will auto-detect the project and you can deploy two services from the same repo:
   - **Web Service (FastAPI):** Use `railway.json` for settings (or set start command to `uvicorn ui_server:app --host 0.0.0.0 --port $PORT`).
   - **Worker Service (Agent):** Add the same repo again, but set the start command to `python agent.py start`.
5. Fill in the environment variables for both services (see below).
6. Deploy.

---

## ⚙️ Required Environment Variables

Set these in Render dashboard (or `.env` locally):

| Variable | Where to get it |
|---|---|
| `LIVEKIT_URL` | [cloud.livekit.io](https://cloud.livekit.io) |
| `LIVEKIT_API_KEY` | LiveKit Cloud project settings |
| `LIVEKIT_API_SECRET` | LiveKit Cloud project settings |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) |
| `SARVAM_API_KEY` | [sarvam.ai](https://sarvam.ai) |
| `CAL_API_KEY` | [cal.com/settings/developer](https://cal.com/settings/developer) |
| `CAL_EVENT_TYPE_ID` | Your Cal.com event type numeric ID |
| `SUPABASE_URL` | Supabase project → Settings → API |
| `SUPABASE_KEY` | Supabase project → Settings → API (anon key) |
| `SIP_TRUNK_ID` | LiveKit SIP trunk ID (after VoBiz trunk setup) |
| `VOBIZ_SIP_DOMAIN` | Your VoBiz SIP domain |

**Optional:**

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | LLM fallback if Gemini fails |
| `DEFAULT_TRANSFER_NUMBER` | Phone number to transfer calls to human agent |
| `SUPABASE_S3_ACCESS_KEY` | Call recording storage |
| `SUPABASE_S3_SECRET_KEY` | Call recording storage |
| `SUPABASE_S3_ENDPOINT` | Call recording storage |

See `.env.example` for the full list.

---

## 🗄️ Supabase Setup

Run both SQL files **in order** in your Supabase SQL Editor:

```
1. supabase_setup.sql        — creates all tables
2. supabase_migration_v2.sql — adds analytics columns
```

Both are safe to re-run.

---

## 🏃 Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in your credentials
cp .env.example .env

# Terminal 1 — Dashboard
uvicorn ui_server:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — Voice agent
python agent.py start
```

Dashboard: http://localhost:8000

---

## 📁 Project Structure

```
agent.py          # Voice agent (LiveKit worker)
ui_server.py      # FastAPI dashboard backend
db.py             # Supabase data layer
calendar_tools.py # Cal.com booking
notify.py         # Notification stubs
phone_utils.py    # Phone number normalization
config.json       # Runtime config (do not commit — in .gitignore)
frontend/         # SPA dashboard (HTML + CSS + JS)
supabase_setup.sql
supabase_migration_v2.sql
Procfile          # Render process definitions
render.yaml       # Render blueprint
Dockerfile        # Docker deploy
.env.example      # Environment variable template
```

---

## 🌐 Supported Languages

Hindi · Hinglish · English (IN) · Tamil · Telugu · Gujarati · Bengali · Marathi · Kannada · Malayalam

Auto-detects caller language by default (`lang_preset: multilingual`).

---

## 🎛️ Configuration

Edit `config.json` locally or use environment variables in production.

Key settings:
- `first_line` — Agent's opening greeting
- `agent_instructions` — Full system prompt
- `lang_preset` — `multilingual` / `hindi` / `english` / etc.
- `tts_voice` — `kavya`, `dev`, `ritu`, `priya`, `rohan`, etc.
- `gemini_model` — `gemini-2.0-flash` (default)
- `max_turns` — Max conversation turns before wrap-up (default: 30)
