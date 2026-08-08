<div align="center">

# 🎬 YT_clips

### Turn any YouTube link into professional, complete 9:16 Shorts — locally, for free.

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat-square&logo=python&logoColor=white)](#)
[![Local](https://img.shields.io/badge/100%25-Local-2ECC71?style=flat-square)](#)
[![Free](https://img.shields.io/badge/Free-No%20credits-2ECC71?style=flat-square)](#)
[![LLM](https://img.shields.io/badge/LLM-Ollama-4B8BBE?style=flat-square&logo=ollama&logoColor=white)](#)
[![STT](https://img.shields.io/badge/STT-faster--whisper-9C27B0?style=flat-square&logo=openai&logoColor=white)](#)
[![ffmpeg](https://img.shields.io/badge/ffmpeg-libass-049375?style=flat-square&logo=ffmpeg&logoColor=white)](#)
[![Platform](https://img.shields.io/badge/platform-macOS-333333?style=flat-square&logo=apple&logoColor=white)](#)
[![Status](https://img.shields.io/badge/status-active-2ECC71?style=flat-square)](#)

</div>

---

## 🧭 What it is

A **fully offline, free** pipeline that turns any long-form YouTube video into
finished-looking Shorts — the whole chain (transcription, highlight selection,
cropping, caption burning, loudness) runs on your own machine. An optional
cloud mode is included for when you'd rather use a hosted LLM.

> Everything is tuned so the output **looks like it was edited by a professional**: it doesn't just chop 60-second slices — it cuts on complete thoughts.

---

## ✨ Why the output looks finished

> [!TIP]
> **Complete sentences.** Every clip opens at the *start of a sentence* and lands at a **natural pause** in the speech — never a mid-thought chop. Clips can run up to your cap (**default 120 s**) so a full point or mini-conversation finishes.

> [!TIP]
> **Genuinely distinct clips.** Highlights are deduped on their **complete spans** *before* cropping, so `N` requested clips cover `N` different moments — no "same clip twice" filler.

> [!TIP]
> **One moment per clip — never merged.** A topic-segmentation pass (`nomic-embed-text` similarity dips + real pauses) computes **boundaries** on the source, and any highlight window that crosses one is **split into separate clips** instead of becoming one mangled Short. Your 6-moment 2-minute video becomes 6 clips, not one 120 s blob.

> [!IMPORTANT]
> **As a library-grade asset.** Big bold **hook title** burned over the opening seconds · clean, thin-outlined **captions** (≤4 words/line, key-word highlight) · **−14 LUFS** loudness (Short/Reels standard) · **30 fps** locked · **1080p** vertical reframe.

> [!TIP]
> **No glitchy motion.** A steady, slow push-in zoom with a fixed anchor — not jumpy per-frame face-tracking (which stutters on two-speaker shots).

---

## ⚙️ How it works

```
 queue/inbox/<job>.json
      │  drop a job → the 24/7 worker picks it up (kept alive by launchd)
      ▼
 ┌─────────────┐  ┌───────────────┐  ┌──────────────────┐  ┌────────────────────────┐
 │ 1. download │→│ 2. transcribe │→│ 3. segment       │→│ 4. highlight            │
 │   yt-dlp    │  │  faster-whisper│ │  topic boundaries│ │   local LLM ranks      │
 │  (1080p)    │  │  → .srt+words │  │  (nomic-embed)   │ │   → dedup spans        │
 └─────────────┘  └───────────────┘  └────────┬─────────┘  └───────────┬───────────┘
                                              ▼                         ▼
 ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐
 │ 7. publish   │← │ 6. subtitles │← │ 5. crop                 │
 │  → published/│  │  hook+captions│  │  9:16 reframe + zoom    │
 │   + manifest │  │  (libass)    │  │  + loudnorm −14 LUFS    │
 └──────────────┘  └──────────────┘  └──────────────────────────┘
```

Workers are **crash-safe**: each job's progress lives in `jobs/<id>/state.json`, finished stages are cached, and in-flight jobs are auto-rescued on boot. A broken job never takes down the loop.

---

## 🚀 Quickstart

One command for a single video (needs `ffmpeg` with `--enable-libass`, Ollama, and the venv):

```bash
python3 -m venv venv && source venv/bin/activate
python main.py "https://www.youtube.com/watch?v=..." --mode local --num-clips 5 --aspect-ratio 9:16
```

> [!NOTE]
> **Local mode is free.** It uses a local Ollama model for ranking and local Whisper for transcription. Install with:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-local.txt     # yt-dlp, faster-whisper, opencv, dotenv, ...
cp .env.example .env
ollama pull qwen3:14b                     # the reliable strict-JSON ranker
brew install ffmpeg-full                  # libass is required for captions
```

**As a 24/7 queue worker (the project's real workflow on macOS):**

```json
// drop into queue/inbox/<job_id>.json
{
  "job_id": "abc12345",
  "source_url": "https://www.youtube.com/watch?v=...",
  "num_clips": 5,
  "aspect_ratio": "9:16"
}
```

---

## 🧰 Feature matrix

| Capability | Local mode | API mode |
|---|---|---|
| Transcription | faster-whisper `small` (CPU) | hosted |
| Highlight ranking | **Ollama** `qwen3:14b` | MuAPI |
| Vertical reframe + zoom | ✅ | ✅ |
| Hook title + styled captions | ✅ | ✅ |
| −14 LUFS + 30 fps | ✅ | ✅ |
| Source quality | 1080p, resolution-aware cache | 1080p |

---

## ⚙️ Configuration (`.env`)

| Knob | Default | Purpose |
|---|---|---|
| `OPENAI_MODEL` | `qwen3:14b` | Local ranking model (must emit strict JSON) |
| `OPENAI_BASE_URL` | `http://localhost:11434/v1` | Ollama OpenAI-compat endpoint |
| `LOCAL_WHISPER_MODEL` | `small` | Whisper size (accuracy vs. speed) |
| `SHORTS_MAX_SECONDS` | `120` | Longest clip (~complete point) |
| `SHORTS_MIN_SECONDS` | `8` | Shortest clip (pulls START back, keeps ending) |
| `HOOK_TEXT / HOOK_SECONDS / HOOK_FONT_SIZE` | `true / 3 / 72` | Hook-title overlay |
| `DYNAMIC_ZOOM / ZOOM_MAX` | `true / 0.06` | Slow push-in (no face-tracking) |
| `LOUDNESS_FILTER` | `loudnorm=I=-14:TP=-1.5:LRA=11` | −14 LUFS audio |
| `OUTPUT_FPS` | `30` | Locked frame rate |
| `DOWNLOAD_FORMAT` | `1080` | Preferred source resolution |
| `KEYWORD_EMPHASIS` | `true` | Yellow key-word highlight |
| `SUBTITLE_LANGUAGE` | — | Force `en` for English-only captions |
| `SEGMENTATION_SERVICE` | `auto` | `off` / `semantic` / `auto` — topic-boundary pass (fixes merged clips) |
| `TOPIC_SIM_SIGMAS` | `0.5` | Boundary threshold: std-devs below mean similarity |
| `PAUSE_BOUNDARY_SECONDS` | `1.2` | Real silence ≥ this is a hard clip boundary |
| `SEGMENT_MIN_SECONDS` | `4` | Split pieces under this merge back (avoids slivers) |
| `BOUNDARY_MIN_GAP_SECONDS` | `8` | Min gap between clip boundaries (dense turn-taking clusters collapse) |

---

## 📁 Repo layout

```
main.py                    one-shot CLI (mode local | api)
worker.py                 24/7 queue pipeline worker (runs under launchd)
stage.py                  per-stage state helpers
subtitles.py              ASS caption/hook builder + burn
shorts_generator/         pipeline package
  local/                  local-mode implementations
    downloader.py         yt-dlp (resolution-aware cache)
    transcriber.py        faster-whisper wrapper (+ cache)
    llm.py                strict-JSON local-LLM highlight ranking
    clipper.py            vertical reframe + sentence alignment + zoom
  config.py               all knobs (env-driven)
docs/                      design notes (research, implementation plan, Mac handoff)
outputs/                   published sample runs (MANIFEST + highlights.json)
```

---

## ⚠️ Notes & limits

> [!WARNING]
> **Rankers must emit strict JSON.** Of the local models tried, **qwen3:14b** is the reliable choice. Some fast compact models (`phi3`) are quicker but occasionally emit malformed JSON (→ retries).

> [!NOTE]
> **How many distinct clips?** It's bounded by the source. A 6-minute dense monologue reliably yields ~3 real sections; a long interview yields more. When you ask for 5 but the source has only 4 complete sections, the pipeline **won't invent** a repetitive 5th clip — feedback, not filler.

> [!CAUTION]
> Caption burning needs a **`ffmpeg` built with `libass`**. The stock Homebrew `ffmpeg` may skip captions; use `ffmpeg-full`.

---

<div align="center">

<sub>Local · Free · Yours — from a long video to complete Shorts.</sub>

</div>