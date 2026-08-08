"""Local LLM backend — OpenAI, Gemini, or local Ollama, selected by LLM_PROVIDER."""
from ..config import (
    GEMINI_MODEL,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENCODE_API_KEY,
    OPENCODE_BASE_URL,
    OPENCODE_MODEL,
    require_gemini_key,
    require_openai_key,
)


def call_openai_llm(prompt: str) -> str:
    """OpenAI Chat Completions backend used by --mode local."""
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "openai is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    client = OpenAI(api_key=require_openai_key())
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.7,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def call_gemini_llm(prompt: str) -> str:
    """Gemini backend used by --mode local when LLM_PROVIDER=gemini."""
    try:
        from google import genai  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "google-genai is required for LLM_PROVIDER=gemini. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    client = genai.Client(api_key=require_gemini_key())
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "temperature": 0.2,
            "response_mime_type": "application/json",
            "max_output_tokens": 8192,
        },
    )
    return response.text or ""


def call_ollama_llm(prompt: str) -> str:
    """Ollama backend — OpenAI-compatible /v1 endpoint, free + local.

    Verified against Ollama's OpenAI-compat API (docs.ollama.com/api/openai-compatibility):
    it accepts any api_key and ignores it. `think:false` disables qwen3 reasoning blocks
    so the JSON parsed in highlights.py stays clean; _parse_json_loose catches leftovers.
    """
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "openai is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    client = OpenAI(
        api_key=OPENAI_API_KEY or "ollama",
        base_url=OPENAI_BASE_URL or "http://localhost:11434/v1",
    )
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
        extra_body={"think": False},
        # Ollama OpenAI-compat: force the model into schema-free JSON mode.
        # Without this, qwen3 drifts into bare arrays / prose on long prompts.
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def call_opencode_llm(prompt: str) -> str:
    """Cloud backend via opencode.ai — free-tier DeepSeek V4 Flash.

    OpenAI-compatible endpoint (https://opencode.ai/zen/v1), same client shape
    as the local path. Free model is 'deepseek-v4-flash-free'; key in
    OPENCODE_API_KEY. Local providers are untouched — this is purely additive.
    """
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "openai is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    client = OpenAI(api_key=OPENCODE_API_KEY, base_url=OPENCODE_BASE_URL)
    response = client.chat.completions.create(
        model=OPENCODE_MODEL,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def call_local_llm(prompt: str) -> str:
    """Dispatch to the configured local LLM provider."""
    provider = (LLM_PROVIDER or "openai").strip().lower()
    if provider == "openai":
        return call_openai_llm(prompt)
    if provider == "gemini":
        return call_gemini_llm(prompt)
    if provider == "ollama":
        return call_ollama_llm(prompt)
    if provider == "opencode":
        return call_opencode_llm(prompt)
    raise RuntimeError(
        f"Unknown LLM_PROVIDER={provider!r}. Use 'openai', 'gemini', 'ollama' or 'opencode'."
    )
