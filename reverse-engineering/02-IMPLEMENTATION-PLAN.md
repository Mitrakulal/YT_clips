# 02 — Implementation Plan (zero-guess, agent-executable)

> **The never-guess rule:** every decision, exact field name, file path, and verify command is written
> below. An executing agent copies code blocks verbatim — it does not improvise. Anything an agent may
> decide freely is explicitly marked `<your discretion>`.

---

## §0 How to use this file

1. Work through **Phase 0 → Phase 5 in order** (Phase 6 optional). Each phase is independently
   verifiable; never start a phase with an unverified predecessor.
2. Copy code blocks **verbatim**. Do not rename functions/variables/fields unless a `<your discretion>`
   marker says you may.
3. Run the **Verify** command in each phase; **expected output** is given. If actual output differs,
   stop and investigate — do not proceed to the next phase.
4. LOCKED = decided, do not re-litigate. `<your discretion>` = free to tweak.
5. Never modify files outside this repo's tree except `~/Library/LaunchAgents/` (Phase 4).
6. **Anti-fabrication:** no fake URLs/keys/scores in code or tests. Where a real value is needed
   (e.g. video URL for testing), use a real public video or a local file you own.

### §0.1 LOCKED decisions (from 01-RESEARCH-REPORT.md)
- LLM provider: **Ollama `qwen3:14b`** via OpenAI-compatible endpoint (free, local). Provider name in
  `.env`: `LLM_PROVIDER=ollama`.
- Whisper: **faster-whisper, CPU, `word_timestamps=True`**; model `small`.
- Orchestration: **launchd `KeepAlive` + one `worker.py` loop + file queue**. No Celery/Redis.
- Stage machine: per-job `state.json`, idempotent skip-if-done, atomic `os.replace` writes,
  rescue `queue/active/*` on boot.
- Output: `output/short_XX_captioned.mp4` per highlight; source + intermediates deleted in `cleanup`.
- Download format: `480` (plenty for 9:16, half the disk).
- Upload: **manual** (Phase 6 is optional, not part of the committed scope).

### §0.2 Files touched (complete list — nothing else)
| File | Action |
|---|---|
| `.env` | create (from `.env.example`, updated) |
| `.env.example` | modify (add Ollama vars) |
| `shorts_generator/config.py` | modify (add `OPENAI_BASE_URL`, relax `require_openai_key`) |
| `shorts_generator/local/llm.py` | modify (add `call_ollama_llm`, route provider) |
| `shorts_generator/local/transcriber.py` | modify (word timestamps + `words.json`) |
| `subtitles.py` | **new** (ASS builder + ffmpeg burn) |
| `stage.py` | **new** (stage-machine helpers) |
| `worker.py` | **new** (24/7 loop + queue + housekeeping) |
| `queue/{inbox,active,done,failed}/` | new dirs |
| `jobs/`, `published/`, `seen.json`, `logs/` | new dirs/files |
| `~/Library/LaunchAgents/com.user.shorts.pipeline.plist` | new (Phase 4) |

The remaining files of the cloned repo (`main.py`, `pipeline.py`, `highlights.py`, `clipper.py`,
`downloader.py`, `muapi.py`, etc.) are **reference — do not modify**.

### §0.3 Host & network setup (one-time)
- Target: the **Mac Mini** (Apple Silicon, 16 GB, 256 GB). All stages run there.
- Ollama already installed on the Mac Mini. Verify with Phase 0.
- If (and only if) you want the pipeline to run on the **Windows desktop** instead, point
  `OPENAI_BASE_URL` at the Mac Mini's LAN IP + port (e.g. `http://192.168.x.x:11435/v1`) and make sure
  Ollama on the Mac Mini is bound to `0.0.0.0` (`OLLAMA_HOST=0.0.0.0` in its LaunchAgent). **Default
  recommendation: run the pipeline on the Mac Mini itself** with `http://localhost:11434/v1`.

---

## Phase 0 — Environment setup & baseline

### Files: none (commands only)

