# 01 — Research Report

**Scope (committed):** automate from **YouTube URL → finished, subtitled vertical clips**. Upload to
Instagram is **manual** (Phase 6 documents optional automation). Runs entirely free, 100% local on the
Mac Mini.

---

## 1. What the cloned code actually does (reverse engineering)

The repo is an "AI clipping tool" — an open-source Opus Clip / Klap alternative. Two modes:

- `--mode api` (default) → everything delegated to **MuAPI** cloud (requires a **paid** key). *Not suitable for the free goal.*
- `--mode local` → everything runs locally except one LLM call. **This is what we build on.**

### `--mode local` pipeline (as shipped)

| Step | File | Function | Notes |
|---|---|---|---|
| 1. Download | `shorts_generator/local/downloader.py` | `download_youtube_local()` | `yt-dlp`; caches `source_<id>.mp4` in `output/` |
| 2. Transcribe | `shorts_generator/local/transcriber.py` | `transcribe_local()` | `faster-whisper`; writes an `.srt` cache; **no word-level timestamps** |
| 3. Rank highlights | `shorts_generator/local/llm.py` + `shorts_generator/highlights.py` | `call_local_llm()` → `get_highlights()` | ONLY PAID STEP. OpenAI or Gemini; returns ranked viral moments (JSON: `start_time,end_time,score,hook_sentence,virality_reason`) |
| 4. Crop | `shorts_generator/local/clipper.py` | `crop_highlights_local()` | ffmpeg cut to `[start,end]` **+** OpenCV face-tracking vertical 9:16 reframe |
| 5. Output | —                | —           | `output/short_01.mp4 …` — **no subtitles burned in** |

Orchestrator: `shorts_generator/pipeline.py` → `generate_shorts()` → `_run_local()`.

### Key facts that make the plan easy
- **LLM is pluggable.** `get_highlights(transcript, llm_fn=call_local_llm)` accepts any callable
  `(prompt) -> str`. We only need to supply a new `llm_fn` hitting **Ollama locally** — the whole
  highlight-ranking + JSON-parsing logic (`highlights.py`) is untouched.
- **Transcription already writes `.srt`.** The subtitle material already exists; we just need to add
  *word-level* timestamps and *burn* them.
- **Per-clip try/except already exists** in `clipper.py` — a failed clip won't kill the batch.

---

## 2. The THREE changes needed (all verified feasible)

### Change A — Free LLM: point the OpenAI SDK at local Ollama
`call_openai_llm()` in `shorts_generator/local/llm.py` uses `OpenAI(api_key=...)`. Ollama exposes an
**OpenAI-compatible** endpoint at `http://localhost:11434/v1` where `api_key` is required-but-ignored
(`verified`: docs.ollama.com/api/openai-compatibility). So we add an `ollama` provider + route it.
Result: highlight ranking costs **$0, forever, offline**.

### Change B — Word-level timestamps
Add `word_timestamps=True` to the `model.transcribe(...)` call in `transcriber.py`. `faster-whisper`
emits `segment.words` (list of `{start,end,word}`). Needed for proper karaoke-style captions.
`faster-whisper` is already a repo dependency — lightest option for 16 GB RAM
(verified: small-int8 ≈ 1.5 GB RAM, ~7.6× realtime).

### Change C — Subtitle burn-in (net-new stage)
Build an **ASS** file from the word timestamps, then burn with `ffmpeg -vf ass=...`. The repo has no
caption stage; we add `subtitles.py` + a `subtitle_burn` stage.

> These are the only 3 functional changes. Everything else (orchestration, storage, reliability) is
> additive and optional-scoped.

---

## 3. Whisper on Apple Silicon — decision

| | faster-whisper ✅ | mlx-whisper | whisperX |
|---|---|---|---|
| Accelerator | CPU only (CTranslate2) | GPU + ANE (fastest) | CPU on Mac (torch, CUDA-oriented) |
| Speed (Apple Silicon) | small-int8 ~7.6× realtime | very fast | slow on CPU |
| RAM peek | small ~1.5 GB | ~1.9-3 GB | ~4-6 GB |
| Word timestamps | ✅ native | ✅ native | ✅ best accuracy |
| Fits this repo | **Drop-in** (already a dep) | needs rewrite | heavy/torch |

**Choose faster-whisper.** Already wired in, lightest RAM (16 GB shared with a 9.3 GB Ollama model),
native word timestamps, adequate caption accuracy. Known trade-off: word timing is token-level and can
drift a few hundred ms vs whisperX's phoneme alignment — acceptable for "good" captions; that's the
documented upgrade path if captions visibly lag.

---

## 4. LLM choice — decision

Use **Ollama `qwen3:14b`** (9.3 GB, verified) already installed on the Mac Mini (port 11434).
- Set `extra_body={"think": False}` — qwen3 is a *reasoning* model; without disabling thinking it may
  emit `...` reasoning blocks that break JSON parsing. (The repo's `_parse_json_loose` also strips
  fences/slices `{…}` as a fallback.)
