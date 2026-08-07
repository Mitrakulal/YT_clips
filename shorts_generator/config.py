import os

from dotenv import load_dotenv

load_dotenv()

MUAPI_API_KEY = os.getenv("MUAPI_API_KEY", "").strip()
MUAPI_BASE_URL = os.getenv("MUAPI_BASE_URL", "https://api.muapi.ai/api/v1").rstrip("/")

POLL_INTERVAL_SECONDS = float(os.getenv("MUAPI_POLL_INTERVAL", "5"))
POLL_TIMEOUT_SECONDS = float(os.getenv("MUAPI_POLL_TIMEOUT", "600"))

# Local-mode (--mode local) settings — only consulted when running offline.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()
LOCAL_WHISPER_MODEL = os.getenv("LOCAL_WHISPER_MODEL", "base")
LOCAL_WHISPER_DEVICE = os.getenv("LOCAL_WHISPER_DEVICE", "auto")  # auto / cpu / cuda
LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "output")

# --- Professional-output knobs (all optional, safe defaults) ---
# 1. Clip length limits: cap 120s (user: "not more than 2min" — long enough for a
#    complete thought/conversation, so cuts land on natural pauses, not caps),
#    floor 8s (reached by pulling START back, never breaking the ending).
SHORTS_MAX_SECONDS = float(os.getenv("SHORTS_MAX_SECONDS", "120"))
SHORTS_MIN_SECONDS = float(os.getenv("SHORTS_MIN_SECONDS", "8"))
# 2. Sentence-boundary trimming is ALWAYS on (aligns cuts to word boundaries)
# 3. Hook text overlay: big bold title burned into the first seconds of each clip
HOOK_TEXT = os.getenv("HOOK_TEXT", "true").strip().lower() == "true"
HOOK_SECONDS = float(os.getenv("HOOK_SECONDS", "3"))
HOOK_FONT_SIZE = int(os.getenv("HOOK_FONT_SIZE", "72"))
# 4. Dynamic zoom (Ken Burns slow push-in, applied per-frame in the reframer)
DYNAMIC_ZOOM = os.getenv("DYNAMIC_ZOOM", "true").strip().lower() == "true"
ZOOM_MAX = float(os.getenv("ZOOM_MAX", "0.06"))  # 6% push-in across the clip
# Face tracking (default OFF for stability). When off, the crop stays anchored
# to a fixed upper-center point so framing never jumps between speakers.
FACE_TRACK = os.getenv("FACE_TRACK", "false").strip().lower() == "true"
FACE_CENTER_Y = float(os.getenv("FACE_CENTER_Y", "0.42"))
# 5. Loudness normalization + locked frame rate on every published clip
LOUDNESS_FILTER = os.getenv("LOUDNESS_FILTER", "loudnorm=I=-14:TP=-1.5:LRA=11")
OUTPUT_FPS = int(os.getenv("OUTPUT_FPS", "30"))
# 6. Source download: prefer high-res when available (falls back automatically)
DOWNLOAD_FORMAT = os.getenv("DOWNLOAD_FORMAT", "1080")
# 7. Caption styling: keyword emphasis; SUBTITLE_LANGUAGE forces a script (e.g. "en")
KEYWORD_EMPHASIS = os.getenv("KEYWORD_EMPHASIS", "true").strip().lower() == "true"
SUBTITLE_LANGUAGE = os.getenv("SUBTITLE_LANGUAGE", "").strip() or None

# VAD (Voice Activity Detection) settings for faster-whisper
# Default threshold is 0.5; lower = more sensitive, higher = less sensitive
# Default min_speech_duration_ms is 250ms; increase to avoid tiny false positives
# Default min_silence_duration_ms is 2000ms; increase to avoid splitting mid-sentence
# DISABLED by default because VAD is too aggressive on mixed speech/music content
LOCAL_WHISPER_VAD_FILTER = os.getenv("LOCAL_WHISPER_VAD_FILTER", "false").strip().lower() == "true"
_vad_params_env = os.getenv("LOCAL_WHISPER_VAD_PARAMETERS", "")
if _vad_params_env:
    import json
    LOCAL_WHISPER_VAD_PARAMETERS = json.loads(_vad_params_env)
else:
    # Match faster-whisper defaults when VAD is enabled
    LOCAL_WHISPER_VAD_PARAMETERS = {
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
        "max_speech_duration_s": float("inf"),
        "min_silence_duration_ms": 2000,
        "speech_pad_ms": 400,
    }


def require_api_key() -> str:
    if not MUAPI_API_KEY:
        raise RuntimeError(
            "MUAPI_API_KEY is not set. Add it to your .env file or export it as an env var."
        )
    return MUAPI_API_KEY


def require_openai_key() -> str:
    # Ollama (and any local OpenAI-compatible server) ignores the API key entirely,
    # so do not demand a real secret when pointing at localhost.
    if OPENAI_BASE_URL and ("localhost" in OPENAI_BASE_URL or "127.0.0.1" in OPENAI_BASE_URL):
        return OPENAI_API_KEY or "ollama"
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Local mode needs an OpenAI key for highlight ranking. "
            "Add it to your .env or export it, or switch back to --mode api."
        )
    return OPENAI_API_KEY


def require_gemini_key() -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Local mode needs a Gemini key when LLM_PROVIDER=gemini. "
            "Add it to your .env or export it, or switch LLM_PROVIDER back to openai."
        )
    return GEMINI_API_KEY
