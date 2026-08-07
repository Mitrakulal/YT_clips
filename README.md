# YT_clips — Free, local YouTube-to-Shorts pipeline

Turn any long-form YouTube video into **professional, complete 9:16 Shorts** — locally on your own Mac, for free. No SaaS, no per-clip credits, no watermark.

Built for creators/students who want full control over the highlight algorithm and output quality. Every clip comes out library-finished: clean opening sentence, a **natural ending** (never cut mid-thought), loudness-normalized audio, styled captions, and a big hook title.

> The whole pipeline can run **fully offline**: local Whisper transcription + a local LLM (Ollama) for highlight selection. The only internet needed is downloading the source video.

---

## What makes the output "finished"

- **Complete boundaries** — a clip starts at the *start of a sentence* and ends at a *natural pause* in speech (extend-to-pause logic), not an arbitrary mid-conversation cut. Clips can run up to your cap (default **120 s**) so a thought or mini-conversation can finish.
- **Genuinely distinct clips** — highlights are deduped on their **complete spans** before cropping, so N clips cover N different moments (no doubled-up "same clip twice"). If the source only has 3 real sections, you get 3, not 5 repetitive ones.
- **Hook title** — a big bold title burned over the first seconds of each clip.
- **Styled captions** — bold, thin-outlined, single clean line, ≤4 words/line, with yellow keyword emphasis.
- **Loudness-normalized** — `loudnorm` to **−14 LUFS** (Short/Reels standard) + locked **30 fps**.
- **1080p vertical reframe** — up to 1080p source, center-anchored reframe with a slow, smooth push-in zoom (no glitchy per-frame face-tracking).

---

## Pipeline stages

```
 queue/inbox/<job>.json
      │  (drop a job → worker picks it up, launchd keeps it alive 24/7)
      ▼
 1. download    yt-dlp (1080p default, resolution-aware cache)
 2. transcribe  faster-whisper small (CPU) → .srt + word timestamps
 3. highlight   local LLM (Ollama: qwen3:14b) ranks viral moments;
                padded + deduped to non-overlapping complete spans
 4. crop        vertical 9:16 reframe + dynamic zoom + -14 LUFS + 30fps
 5. subtitles   burn hook + styled captions (libass)
 6. publish     final mp4s → published/<job_id>/ + manifest + highlights.json
```

---

## Two ways to run

### 1. One-shot CLI (easy, for a single video)

```bash
python main.py "https://www.youtube.com/watch?v=..." --mode local --num-clips 5 --aspect-ratio 9:16
```

### 2. 24/7 queue worker (the project's workflow on this Mac)

`worker.py` is a launchd daemon (`com.user.shorts.pipeline`) that watches a file queue and runs every job through the `download → transcribe → highlight → crop → subtitles → publish` stages. Submit a job by dropping a spec into the inbox:

```json
// queue/inbox/<job_id>.json
{
  "job_id": "abc12345",
  "source_url": "https://www.youtube.com/watch?v=...",
  "num_clips": 5,
  "aspect_ratio": "9:16"
}
```

Workers are crash-safe: each job's state lives in `jobs/<job_id>/state.json`, finished stages are cached, and in-flight jobs are rescued on restart. One broken job never kills the loop.

---

## Requirements

- **Python 3** + `venv` (tested on 3.14)
- **ffmpeg with `--enable-libass`** (for styled caption burning). On macOS with Homebrew that's `ffmpeg-full`.
- **Ollama** running locally (for free local ranking) — `ollama pull qwen3:14b`
- Optional cloud: [MuAPI](https://muapi.ai) key for the `api` mode (`generate_shorts` + `--mode api`).

### Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-local.txt   # yt-dlp, faster-whisper, opencv, openai, dotenv, ...
cp .env.example .env                    # → set local model + knobs
```

---

## Configuration (`.env`)

| Knob | Default | Meaning |
|---|---|---|
| `OPENAI_MODEL` | `qwen3:14b` | Local Ollama model for ranking (must emit strict JSON) |
| `OPENAI_BASE_URL` | `http://localhost:11434/v1` | Ollama OpenAI-compat endpoint |
| `LOCAL_WHISPER_MODEL` | `small` | Whisper size (larger = slower but more accurate) |
| `LOCAL_WHISPER_DEVICE` | `cpu` | leave `cpu` unless you set up Metal/CUDA torch |
| `SHORTS_MAX_SECONDS` | `120` | Longest clip (user ceiling; enough for a complete point) |
| `SHORTS_MIN_SECONDS` | `8` | Shortest clip (reached by pulling START back, never breaking the ending) |
| `HOOK_TEXT` / `HOOK_SECONDS` / `HOOK_FONT_SIZE` | `true` / `3` / `72` | Hook-title overlay style |
| `DYNAMIC_ZOOM` / `ZOOM_MAX` | `true` / `0.06` | Slow push-in (6%) — no glitchy face-tracking |
| `LOUDNESS_FILTER` | `loudnorm=I=-14...` | −14 LUFS broadcast-normalized audio |
| `OUTPUT_FPS` | `30` | Locked output frame rate |
| `DOWNLOAD_FORMAT` | `1080` | Preferred source resolution (auto-fallback) |
| `KEYWORD_EMPHASIS` | `true` | Yellow-highlight key words in captions |
| `SUBTITLE_LANGUAGE` | (empty) | Force `en` for English-only captions |

---

## Repo layout

```
main.py                one-shot CLI (mode local | api)
worker.py              24/7 queue pipeline worker (run under launchd)
stage.py               per-stage state helpers
subtitles.py           ASS caption/hook builder + burn
shorts_generator/      pipeline package
  local/               local-mode implementations
    downloader.py      yt-dlp wrapper (resolution-aware cache)
    transcriber.py     faster-whisper wrapper (+ .srt/.words cache)
    llm.py             strict-JSON local-LLM highlight ranking
    clipper.py         vertical reframe + sentence-alignment + zoom
config.py              all knobs (env-driven)
docs/                  design notes: research report, implementation plan, Mac handoff
outputs/               published sample runs (MANIFEST + highlights.json)
```

---

## Notes & known limits

- Video length & content govern how many distinct complete clips are possible. A 6-min dense monologue reliably yields ~3 real sections; a long interview can yield many more. The pipeline **never invents** a 5th clip if the source only has 4 distinct complete sections.
- Ranking models must emit **strict JSON**. Of the local options tested, `qwen3:14b` is the reliable choice; some faster compact models (e.g. `phi3`) are quicker but occasionally produce malformed JSON and retries.
- Subtitle burning needs `ffmpeg` built with **libass**; the plain Homebrew `ffmpeg` may fail captions (use `ffmpeg-full`).

---

*Local, free, and yours. Compare moments, get complete Shorts, ship them.*