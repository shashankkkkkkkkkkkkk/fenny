# 📞 InboundAIVoice - Complete Setup & Guide

## 🎯 What This Folder Does

This is a **Production-Ready LiveKit Outbound AI Voice Calling Agent** that allows you to:

1. **Make Outbound Phone Calls** — Automated AI agent places calls to phone numbers via SIP trunks (Vobiz)
2. **Hold Natural Conversations** — AI uses OpenAI LLM (gpt-4o-mini) to understand and respond naturally
3. **Speech Processing** — Uses Sarvam AI for both speech-to-text (STT) and text-to-speech (TTS)
4. **Book Appointments** — Agent can schedule calls on Cal.com
5. **Multi-language Support** — Handles Hindi, Hinglish, English, Tamil, Telugu, Gujarati, Bengali, Marathi, Kannada, Malayalam
6. **Log Everything** — Stores call logs, recordings, and metadata in Supabase database
7. **Send Notifications** — Alerts you via Telegram when calls complete
8. **Rate Limiting** — Prevents abuse (5 calls/hour per phone number)
9. **Custom Per-Client Configs** — Different personalities/instructions per phone number

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      YOUR APPLICATION                           │
└─────────────┬───────────────────────────────────────────────────┘
              │
     ┌────────▼────────┐          ┌──────────────────┐
     │   make_call.py  │         │   ui_server.py   │
     │  (Dispatcher)   │         │  (Dashboard)     │
     └────────┬────────┘         │ http://8000      │
              │                   └──────────────────┘
              │ Dispatch Request
              │ (Phone Number)
              │
     ┌────────▼────────────────────────────────────────┐
     │          LiveKit Cloud Service                   │
     │  (WebRTC & Room Management)                     │
     └────────┬───────────────────────────────────────┘
              │
     ┌────────▼────────────────────────────────────────┐
     │          agent.py (Main Worker)                 │
     │  ┌─────────────────────────────────────────┐    │
     │  │ LLM: OpenAI gpt-4o-mini (Conversation) │    │
     │  │ STT: Sarvam AI (Speech to Text)        │    │
     │  │ TTS: Sarvam AI (Kavya voice, Hindi)    │    │
     │  │ DB: Supabase (Call logs & recordings)  │    │
     │  │ API: Cal.com (Booking appointments)    │    │
     │  │ SMS: Telegram (Send notifications)    │    │
     │  │ SIP: Vobiz (Actual phone calls)        │    │
     │  └─────────────────────────────────────────┘    │
     └────────────────────────────────────────────────┘
```

---

## 🔌 Services & APIs Required

You **MUST** have accounts and API keys for these services:

| Service | Purpose | Where to Get | Cost |
|---------|---------|-------------|------|
| **LiveKit Cloud** | Voice/video infrastructure & room management | [cloud.livekit.io](https://cloud.livekit.io) | Free tier available |
| **OpenAI API** | LLM for conversation intelligence | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | Pay-as-you-go (~$0.15/call) |
| **Sarvam AI** | Speech-to-text + text-to-speech | [app.sarvam.ai](https://app.sarvam.ai) | Free credits + pay-as-you-go |
| **Supabase** | PostgreSQL database + file storage | [app.supabase.com](https://app.supabase.com) | Free tier (5GB storage) |
| **Cal.com** | Appointment/meeting scheduling | [app.cal.com](https://app.cal.com) | Free tier available |
| **Telegram** | Push notifications about call status | [@BotFather](https://t.me/BotFather) | Free |
| **Vobiz** | SIP trunk (actual phone calls) | [Vobiz Console](https://vobiz.ai) | Paid (voice termination rates) |

---

## 🔐 API Keys & Credentials Needed

Create a `.env` file (copy from `.env.example`) with these variables:

```env
# ═══════════════════════════════════════════
# REQUIRED FOR BASIC OPERATION
# ═══════════════════════════════════════════

# LiveKit Cloud - Get from: https://cloud.livekit.io/projects
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=APIxxxxxxxxxxxxxxxx
LIVEKIT_API_SECRET=your_api_secret

# OpenAI - Get from: https://platform.openai.com/api-keys
OPENAI_API_KEY=sk-proj-xxxxxx...

# Sarvam AI - Get from: https://app.sarvam.ai/settings/api-keys
SARVAM_API_KEY=sk_xxxxxxxxxxxx

# ═══════════════════════════════════════════
# REQUIRED FOR CALLING (SIP TRUNK)
# ═══════════════════════════════════════════

