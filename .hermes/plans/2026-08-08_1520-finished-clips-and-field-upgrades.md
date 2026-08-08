# YT_clips — "Finished Clips" + Best-of-Field Upgrades Implementation Plan

> **For Hermes:** implement task-by-task, verifying each phase on the 2 test videos
> (proof: `video_1b5576da1d2e.mp4` · interview: `8XyiBBhmBQ0`).

**Goal:** make `main.py --mode local` output *finished, Shorts-ready* mp4s (hook + captions + −14 LUFS + 30fps + 1080×1920), then steal the two best free/local ideas from the field research (karaoke word-level captions, MediaPipe face-track reframe) and fix the flat LLM ranking on long transcripts.

**Architecture:** one render pass shared by CLI and worker paths. Local path currently ends at `crop_highlights_local` → raw 606×1080@25fps clips. The finished treatment already exists in `subtitles.py` (`build_ass` + `burn_subtitles`, pads to 1080×1920, locks OUTPUT_FPS, loudnorm −14) — it's just only called from the worker path. Phase 0 is **reuse, not invention**.

**Tech stack:** ffmpeg (libass bundled), Python 3.14 venv, existing `words.json` word timestamps, `mediapipe` (pip, CPU-only) for face tracking, Ollama `qwen3:14b` + `nomic-embed-text`.

**Steal credits (researched 2026-08-08):**
- Karaoke/word-level captions → `NaufalRizqullah/opensource-clipping`, `Shaarav4795/ClippedAI`
- MediaPipe BlazeFace smooth-pan reframe → `NaufalRizqullah/opensource-clipping`
- Chunked transcript ranking → existing `chunk_transcript()` in `highlights.py` (not wired in local mode)

---

## Phase 0 — Parity: finished treatment in `--mode local` (reuse `subtitles.py`)

### Task 0.1: Wire subtitle burn into `_run_local`

**Files:**
- Modify: `shorts_generator/pipeline.py:53` (after `crop_highlights_local`, before `return`)
- Modify: `shorts_generator/local/clipper.py` — ensure each short dict carries `words` range or the clip's raw cut path
- Reuse: `subtitles.py` `build_ass(words, clip_start, clip_end, out_path, hook_text)` + `burn_subtitles(clip_path, ass_path, out_path)`

**Steps:**
1. In `_run_local`, for each short produced: load `output/<source>.words.json` (already cached), call `build_ass` with the clip's global start/end + the highlight's `hook_sentence` as `hook_text`, then `burn_subtitles` → `short_XX_captioned.mp4`.
2. Point the result's `clip_url` at the captioned file.
3. Handle `HOOK_TEXT=false` / `KEYWORD_EMPHASIS=false` config (build_ass already respects them).

**Verification (proof, not vibes):**
- `ffprobe` on a captioned clip → `1920x1080`, `30/1` fps, loudnorm measured `I ≈ -14 LUFS` (`ffmpeg -af ebur128 -f null -`)
- `tesseract` OCR of a frame at t=1.5s → hook text present; frame at t=15s → caption text present
- Re-run the full interview `8XyiBBhmBQ0` → all 9 clips get `_captioned` variants

### Task 0.2: Config + README truth

- Set `HOOK_TEXT=true` defaults (already default) — ensure `.env.example` documents that local mode now renders.
- Update README: remove the "raw" caveat; state local mode output = finished.

**Checkpoint Phase 0:** both test videos produce OCR-verified captioned clips at 1080×1920/30fps/−14 LUFS. Commit `feat: finished treatment for --mode local`.

---

## Phase 1 — Karaoke captions (word-by-word pop, Hormozi-style)

### Task 1.1: Word-level ASS events in `build_ass`

**Files:**
- Modify: `subtitles.py` `build_ass` (chunking loop at lines 104–127)

**Approach:** replace the ≤6-word static chunk with **per-word Dialogue events** (each word = one event using its own start/end from `words.json`). Words matching `KEYWORD_EMPHASIS` keep the yellow highlight style. Optional `\k`-based fill later; start with pop-in (simplest, looks like Hormozi/Veed).
- Keep the "no caption under hook during first HOOK_SECONDS" rule.
- Cap: if a word has no end (whisper padding), default 0.3s.

**Verification:**
- OCR two frames 1–2s apart inside one clip → caption text *changes* (word-level timing, not static chunk)
- Unit check: `build_ass` event count ≈ word count (no chunk collapse)