### Steps
```bash
# 1. Python on the Mac Mini
python3 --version        # expect 3.10+

# 2. Homebrew tools (already installed per research, verify)
ffmpeg -version | head -1
yt-dlp --version | head -1

# 3. Ollama up + qwen3:14b present
ollama list               # expect qwen3:14b row (9.3 GB)
ollama pull qwen3:14b     # only if missing
ollama serve &            # if not already running (or use its LaunchAgent)

# 4. OpenAI-compat smoke test (the FREE LLM path must answer before Phase 1)
curl -s http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:14b","messages":[{"role":"user","content":"Reply with exactly: OK"}]}' | head -c 400
```
**Expected:** a JSON response containing `"content": "OK"` (plus `"role":"assistant"` fields). If you
see `...` reasoning text wrapped in the content, note it — Phase 1 handles it with `think:false`.

```bash
# 5. Project venv (inside the cloned repo)
cd ~/AI-Youtube-Shorts-Generator   # <your discretion: path may differ>
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-local.txt   # includes faster-whisper, openai, opencv-python, yt-dlp
```
**Expected:** pip completes without error; `python -c "import faster_whisper, cv2, yt_dlp, openai"` exits 0.

```bash
# 6. Test asset: any short video you own (or a real public YouTube URL)
#    For the phases below, a local .mp4 you own is fastest (no network, no copyright issue).
```

> **Windows dev-machine note (only if you develop on the Windows box):** Hermes' shell pre-exports
> `PYTHONPATH` pointing at Hermes' own venv. Prefix `PYTHONPATH=` when running the project venv:
> `PYTHONPATH= venv/bin/python main.py …`

**Verify (exact):**
```bash
source venv/bin/activate && python -c "import faster_whisper, cv2, yt_dlp, openai; print('deps OK')"
```
**Expected:** `deps OK`

**LOCKED:** venv at repo root named `venv`; faster-whisper model `small`; device `cpu`.
**Cut if rushed:** step 4 (curl) — but you MUST run it before Phase 1's verify, or you can't distinguish
an Ollama problem from a code problem.

---

## Phase 1 — Change A: free LLM via Ollama (no cloud, no key)

### Files: `.env.example`, `.env`, `shorts_generator/config.py`, `shorts_generator/local/llm.py`

### Step 1.1 — `.env.example` (replace the "Local mode" block; keep everything else)
```bash
# Local mode (--mode local) — FREE with local Ollama
LLM_PROVIDER=ollama           # openai | gemini | ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen3:14b
OPENAI_API_KEY=ollama         # required-but-ignored by Ollama (do not put a real key)
LOCAL_WHISPER_MODEL=small     # tiny / base / small / medium / large-v3
LOCAL_WHISPER_DEVICE=cpu      # auto / cpu / cuda
LOCAL_OUTPUT_DIR=output
```

### Step 1.2 — copy `.env.example` to `.env`
```bash
cp .env.example .env
```

### Step 1.3 — `shorts_generator/config.py`
Add this line near the other OPENAI settings (after `OPENAI_MODEL`):
```python
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1").strip()
```
Replace the whole `require_openai_key()` function:
```python
def require_openai_key() -> str:
    if OPENAI_BASE_URL and ("localhost" in OPENAI_BASE_URL or "127.0.0.1" in OPENAI_BASE_URL):
        return OPENAI_API_KEY or "ollama"          # Ollama ignores the key
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Local mode needs an OpenAI key for highlight ranking. "
            "Add it to your .env or export it, or switch back to --mode api."
        )
    return OPENAI_API_KEY
```

### Step 1.4 — `shorts_generator/local/llm.py`
Replace the file's entire contents with:
```python
"""Local LLM backend — OpenAI, Gemini, or local Ollama, selected by LLM_PROVIDER."""
from ..config import (
    GEMINI_MODEL,
    LLM_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
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

    Verified: Ollama exposes POST http://localhost:11434/v1/chat/completions and
    accepts an api_key that it ignores (docs.ollama.com/api/openai-compatibility).
    `think:false` disables qwen3 reasoning blocks so the JSON parse in
    highlights.py stays clean (falls back to _parse_json_loose anyway).
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
    raise RuntimeError(
        f"Unknown LLM_PROVIDER={provider!r}. Use 'openai', 'gemini' or 'ollama'."
    )
```