- On 16 GB, one Ollama model stays resident (9.3 GB) + faster-whisper-small (~1.5 GB) fit fine.
  Don't run whisper-large-v3 and a heavy second Ollama model simultaneously.

---

## 5. Orchestration — decision

**Skip Celery/Redis** (not justified: a Mac Mini producing a few videos/day is a single serial worker).
Use **launchd with `KeepAlive=true` supervising ONE long-lived `worker.py` infinite loop + a file queue.**

Why: files-as-jobs survive crashes; `KeepAlive` auto-restarts the worker on death (cron/APScheduler don't);
the workload is strictly serial so there's no parallelism to buy.

### Per-video = stage state machine in `state.json`
Each job gets a `jobs/<id>/state.json` recording per-stage status + artifact paths. Rules:
1. **Idempotent:** skip a stage if `state.stages[name].status == "done"` and the artifact exists.
2. **Atomic writes:** write to tmp then `os.replace()` (no half-files on crash).
3. **Rescue `queue/active/*` on boot:** re-queue any `status != "done"` job and resume from the last
   completed stage.

---

## 6. Storage on 256 GB — decision

| Uses | Size |
|---|---|
| macOS system | ~40-60 GB |
| `~/.ollama/models` (qwen3:14b only) | **9.3 GB** (verified) — drop unneeded models |
| Whisper cache (`HF_HOME`) | base ~0.5 GB / large-v3 ~3 GB |
| In-flight source @480/720p | ~0.3-0.5 GB each |
| Kept published shorts | as configured (~50) |

Hygiene: pin `OLLAMA_MODELS` + `HF_HOME` under `~/pipeline/cache/`; per-job sweep deletes source +
intermediates after burn; global sweep in the idle loop warns when free < 20 GB; cap inbox ≤ 20 jobs;
download at 480/720p.

### Error handling
- Permanent errors (yt-dlp `DownloadError`, "no segments") → fail immediately, move to `failed/`, never retry.
- Transient (5xx/timeout, Ollama busy) → retry with backoff + jitter, max 3-5.
- Circuit breaker: after N consecutive failures, sleep 5-10 min.
- `seen.json` (URL hashes) prevents re-enqueueing failed/processed/failed sources.

---

## 7. Instagram publishing — decision (OPTIONAL, Phase 6)

**Committed scope = manual upload.** When you choose to automate, the verified free + sanctioned path:

> **Official "Instagram API with Instagram Login"** — works on a **Creator (professional)** account
> (converted free in-app, ~2 min), **no Facebook Page needed**, **no App Review** for an account you own
> (Standard Access), free, **50 posts/24 h**, fully headless.

Flow: `POST /<IG_ID>/media` (`media_type=VIDEO` + `video_url`) → `POST /<IG_ID>/media_publish`
(`is_reel=true` for a Reel). Caveat: the mp4 must be on a **public HTTPS URL** briefly (free Cloudflare R2
/ S3 / tiny self-hosted endpoint). Video output (YouTube) can use YouTube Data API or `yt-upload`.

**Avoid** unofficial libs (instagrapi/Selenium) for a brand-new account — ToS violation with real,
escalating enforcement (`challenge_required`, shadowban, eventual disable). Unsanctioned path is documented
for risk-awareness, not as a technique.

---

## 8. Copyright & account health — read this before posting anything

- Re-clipping **other people's** YouTube videos and reposting is **reposting, not fair use** — the #1
  strike source on IG. Subtitles on a straight clip = weak protection.
- **Safest sources:** your own content; CC-licensed that allows derivatives (`CC BY`/`CC BY-SA`, *with
  attribution*); licensed stock. **Avoid:** popular creators' clips, recognizable music, TV/movie/news.
- **Crediting is not permission** — it reduces perceived theft but doesn't stop a DMCA.
- New accounts: warm up 2-4 weeks, post ~1/day (max 2-3/day after), keep same device+IP, 10-15 min real
  daily engagement. Watch for "action blocked" / flatline reach — pause 2-4 days and post original on flags.
- Free caption/hashtag generation: your local Ollama loop model — draft, then schedule natively via
  Meta Business Suite / in-app scheduler (no unofficial-API login). Legitimate automation = automating
  *creation/scheduling* via official tools; ban-bait = automating *engagement* (auto-like/follow/comment).

---

## 9. Bottom line

The whole goal is **free and local** with three small code changes (A/B/C) + additive orchestration.
The only hard limits are: (1) caption timing is token-level (good, not perfect), (2) disk is 256 GB
(manage small), and (3) asset re-use must respect copyright if the account is to survive.
See `02-IMPLEMENTATION-PLAN.md` for the exact, guess-free build steps.