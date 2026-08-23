import os

from dotenv import load_dotenv

load_dotenv()

MUAPI_API_KEY = os.getenv("MUAPI_API_KEY", "").strip()
MUAPI_BASE_URL = os.getenv("MUAPI_BASE_URL", "https://api.muapi.ai/api/v1").rstrip("/")

POLL_INTERVAL_SECONDS = float(os.getenv("MUAPI_POLL_INTERVAL", "5"))
POLL_TIMEOUT_SECONDS = float(os.getenv("MUAPI_POLL_TIMEOUT", "600"))

# Local-mode (--mode local) settings — only consulted when running offline.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen3:14b")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1").strip()
# Cloud provider (free tier via opencode.ai) — ADDED alongside local, not replacing it.
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "").strip()
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "deepseek-v4-flash-free")
OPENCODE_BASE_URL = os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/v1").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
LOCAL_WHISPER_MODEL = os.getenv("LOCAL_WHISPER_MODEL", "small")
LOCAL_WHISPER_DEVICE = os.getenv("LOCAL_WHISPER_DEVICE", "cpu")  # auto / cpu / cuda
LOCAL_OUTPUT_DIR = os.getenv("LOCAL_OUTPUT_DIR", "output")
OLLAMA_REQUEST_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_REQUEST_TIMEOUT_SECONDS", "900"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "2048"))
RANKING_MAX_CANDIDATES_PER_CALL = int(os.getenv("RANKING_MAX_CANDIDATES_PER_CALL", "8"))

# --- Chunked ranking for long videos (fixes flat 7-9 scores) ---
# The hardcoded 30-min threshold in highlights.py was too high for local models:
# a 10-min interview went into ONE giant prompt and produced flat scores.
HL_CHUNK_SIZE_SECONDS = float(os.getenv("HL_CHUNK_SIZE_SECONDS", "300"))
HL_LONG_VIDEO_THRESHOLD = float(os.getenv("HL_LONG_VIDEO_THRESHOLD", "420"))
HL_CHUNK_OVERLAP_SECONDS = float(os.getenv("HL_CHUNK_OVERLAP_SECONDS", "60"))

# --- Karaoke captions (word-by-word pop, Hormozi-style) ---
# When true, build_ass renders {\k}-timed word highlights (yellow fill sweeps
# across each word as it is spoken) instead of static whole-line captions.
KARAOKE = os.getenv("KARAOKE", "true").strip().lower() == "true"

# --- Professional-output knobs (all optional, safe defaults) ---
# 1. Clip length limits: cap 120s (user: "not more than 2min" — long enough for a
#    complete thought/conversation, so cuts land on natural pauses, not caps),
#    floor 8s (reached by pulling START back, never breaking the ending).
SHORTS_MAX_SECONDS = float(os.getenv("SHORTS_MAX_SECONDS", "120"))
SHORTS_MIN_SECONDS = float(os.getenv("SHORTS_MIN_SECONDS", "8"))
# Candidate construction happens before ranking; candidates are complete units,
# not arbitrary timestamp windows.
COHERENCE_MIN_SECONDS = float(os.getenv("COHERENCE_MIN_SECONDS", "12"))
COHERENCE_TARGET_SECONDS = float(os.getenv("COHERENCE_TARGET_SECONDS", "45"))
COHERENCE_MAX_SECONDS = float(os.getenv("COHERENCE_MAX_SECONDS", "120"))
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
# Options: off | haar (OpenCV cascade) | mediapipe (BlazeFace, smoother pan)
FACE_TRACK = os.getenv("FACE_TRACK", "off").strip().lower()
FACE_CENTER_Y = float(os.getenv("FACE_CENTER_Y", "0.42"))
# 5. Loudness normalization + locked frame rate on every published clip
LOUDNESS_FILTER = os.getenv("LOUDNESS_FILTER", "loudnorm=I=-14:TP=-1.5:LRA=11")
OUTPUT_FPS = int(os.getenv("OUTPUT_FPS", "30"))
# 6. Source download: prefer high-res when available (falls back automatically)
DOWNLOAD_FORMAT = os.getenv("DOWNLOAD_FORMAT", "1080")
# 7. Caption styling: keyword emphasis; SUBTITLE_LANGUAGE forces a script (e.g. "en")
KEYWORD_EMPHASIS = os.getenv("KEYWORD_EMPHASIS", "true").strip().lower() == "true"
SUBTITLE_LANGUAGE = os.getenv("SUBTITLE_LANGUAGE", "").strip() or None

# --- Topic-shift segmentation (fixes merged clips) ---
# SEGMENTATION_SERVICE: "off" (default) | "semantic" | "auto"
#   semantic = nomic-embed-text similarity dips (local Ollama, free)
#   auto     = semantic + pause boundaries
# TOPIC_SIM_SIGMAS: boundary threshold in std-devs below mean similarity
# PAUSE_BOUNDARY_SECONDS: any real silence >= this is a hard clip boundary
SEGMENTATION_SERVICE = os.getenv("SEGMENTATION_SERVICE", "auto").strip().lower()
TOPIC_SIM_SIGMAS = float(os.getenv("TOPIC_SIM_SIGMAS", "0.5"))
PAUSE_BOUNDARY_SECONDS = float(os.getenv("PAUSE_BOUNDARY_SECONDS", "1.2"))
# Split pieces shorter than this (default 4s) merge back into the neighbour;
# the 8s SHORTS_MIN_SECONDS quality floor stays for FULL clips.
SEGMENT_MIN_SECONDS = float(os.getenv("SEGMENT_MIN_SECONDS", "4"))
# No two boundaries may sit closer than this (seconds). Turn-taking in an
# interview fires pauses constantly — cluster them and keep the strongest.
BOUNDARY_MIN_GAP_SECONDS = float(os.getenv("BOUNDARY_MIN_GAP_SECONDS", "8"))

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