# Vobiz SIP - Get from Vobiz Console
VOBIZ_SIP_DOMAIN=xxx.sip.vobiz.ai
VOBIZ_USERNAME=your_username
VOBIZ_PASSWORD=your_password
VOBIZ_OUTBOUND_NUMBER=+91XXXXXXXXXX    # Your DID number

# ═══════════════════════════════════════════
# OPTIONAL BUT RECOMMENDED
# ═══════════════════════════════════════════

# Supabase Database - Get from: https://app.supabase.com/project/[id]/settings/api
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIs...

# Supabase S3 Storage (for call recordings)
SUPABASE_S3_ACCESS_KEY=your_access_key_id
SUPABASE_S3_SECRET_KEY=your_secret_key
SUPABASE_S3_ENDPOINT=https://your-project.supabase.co/storage/v1/s3
SUPABASE_S3_REGION=ap-south-1

# Cal.com Booking - Get from: https://app.cal.com/settings/developer/api-keys
CAL_API_KEY=cal_live_xxxxx
CAL_EVENT_TYPE_ID=1234567

# Telegram Notifications - Create bot via @BotFather
TELEGRAM_BOT_TOKEN=123456789:AAxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789

# ═══════════════════════════════════════════
# OPTIONAL
# ═══════════════════════════════════════════

# Error tracking
SENTRY_DSN=https://xxxxx@xxxxx.ingest.sentry.io/xxxxx

# Call transfer fallback
DEFAULT_TRANSFER_NUMBER=+91XXXXXXXXXX
```

---

## 🚀 How to Run the Live Model

### **Phase 1: Setup (One-time)**

1. **Verify venv is activated:**
   ```powershell
   cd d:\InboundAIVoice-main
   .venv\Scripts\activate
   ```
   *(You should see `(.venv)` at the start of your prompt)*

2. **Configure credentials:**
   - Copy `.env.example` to `.env` or `.env.local`
   - Fill in all API keys (see table above)
   - For testing, you need at least:
     - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
     - `OPENAI_API_KEY`
     - `SARVAM_API_KEY`

3. **Create LiveKit SIP Trunk** (if not already done):
   ```powershell
   .venv\Scripts\python setup_trunk.py
   ```
   - This creates the SIP trunk and connects Vobiz credentials
   - You'll get a `TRUNK_ID` (starts with `ST_...`)
   - Copy this ID to `agent.py` line ~25

---

### **Phase 2: Start the Web Dashboard (Optional but Recommended)**

Open **Terminal 1** and run:

```powershell
.venv\Scripts\python ui_server.py
```

**Output should show:**
```
INFO:     Uvicorn running on http://127.0.0.1:8000
```

🌐 **Open browser:** http://localhost:8000

This UI lets you:
- View/edit agent instructions (personality, tone, etc.)
- Select LLM model, TTS voice, STT settings
- Safely paste API keys without editing code
- Monitor live call status

---

### **Phase 3: Start the Voice Agent (Main Worker)**

Open **Terminal 2** (keep Terminal 1 running) and run:

```powershell
.venv\Scripts\python agent.py dev
```

**Wait for this message:**
```
INFO:livekit.agents:registered worker "outbound-caller" ...
INFO:outbound-agent:Agent started successfully
```

✅ **Agent is now LIVE!** It's listening for dispatch requests.

---

### **Phase 4: Make Your First Call**

Open **Terminal 3** and run:

```powershell
.venv\Scripts\python make_call.py --to=+919876543210
```

Replace `+919876543210` with your test phone number.

**Watch Terminal 2** for logs:
```
Initiating outbound call to +919876543210
Connected to LiveKit room: call-91987654321-5829
Dialing via Vobiz SIP...
Call initiated, waiting for answer...
```

**On the phone:** Agent will greet the recipient and start conversation!

---

## 📊 Monitoring & Logs

### **Agent Logs (Terminal 2)**
```
INFO:outbound-agent:Loading config for phone: +919876543210
INFO:livekit.agents:Participant joined: Agent
INFO:outbound-agent:DIAL: Sending SIP INVITE...
INFO:outbound-agent:✓ Call answered
INFO:outbound-agent:STT: "Hello, who is this?"
INFO:outbound-agent:LLM: Processing...
INFO:outbound-agent:TTS: Generating response...
INFO:outbound-agent:TTS: "Hi! This is Aryan from RapidX AI..."
```

### **Dashboard Logs (http://localhost:8000)**
- Active calls
- API credentials status
- Config editor
- Voice/model selector

### **Database Logs (Supabase)**
- Call timestamps, durations, participants
- Transcripts (if enabled)
- Recordings (in S3 bucket)
- Booking intents

---

## ⚙️ Configuration Details

### **Per-Client Custom Config**

Create unique personality per phone number:

**File:** `configs/919876543210.json`

```json
{
  "agent_instructions": "You are Aryan, a sales consultant...",
  "llm_model": "gpt-4o-mini",
  "tts_voice": "kavya",
  "tts_language": "hi-IN",
  "lang_preset": "multilingual"
}
```

Agent automatically loads this instead of default config.json!

### **Key Configuration Options**

| Setting | Options | Purpose |
|---------|---------|---------|
| `llm_model` | gpt-4o-mini, gpt-4, gpt-3.5-turbo | Conversation intelligence |
| `tts_voice` | kavya, (others from Sarvam) | Speaking voice |
| `tts_language` | hi-IN, en-US, ta-IN, etc. | Voice language |
| `lang_preset` | multilingual, english, hindi | STT language detection |
| `stt_min_endpointing_delay` | 0.1-0.5 | How long to wait for silence before response |

---

## 🐛 Troubleshooting

### **Error: "LIVEKIT_URL not found"**
- Create `.env` file (copy `.env.example`)
- Fill in `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`

### **Error: "Vobiz SIP credentials invalid"**
- Verify `VOBIZ_USERNAME`, `VOBIZ_PASSWORD`, `VOBIZ_SIP_DOMAIN`
- Check if DID number is active in Vobiz Console
- Ensure IP whitelist allows your network

### **Agent starts but call fails**
- Check SIP trunk ID in `agent.py` matches LiveKit
- Run `setup_trunk.py` to recreate/update trunk
- Check call logs in Supabase for detailed errors

### **No audio response**
- Check `SARVAM_API_KEY` and quota
- Verify TTS voice exists (run test: `test_streaming_tts.py`)
- Check agent logs for "TTS error"

### **Deepgram errors (legacy)**
- Replace with Sarvam AI (already configured)
- Remove `DEEPGRAM_API_KEY` from .env

---

## 📁 Key Files Reference

| File | Purpose |
|------|---------|
| `agent.py` | Main AI worker — processes calls, manages LLM/STT/TTS |
| `make_call.py` | Dispatcher — triggers outbound calls |
| `ui_server.py` | FastAPI dashboard on port 8000 |
| `calendar_tools.py` | Cal.com booking integration |
| `db.py` | Supabase database helpers |
| `notify.py` | Telegram notifications |
| `setup_trunk.py` | Configure LiveKit SIP trunk with Vobiz |
| `config.json` | Default agent instructions & settings |

---

## 📞 Quick Command Reference

```powershell
# Activate environment
.venv\Scripts\activate