### Task 1.2: Per-clip burn re-run + A/B

- Re-run interview; eyeball one clip vs Phase 0 (word pop vs static lines).

**Checkpoint Phase 1:** karaoke verified on one proof + one interview clip. Commit `feat: karaoke word-level captions`.

---

## Phase 2 — MediaPipe face-track reframe (steal, default `off` → `auto` later)

### Task 2.1: Add mediapipe + detector module

**Files:**
- Create: `shorts_generator/local/facetrack.py` (lazy-import mediapipe so absence doesn't break anything)
- Modify: `shorts_generator/config.py` → `FACE_TRACK = off|haar|mediapipe` (default `off` — keep current no-jump anchor as the safe default, matching today's design intent)

**Approach (from opensource-clipping):** per-frame face detection at reduced scale (e.g. 480px wide) → smoothed target center (EMA + deadzone: ignore moves < 3% of frame width) → clamp crop window to frame bounds → feed to the existing crop logic in `clipper.py:183`.

**Verification:**
- Two-speaker interview clip: face bbox of output frames stays within frame ≥ 98% of frames (sample every 1s)
- No new deps at import time when mediapipe missing; `FACE_TRACK=off` path byte-identical to today

**Checkpoint Phase 2:** face-track clip verified on interview; off-path regression-tested. Commit `feat: mediapipe face-track reframe`.

---

## Phase 3 — Ranker quality on long transcripts (flat 7–9 scores)

### Task 3.1: Wire `chunk_transcript` into local highlights

**Files:**
- Modify: `shorts_generator/pipeline.py:45` (call `get_highlights` with chunking for duration > `CHUNK_SIZE_SECONDS`)

`highlights.py` already has `chunk_transcript()` + `_offset` handling and a `call_highlight_api(..., is_chunk=True)` path — the local path just never uses it. Merge per-chunk highlights, re-score globally, dedupe.

### Task 3.2: (Optional) allow `LLM_PROVIDER=gemini` free key

`.env.example` already documents `GEMINI_API_KEY` — confirm `llm.py` supports it; if not, add the provider branch.

**Verification:**
- Re-run `8XyiBBhmBQ0` → top-3 scores spread (no 7–9 flatness), titles distinct, no duplicated spans

**Checkpoint Phase 3:** interview re-run has meaningful score spread. Commit `feat: chunked ranking for long videos`.

---

## Phase 4 — Cheap polish (only if wanted; each is XS–S)

- **Auto-thumbnail:** extract frame at hook time from captioned clip + draw title (reuse hook text) → `thumb_<clip>.jpg` (steal from opensource-clipping)
- **BGM + sidechain ducking:** needs asset pool + `sidechaincompress`; **defer — YAGNI** unless user asks
- **Multi-hook intro variants:** defer — current hook works

---

## Verification battery (run at every checkpoint)

```bash
cd ~/YT_clips
SEGMENTATION_SERVICE=auto ./venv/bin/python main.py \
  "/Users/inunity/.hermes/cache/videos/video_1b5576da1d2e.mp4" \
  --mode local --num-clips 3
# + the interview URL for multi-speaker/long-form checks
```

| check | command |
|---|---|
| duration sanity | `ffprobe -show_entries format=duration` |
| resolution/fps | `ffprobe -select_streams v:0 -show_entries stream=width,height,r_frame_rate` |
| loudness | `ffmpeg -i out.mp4 -af ebur128 -f null - 2>&1 \| grep -A3 Summary` |
| captions rendered | `ffmpeg -ss 15 -i out.mp4 -frames:v 1 f.png && tesseract f.png -` |
| boundaries still split | grep `splits into N clip(s)` in run log |

## Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| mediapipe adds ~60MB + CPU cost | Med | lazy import; detect at 480p; knob off by default |
| karaoke word timing jitter (whisper words overlap) | Low | clamp to [prev_end, next_start]; min 0.2s |
| burn pass doubles render time | Med | only re-encode once (single ffmpeg call per clip — already the design) |
| chunked ranking changes highlight quality | Med | keep `is_chunk=False` fallback path; A/B on interview |

## Open questions (for user)

1. Face-track default: keep `off` (safe, current) or `auto` for single-face clips? → recommendation: `off` for v1
2. BGM/thumbnails — want them now or later?
3. After Phase 0, should published/ artifacts (worker path) also get karaoke? (shared `build_ass` → yes automatically)