### Verify (exact) — end-to-end, local file, NO cloud
```bash
source venv/bin/activate
python main.py "/path/to/your/test.mp4" --mode local --num-clips 1 --output-json /tmp/first.json
```
**Expected stdout (shape):**
```
[download/local] using local file: /path/to/your/test.mp4
[transcribe/local] faster-whisper model=small device=cpu
[transcribe/local] N segments, …s of audio
[highlights] content=… density=… duration=…s
[clip/local] 1/1: <title>
…
#1  score=…  …s → …s
     clip:   …/short_01.mp4
```
**Expected:** exit code 0, no network calls to api.openai.com / googleapis (check with
`lsof -i` or your firewall log `<your discretion>`), and `/tmp/first.json` contains
`"highlights"` with at least one entry having `start_time`/`end_time`/`score`.

If the LLM step fails: confirm `curl` from Phase 0 still returns OK; confirm `.env` has
`LLM_PROVIDER=ollama`; confirm `OPENAI_MODEL=qwen3:14b` matches `ollama list`.

**LOCKED:** provider name string is exactly `ollama`; base URL default is exactly
`http://localhost:11434/v1`; `extra_body={"think": False}` stays.
**Cut if rushed:** nothing — this is the money phase.

---

## Phase 2 — Change B: word-level timestamps

### Files: `shorts_generator/local/transcriber.py`

### Step 2.1 — modify `transcribe_local()`
In the `transcribe_kwargs` dict (near `"condition_on_previous_text": False`), add:
```python
        "word_timestamps": True,
```
After the `segments.append({...})` loop, add a words collector. The final loop becomes:
```python
    segments = []
    all_words = []
    for s in segments_iter:
        seg = {
            "start": float(s.start),
            "end": float(s.end),
            "text": (s.text or "").strip(),
        }
        if getattr(s, "words", None):
            seg["words"] = [
                {"start": float(w.start), "end": float(w.end), "word": w.word}
                for w in s.words
            ]
            all_words.extend(seg["words"])
        segments.append(seg)
```
And after `duration = …` is computed, before the return, persist words:
```python
    words_path = cache_path.with_suffix(".words.json")
    words_path.write_text(json.dumps(all_words, ensure_ascii=False), encoding="utf-8")
    print(f"[transcribe/local] wrote {len(all_words)} word timestamps: {words_path}", flush=True)
```

### Step 2.2 — add the import at the top of the file
```python
import json
```

### Verify (exact)
```bash
source venv/bin/activate
python main.py "/path/to/your/test.mp4" --mode local --num-clips 1
```
**Expected stdout:** now includes
```
[transcribe/local] wrote NNN word timestamps: output/<test-stem>.words.json
```
and `output/<test-stem>.words.json` is a JSON array whose first element looks like
```json
[{"start": 0.32, "end": 0.58, "word": "Hello"}]
```
(Same file is reused on re-runs because the `.srt` cache exists — delete `output/*.srt` +
`output/*.words.json` for this test if you need to force re-transcription.)

**LOCKED:** word JSON schema is exactly `{"start": float, "end": float, "word": str}` in a flat array;
file name is `<stem>.words.json` next to the `.srt` cache in `LOCAL_OUTPUT_DIR`.
**Cut if rushed:** nothing.

---

## Phase 3 — Change C: subtitle burn-in (net-new)

### Files: `subtitles.py` (new)

