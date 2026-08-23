"""Local LLM backend — OpenAI, Gemini, or local Ollama, selected by LLM_PROVIDER."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..config import (
    GEMINI_MODEL,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OLLAMA_NUM_CTX,
    OLLAMA_NUM_PREDICT,
    OLLAMA_REQUEST_TIMEOUT_SECONDS,
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
    """Call Ollama's native chat API with reliable JSON and empty-output diagnostics.

    The native endpoint is more stable than the OpenAI-compat response-format path
    on local qwen3 installations. ``format='json'`` requires valid JSON and
    ``think=False`` prevents reasoning text from contaminating the result.
    """
    parsed = urlparse(OPENAI_BASE_URL or "http://localhost:11434/v1")
    api_url = f"{parsed.scheme or 'http'}://{parsed.netloc or 'localhost:11434'}/api/chat"
    payload = {
        "model": OPENAI_MODEL,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_predict": OLLAMA_NUM_PREDICT, "num_ctx": OLLAMA_NUM_CTX},
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=OLLAMA_REQUEST_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Ollama request failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach local Ollama at {api_url}: {exc.reason}") from exc

    message = body.get("message") if isinstance(body, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        done_reason = body.get("done_reason") if isinstance(body, dict) else None
        raise RuntimeError(
            "Ollama returned no ranking content "
            f"(model={OPENAI_MODEL}, done_reason={done_reason!r})."
        )
    return content.strip()


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
    provider = (LLM_PROVIDER or "ollama").strip().lower()
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