# Start dashboard
python ui_server.py

# Start agent
python agent.py dev

# Make a call
python make_call.py --to=+919876543210

# Test STT from audio file
python test_streaming_tts.py

# Setup SIP trunk
python setup_trunk.py
```

---

## ✅ Verification Checklist

- [x] Python 3.9+ installed
- [x] Virtual environment created (`.venv/`)
- [x] Dependencies installed via `pip install -r requirements.txt`
- [ ] `.env` file created with API keys
- [ ] LiveKit SIP trunk configured
- [ ] Agent starts without errors (`python agent.py dev`)
- [ ] Dashboard accessible on http://localhost:8000
- [ ] Test call successful (real phone rings)

---

## 🎓 Next Steps

1. **Get API Keys** — Sign up for services listed above
2. **Fill `.env`** — Add all credentials
3. **Test Locally** — Run `agent.py dev` and `make_call.py`
4. **Deploy** — See `COOLIFY_DEPLOYMENT.md` or `VERCEL_DEPLOYMENT.md`
5. **Customize** — Edit `config.json` for your use case

---

## 📚 Additional Documentation

- `README.md` — Project overview
- `LOCAL_STARTUP_GUIDE.md` — Step-by-step local setup
- `SUPABASE_SETUP.md` — Database configuration
- `COOLIFY_DEPLOYMENT.md` — Production deployment
- `transfer_call.md` — Call transfer to humans
- `SOP.md` — Best practices & error solutions

---

**Ready to make AI calls? Start with Phase 1 above! 🚀**