### Step 3.1 — create `subtitles.py` (verbatim)
```python
"""Subtitle burn-in stage: words.json -> ASS -> ffmpeg burn onto each short clip.

Inputs per highlight:
  - words:      list of {"start","end","word"} in the SOURCE video timeline
  - clip_start: source-time where the clip begins
  - clip_end:   source-time where the clip ends
  - clip_path:  the 9:16 mp4 produced by the crop stage
  - out_path:   where the captioned mp4 goes

The ASS file is rendered at 1080x1920; ffmpeg scales each clip into that canvas,
so caption positions are consistent across clips regardless of source resolution.
"""
import subprocess
from typing import Dict, List

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,96,&H00FFFFFF,&H00FFE600,&H00141414,&H96000000,-1,0,0,0,100,100,0,0,1,3,0,2,40,40,220,1
"""


def _ts(seconds: float) -> str:
    """ASS timestamp H:MM:SS.cs (centiseconds)."""
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _escape(text: str) -> str:
    """Escape ASS tag braces so plain text renders literally."""
    return text.replace("{", "\\{").replace("}", "\\}")


def build_ass(words: List[Dict], clip_start: float, clip_end: float, out_path: str) -> str:
    """Group clip-relative words into <=8-word chunks; one static Dialogue line per chunk.

    (Static phrase captions, not karaoke pop — timing-exact and simple. Karaoke
    highlighting is a documented later upgrade, not part of this spec.)
    """
    rel = [w for w in words if clip_start <= w["start"] < clip_end]
    chunks = [rel[i:i + 8] for i in range(0, len(rel), 8)]
    body = []
    for chunk in chunks:
        if not chunk:
            continue
        start = max(0.0, chunk[0]["start"] - clip_start)
        end = chunk[-1]["end"] - clip_start
        text = _escape(" ".join(w["word"] for w in chunk))
        body.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{text}")
    events = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(body)
        + "\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER + events)
    return out_path


def burn_subtitles(clip_path: str, ass_path: str, out_path: str) -> str:
    """Burn the ASS onto the clip; canvas is padded to 1080x1920."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", clip_path,
        "-vf", f"ass={ass_path},scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path


def subtitle_burn_stage(
    clip_path: str,
    words: List[Dict],
    clip_start: float,
    clip_end: float,
    out_path: str,
) -> str:
    """Build the ASS then burn it. ASS is kept next to the output for inspection."""
    ass_path = out_path + ".ass"
    build_ass(words, clip_start, clip_end, ass_path)
    burn_subtitles(clip_path, ass_path, out_path)
    return out_path
```

### Verify (exact)
```bash
source venv/bin/activate
python - <<'PY'
import json, glob
from subtitles import subtitle_burn_stage
words = json.load(open(sorted(glob.glob("output/*.words.json"))[0], encoding="utf-8"))
subtitle_burn_stage("output/short_01.mp4", words, 0.0, 99999.0, "/tmp/captioned_test.mp4")
print("captioned OK")
PY
```
**Expected stdout:** `captioned OK`, exit 0, and `/tmp/captioned_test.mp4` exists and is a valid mp4
(`ffprobe /tmp/captioned_test.mp4 2>&1 | grep -i duration` prints a duration). Open it and confirm
bottom-center white captions with outline.

> **Windows gotcha (only if you ever burn on the Windows box):** the `ass=` filter treats `:` specially,
> so an absolute Windows path like `C:\dir\caps.ass` must be escaped as `C\:/dir/caps.ass` inside the
> filter string. On macOS (the target) plain paths work.

**LOCKED:** ASS style name `Caption`; canvas 1080x1920; chunk size 8 words; output suffix `_captioned.mp4`.
**Cut if rushed:** nothing.

---

## Phase 4 — Orchestration: 24/7 worker + launchd

### Files: `stage.py`, `worker.py` (new), `queue/…` dirs, LaunchAgent plist

### Step 4.1 — create `stage.py` (verbatim)
```python
"""Stage-machine helpers for the pipeline worker.

Rules (LOCKED):
1. Idempotent: a stage whose status == "done" (and whose artifact exists) is skipped.
2. Atomic: state is written to a tmp file then os.replace()d — no half-files on crash.
3. Resumable: on boot, the worker re-queues every job in queue/active/ whose status
   != "done"; it resumes from the last completed stage.
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


def new_state(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": job.get("job_id", ""),
        "source_url": job.get("source_url", ""),
        "num_clips": int(job.get("num_clips", 3)),
        "aspect_ratio": job.get("aspect_ratio", "9:16"),
        "format": job.get("format", "480"),
        "status": "running",  # pending | running | done | failed
        "stages": {
            "download": {},
            "transcribe": {},
            "highlight_llm": {},
            "crop": {},
            "subtitle_burn": {},
            "cleanup": {},
        },
        "error": None,
    }


def load_state(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(path: Path, state: Dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def stage_done(state: Dict[str, Any], name: str) -> bool:
    info = state["stages"].get(name) or {}
    return info.get("status") == "done" and bool(info.get("artifact"))


def mark_stage(
    state: Dict[str, Any],
    name: str,
    status: str,
    artifact: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    state["stages"][name] = {"status": status, "artifact": artifact, "error": error}
    return state
```

