import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_FIRST_LINE = "Hello! This is Aakriti Hospitals. How can I help you?"

DEFAULT_AGENT_INSTRUCTIONS = """You are a friendly and professional dental clinic receptionist.

Your goal is to:
- Greet callers politely
- Understand their needs
- Help with appointment booking or queries
- Collect required details
- End the call professionally

Tone:
- Warm, polite, and calm
- Professional but conversational
- Keep responses short and clear

Conversation Flow:

1. Greeting:
"Hello! Thank you for calling our dental clinic. How can I assist you today?"

2. Understand Need:
Ask what the caller needs (appointment, information, emergency)

3. Appointment Booking:
Ask for:
- Name
- Phone number
- Preferred date and time
- Issue (optional)

4. Confirmation:
Repeat details and confirm booking

5. Closing:
"Thank you for calling. Have a great day!"

Rules:
- Do NOT give medical advice
- Do NOT assume missing details
- If unsure, ask politely
- Keep responses concise

Behavior:
- If user is silent → ask again politely
- If user is confused → simplify questions
- If user is upset → respond calmly

Language:
- Speak English by default
- Switch to the user's language. Supported languages: Hindi, Hinglish, English, Tamil, Telugu, Gujarati, Bengali, Marathi, Kannada, Malayalam

Fallback:
- If unclear: "Sorry, could you please repeat that?"""

def get_config():
    def g(k, default=""): 
        return os.getenv(k, default)

    return {
        "first_line": g("FIRST_LINE", DEFAULT_FIRST_LINE),
        "agent_instructions": g("AGENT_INSTRUCTIONS", DEFAULT_AGENT_INSTRUCTIONS),
        "stt_min_endpointing_delay": float(g("STT_MIN_ENDPOINTING_DELAY", 0.08)),
        "llm_provider": g("LLM_PROVIDER", "gemini"),
        "gemini_model": g("GEMINI_MODEL", "gemini-2.5-flash"),
        "groq_model": g("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        "llm_max_completion_tokens": int(g("LLM_MAX_COMPLETION_TOKENS", 80)),
        "tts_voice": g("TTS_VOICE", "kavya"),
        "tts_language": g("TTS_LANGUAGE", "en-IN"),
        "lang_preset": g("LANG_PRESET", "multilingual"),
        "max_turns": int(g("MAX_TURNS", 30)),
        "livekit_url": g("LIVEKIT_URL", ""),
        "livekit_api_key": g("LIVEKIT_API_KEY", ""),
        "livekit_api_secret": g("LIVEKIT_API_SECRET", ""),
        "gemini_api_key": g("GEMINI_API_KEY", ""),
        "groq_api_key": g("GROQ_API_KEY", ""),
        "sarvam_api_key": g("SARVAM_API_KEY", ""),
        "cal_api_key": g("CAL_API_KEY", ""),
        "cal_event_type_id": g("CAL_EVENT_TYPE_ID", ""),
        "sip_trunk_id": g("SIP_TRUNK_ID", ""),
        "vobiz_sip_domain": g("VOBIZ_SIP_DOMAIN", ""),
        "supabase_url": g("SUPABASE_URL", ""),
        "supabase_key": g("SUPABASE_KEY", ""),
        "supabase_s3_access_key": g("SUPABASE_S3_ACCESS_KEY", ""),
        "supabase_s3_secret_key": g("SUPABASE_S3_SECRET_KEY", ""),
        "supabase_s3_endpoint": g("SUPABASE_S3_ENDPOINT", ""),
        "supabase_s3_region": g("SUPABASE_S3_REGION", "ap-south-1")
    }