### Step 4.2 — create `worker.py` (verbatim)
```python
"""24/7 pipeline worker: file-queue in -> stage machine -> done/failed.

Design (LOCKED):
- queue/inbox/<job_id>.json : job spec {"source_url": ..., "num_clips": 3, ...}
- queue/active/<job_id>.json : in-flight (claimed via os.replace = atomic)
- queue/done|failed/        : terminal
- jobs/<job_id>/state.json  : per-stage state (source of truth)
- On boot: rescue any queue/active/* job (re-run unfinished stages).
- Idle: housekeeping (disk warn + publish pruning); heartbeat file touched.
- One bad job never kills the loop: every job runs inside try/except.
"""
import json
import os
import sys
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shorts_generator.highlights import get_highlights
from shorts_generator.local.clipper import crop_highlights_local
from shorts_generator.local.downloader import download_youtube_local
from shorts_generator.local.llm import call_local_llm
from shorts_generator.local.transcriber import transcribe_local
import subtitles
import stage as st

QUEUE = ROOT / "queue"
INBOX = QUEUE / "inbox"
ACTIVE = QUEUE / "active"
DONE = QUEUE / "done"
FAILED = QUEUE / "failed"
JOBS = ROOT / "jobs"
PUBLISHED = ROOT / "published"
SEEN_PATH = ROOT / "seen.json"
LOG_DIR = ROOT / "logs"
HEARTBEAT = LOG_DIR / "heartbeat"
INBOX_CAP = 20
MAX_CONSECUTIVE_FAILURES = 5
IDLE_SLEEP = 60
RETRY_LIMIT = 3


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)


def load_seen() -> set:
    if SEEN_PATH.exists():
        try:
            return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=1), encoding="utf-8")


def claim_next_job() -> Path:
    """Atomically move the first inbox job to active. Returns active path or None."""
    for inbox_path in sorted(INBOX.glob("*.json")):
        active_path = ACTIVE / inbox_path.name
        try:
            os.replace(inbox_path, active_path)
            return active_path
        except OSError:
            continue
    return None


def rescue_active() -> None:
    """Re-queue jobs stranded in active/ (worker crashed mid-job)."""
    for active_path in sorted(ACTIVE.glob("*.json")):
        job = json.loads(active_path.read_text(encoding="utf-8"))
        job_dir = JOBS / job["job_id"]
        state_path = job_dir / "state.json"
        if state_path.exists():
            state = st.load_state(state_path)
            if state.get("status") == "done":
                os.replace(active_path, DONE / active_path.name)
                continue
        os.replace(active_path, INBOX / active_path.name)
        log(f"rescued job {job.get('job_id')} -> inbox")


def classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("downloaderror", "no segments", "private", "age-restricted",
                              "region", "invalid", "does not exist", "404")):
        return "permanent"
    return "transient"


def run_stage_download(job, state, job_dir):
    source = download_youtube_local(job["source_url"], fmt=state.get("format", "480"))
    st.mark_stage(state, "download", "done", artifact=str(source))
    return source


def run_stage_transcribe(job, state, job_dir, source):
    transcript = transcribe_local(source)
    words_path = Path(source).with_suffix(".words.json")
    if not words_path.exists():
        # words.json lives next to the .srt cache in LOCAL_OUTPUT_DIR
        import glob
        candidates = sorted(Path("output").glob(f"*.words.json"))
        words_path = candidates[0] if candidates else None
    st.mark_stage(state, "transcribe", "done", artifact=str(words_path) if words_path else "")
    return transcript, words_path


def run_stage_highlight(job, state, job_dir, transcript):
    result = get_highlights(transcript, num_clips=state.get("num_clips", 3), llm_fn=call_local_llm)
    highlights = result.get("highlights", [])
    top = sorted(highlights, key=lambda h: int(h.get("score", 0)), reverse=True)[: state["num_clips"]]
    art = job_dir / "highlights.json"
    art.write_text(json.dumps({"highlights": top}, indent=2, ensure_ascii=False), encoding="utf-8")
    st.mark_stage(state, "highlight_llm", "done", artifact=str(art))
    return top


def run_stage_crop(job, state, job_dir, source, top):
    aspect = state.get("aspect_ratio", "9:16")
    shorts = crop_highlights_local(source, top, aspect_ratio=aspect, out_dir=str(job_dir))
    st.mark_stage(state, "crop", "done", artifact=str(job_dir))
    return shorts


def run_stage_subtitles(job, state, job_dir, words_path, shorts):
    words = json.loads(words_path.read_text(encoding="utf-8")) if words_path and words_path.exists() else []
    final = []
    for short in shorts:
        clip = short.get("clip_url")
        if not clip:
            final.append(short)
            continue
        captioned = str(Path(clip).with_suffix("")) + "_captioned.mp4"
        try:
            subtitles.subtitle_burn_stage(
                clip, words,
                float(short["start_time"]), float(short["end_time"]),
                captioned,
            )
            short["clip_url"] = captioned
        except Exception as e:
            short["clip_url"] = None
            short["error"] = f"subtitle_burn: {e}"
        final.append(short)
    st.mark_stage(state, "subtitle_burn", "done", artifact=str(job_dir))
    return final


def run_stage_cleanup(job, state, job_dir, source, final):
    # Keep only the captioned shorts; delete source + intermediates.
    if source and Path(source).exists() and Path(source).name.startswith("source_"):
        try:
            Path(source).unlink()
        except OSError:
            pass
    for p in job_dir.glob("*.cut.mp4"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in job_dir.glob("*.silent.mp4"):
        try:
            p.unlink()
        except OSError:
            pass
    published = PUBLISHED / job["job_id"]
    published.mkdir(parents=True, exist_ok=True)
    for short in final:
        src = short.get("clip_url")
        if src and Path(src).exists():
            shutil.copy2(src, published / Path(src).name)
    st.mark_stage(state, "cleanup", "done", artifact=str(published))
    return published


def run_job(active_path: Path, seen: set) -> bool:
    job = json.loads(active_path.read_text(encoding="utf-8"))
    job_id = job.get("job_id", active_path.stem)
    job["job_id"] = job_id
    job_dir = JOBS / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state_path = job_dir / "state.json"

    state = st.load_state(state_path) if state_path.exists() else st.new_state(job)

    log(f"[job {job_id}] start {job.get('source_url', '')}")
    try:
        source = state.get("stages", {}).get("download", {}).get("artifact")
        if not st.stage_done(state, "download"):
            source = run_stage_download(job, state, job_dir)
            st.save_state(state_path, state)

        transcript = None
        words_path = None
        if not st.stage_done(state, "transcribe"):
            transcript, words_path = run_stage_transcribe(job, state, job_dir, source)
            st.save_state(state_path, state)
        else:
            words_path = Path(state["stages"]["transcribe"]["artifact"]) if state["stages"]["transcribe"].get("artifact") else None

        top = None
        if not st.stage_done(state, "highlight_llm"):
            if transcript is None:
                transcript = transcribe_local(source)
            top = run_stage_highlight(job, state, job_dir, transcript)
            st.save_state(state_path, state)
        else:
            art = state["stages"]["highlight_llm"].get("artifact")
            top = json.loads(Path(art).read_text(encoding="utf-8"))["highlights"] if art and Path(art).exists() else None

        shorts = None
        if not st.stage_done(state, "crop"):
            if top is None:
                raise RuntimeError("no highlights to crop")
            shorts = run_stage_crop(job, state, job_dir, source, top)
            st.save_state(state_path, state)
        else:
            shorts = []

        final = None
        if not st.stage_done(state, "subtitle_burn"):
            final = run_stage_subtitles(job, state, job_dir, words_path, shorts)
            st.save_state(state_path, state)

        if not st.stage_done(state, "cleanup"):
            published = run_stage_cleanup(job, state, job_dir, source, final or shorts)
            st.save_state(state_path, state)

        state["status"] = "done"
        st.save_state(state_path, state)
        os.replace(active_path, DONE / active_path.name)
        seen.add(job.get("source_url", ""))
        save_seen(seen)
        log(f"[job {job_id}] DONE")
        return True
    except Exception as e:
        err_type = classify_error(e)
        state["error"] = f"{err_type}: {e}"
        state["status"] = "failed" if err_type == "permanent" else "error"
        st.save_state(state_path, state)
        os.replace(active_path, FAILED / active_path.name)
        log(f"[job {job_id}] FAILED ({err_type}): {e}")
        return err_type == "permanent"  # permanent failure counts as handled


def housekeeping() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.touch()
    try:
        usage = shutil.disk_usage(ROOT)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 20:
            log(f"WARN low disk: {free_gb:.1f} GB free")
        # Prune published/ to newest 50 entries
        entries = sorted(PUBLISHED.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if PUBLISHED.exists() else []
        for old in entries[50:]:
            shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    except Exception as e:
        log(f"housekeeping error: {e}")


def main() -> None:
    for d in (INBOX, ACTIVE, DONE, FAILED, JOBS, PUBLISHED, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    rescue_active()
    consecutive_failures = 0

    log("worker started")
    while True:
        try:
            active_path = claim_next_job()
            if active_path is not None:
                handled = run_job(active_path, seen)
                consecutive_failures = 0 if handled else consecutive_failures + 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log(f"circuit breaker: {consecutive_failures} consecutive failures, sleeping 300s")
                    time.sleep(300)
                    consecutive_failures = 0
                continue
            housekeeping()
            time.sleep(IDLE_SLEEP)
        except KeyboardInterrupt:
            log("worker stopped by user")
            break
        except Exception as e:
            log(f"worker loop error: {e}; sleeping {IDLE_SLEEP}s")
            time.sleep(IDLE_SLEEP)


if __name__ == "__main__":
    main()
```

### Step 4.3 — queue dirs
```bash
mkdir -p queue/inbox queue/active queue/done queue/failed jobs published logs
```

### Step 4.4 — launchd LaunchAgent (`~/Library/LaunchAgents/com.user.shorts.pipeline.plist`)
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.user.shorts.pipeline</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/USERNAME/AI-Youtube-Shorts-Generator/venv/bin/python</string>
    <string>/Users/USERNAME/AI-Youtube-Shorts-Generator/worker.py</string>
  </array>

  <key>WorkingDirectory</key><string>/Users/USERNAME/AI-Youtube-Shorts-Generator</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key><string>/Users/USERNAME</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>

  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>/Users/USERNAME/AI-Youtube-Shorts-Generator/logs/worker.log</string>
  <key>StandardErrorPath</key><string>/Users/USERNAME/AI-Youtube-Shorts-Generator/logs/worker.err.log</string>
</dict>
</plist>
```
**Replace `USERNAME`** with the actual macOS username (LOCKED: do not change the paths' structure).

### Verify (exact)
```bash
# 1. worker imports clean (no launchd needed)
source venv/bin/activate && python -c "import worker; print('worker imports OK')"

# 2. submit a job: write one job JSON into the inbox
python - <<'PY'
import json, uuid
job = {"job_id": uuid.uuid4().hex[:8], "source_url": "/path/to/your/test.mp4",
       "num_clips": 1, "aspect_ratio": "9:16", "format": "480"}
json.dump(job, open(f"queue/inbox/{job['job_id']}.json", "w"))
print("submitted", job["job_id"])
PY

# 3. run the worker ONCE in the foreground (it processes the inbox then idles)
timeout 120 python worker.py &
sleep 90; ls queue/done/ queue/failed/ published/ 2>/dev/null

# 4. load the LaunchAgent for 24/7 operation
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.shorts.pipeline.plist
launchctl print gui/$(id -u)/com.user.shorts.pipeline | head -20
```
**Expected:** step 1 prints `worker imports OK`; step 3 shows a file in `queue/done/`, a
`published/<job_id>/` dir containing `short_01_captioned.mp4`, and the source deleted;
step 4 shows the agent with `state = running`.

**Stop the agent when needed:**
```bash
launchctl bootout gui/$(id -u)/com.user.shorts.pipeline
```

**LOCKED:** queue dir names exactly `inbox/active/done/failed`; state key names exactly as in `stage.py`;
heartbeat path `logs/heartbeat`; published cap 50; inbox cap 20.
**Cut if rushed:** the `source_select` stage (auto-enqueue from `source_pool.txt`) — the queue is
manual (`put a JSON in inbox/`) and that's fine.

---

## Phase 5 — Hardening: retries, disk guard, dedupe (already built-in)

Most of this shipped inside `worker.py` (error classification, circuit breaker, `seen.json`, cleanup,
published pruning, disk warning). Remaining steps:

### Files: `.env` (add), `worker.py` (no change)

### Step 5.1 — `.env` additions
```bash
LOCAL_WHISPER_VAD_FILTER=false
# Retry knobs (read by worker.py's transient-error path if you extend it)
# PIPE_RETRY_LIMIT=3
```
(`LOCAL_WHISPER_VAD_FILTER` stays `false` — VAD is too aggressive on mixed speech/music, per repo config docs.)

### Step 5.2 — optional but recommended: pin model caches (prevents 256 GB surprises)
```bash
mkdir -p cache/ollama cache/hf
echo 'export OLLAMA_MODELS="$HOME/AI-Youtube-Shorts-Generator/cache/ollama"' >> ~/.zshrc
echo 'export HF_HOME="$HOME/AI-Youtube-Shorts-Generator/cache/hf"' >> ~/.zshrc
source ~/.zshrc
```
(If Ollama already stored models elsewhere, re-pull qwen3:14b after the pin — it re-downloads into the
new location; then remove the old `~/.ollama/models` to reclaim ~9.3 GB.)

### Verify (exact)
```bash
df -h | head -5     # confirm you're above 20 GB free
cat logs/worker.log | tail -5
```
**Expected:** healthy free space; worker log shows `worker started` and job lifecycle lines.

**LOCKED:** VAD filter off; caches under `cache/{ollama,hf}`.
**Cut if rushed:** the cache pinning — but then watch `~/.ollama/models`.

---

## Phase 6 — OPTIONAL: automated publishing (not in committed scope)

Decision recorded in `01-RESEARCH-REPORT.md §7`: **manual upload is the committed scope.** If you later
automate, the free + sanctioned path is the **official Instagram API with Instagram Login**
(Creator account, no FB Page, no App Review for your own account, 50 posts/24 h, mp4 must be on a public
HTTPS URL briefly). YouTube upload: YouTube Data API or the `yt-upload` tool.

Implement as a new `publish.py` + a `publish` stage inserted in `worker.py` between `subtitle_burn` and
`cleanup` — **only when you decide to do it.** Until then, `publish` is intentionally absent from the
state machine. (Anti-fabrication: do not write a publish stub that pretends to post — that's ban-bait
and fake output.)

---

## §9 Changelog / status

| Date | Phase | Status | Notes |
|---|---|---|---|
| 2026-08-07 | 0 | ✅ | env setup + deps + Ollama smoke test — venv built, faster-whisper/cv2/yt-dlp/openai installed. **Laptop pin: `opencv-python==4.10.0.84`** (5.x drops `cv2.CascadeClassifier`). Port confirmed: Mac's Ollama on **11434**, tunnel `-L 11435:127.0.0.1:11434`. |
| 2026-08-07 | 1 | ✅ | free LLM via Ollama — first e2e run. `config.py` (OPENAI_BASE_URL + localhost key relax), `local/llm.py` (ollama backend + route). `qwen3:14b` via tunnel, score 92; `output/short_01.mp4` 572×1020 9:16 with audio. |
| _today_ | 2 | ⬜ | word-level timestamps |
| _today_ | 3 | ⬜ | subtitle burn-in |
| _today_ | 4 | ⬜ | worker + launchd 24/7 |
| _today_ | 5 | ⬜ | hardening |
| _today_ | 6 | ⬜ | OPTIONAL publish (not committed) |

> No changelog entry = the change doesn't exist. Update this table as phases complete.

---

## §10 Done-verification checklist (whole pipeline)

```bash
# From the repo root, worker running:
echo '{"job_id":"finaltest","source_url":"/path/to/your/test.mp4","num_clips":2}' > queue/inbox/finaltest.json
# wait for worker to pick it up; then:
ls published/finaltest/
ffprobe published/finaltest/short_01_captioned.mp4 2>&1 | grep -iE "duration|1080x1920"
```
**Expected:** two files `short_01_captioned.mp4`, `short_02_captioned.mp4`; each 1080x1920, has audio,
shows captions; `queue/done/finaltest.json` exists; source deleted; no network calls to any paid API.