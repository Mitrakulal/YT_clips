# 03 — Mac-side Agent Handoff (execute on the Mac Mini)

**Who this is for:** a Hermes agent running **on the Mac Mini** (Apple Silicon, 16 GB, 256 GB).
**Who wrote it:** the laptop-side agent (dev machine). Phases 0–3 are already implemented, committed,
and pushed to GitHub. Your job is to set up the environment on the Mac, **verify** the committed code,
then implement and verify **Phases 4 and 5**, test end-to-end, and push.

**READ FIRST — the authoritative spec:** `reverse-engineering/02-IMPLEMENTATION-PLAN.md` (same repo,
same commit). This file is the *execution briefing*; the plan file has the verbatim code blocks and
locked decisions. Where they differ, **the plan wins** — but they should not differ.

---

## 1. Context: what this project is

A **fully automated, $0-cost, forever-free pipeline** that turns a pasted YouTube URL into **N finished,
subtitled, 9:16 vertical Shorts clips**. Flow:

```
YouTube URL → yt-dlp download → faster-whisper transcribe (word timestamps)
  → Ollama qwen3:14b ranks segments (FREE, local) → clip top segments (cv2 + ffmpeg)
  → burn captions from word timestamps (ffmpeg ASS) → ready-to-post clips in output/
```

- Instagram upload is **manual** — automation ends at finished subtitled clips.
- LLM = **local Ollama only** (no cloud, no keys, no subscriptions). Paid APIs are NOT acceptable.
- Everything runs **on this Mac Mini**. Ollama already runs here on `localhost:11434` with `qwen3:14b`.

## 2. State of the repo (already done — do NOT redo)

| Phase | Status | What exists in the repo |
|---|---|---|
| 0 | ✅ committed | venv/deps instructions (run them again locally — see §4) |
| 1 | ✅ committed | `.env.example`, `shorts_generator/config.py` (OPENAI_BASE_URL + localhost key relax), `shorts_generator/local/llm.py` (ollama backend + dispatch) |
| 2 | ✅ committed | `shorts_generator/local/transcriber.py` writes word timestamps to `<stem>.words.json` (schema `{"start","end","word"}` flat array) |
| 3 | ✅ committed | `subtitles.py` at repo root — `build_ass()` (≤8-word chunks), `burn_subtitles()` (ffmpeg `ass=` filter, 1080×1920 canvas, cwd+basename trick for path safety) |
| 4 | ⬜ **YOUR WORK** | plan has verbatim `stage.py` + `worker.py` + LaunchAgent plist — **not yet in repo** |
| 5 | ⬜ **YOUR WORK** | `.env` retry knobs + model-cache pinning (plan §5) |
| 6 | ⬜ optional | publishing — out of committed scope, skip |

**Bottom line:** the code edits are done. You implement Phase 4/5 from the plan's code blocks, then
prove the whole thing end-to-end.

## 3. LOCKED decisions (from plan §0.1/§0.3 — do not deviate)

- Pipeline runs **on this Mac**, LLM base URL = **`http://localhost:11434/v1`** (Ollama default port — no
  SSH tunnel on this machine, that was a laptop-only workaround).
- Model: **`qwen3:14b`** (already in `ollama list` — verify, pull only if missing).
- Whisper: **`faster-whisper` model `small`, device `cpu`** (locked; the plan says so. Apple-Silicon
  speedups are explicitly out of scope — do not "improve" this unless Phase 5 says so).
- Repo root venv named **`venv`**. Whisper model cache lands in `~/.cache/huggingface/`.
- Subtitle canvas: **1080×1920**.
- Commit convention: `feat: Phase N <short desc>`; push to `origin main`.

## 4. Step-by-step execution

### Step 1 — Pull the code
```bash
cd ~   # or wherever you keep projects
git clone https://github.com/Mitrakulal/YT_clips.git
cd YT_clips
git log --oneline -1    # expect: 6dfc5ef "feat: Phase 3 subtitle burn-in ..." or later
```
If the clone dir already exists: `git pull` and confirm the same HEAD.

### Step 2 — Verify Mac prerequisites (plan Phase 0)
```bash
python3 --version                      # 3.10+ expected
ffmpeg -version | head -1              # present
yt-dlp --version | head -1             # present
ollama list                            # qwen3:14b must be listed; if missing: ollama pull qwen3:14b
```
**Smoke test the free LLM path** (MUST pass before anything else):
```bash
curl -s http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:14b","messages":[{"role":"user","content":"Reply with exactly: OK"}],"think":false}'
```
Expected: JSON with `"content": "OK"`. If the response includes a `reasoning` field, that's normal —
the code handles it via `extra_body={"think": False}`.

### Step 3 — Create the venv (plan Phase 0, step 5)
```bash
cd ~/YT_clips
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-local.txt
```
**CRITICAL PITFALL (learned on the dev machine, applies everywhere):** `requirements-local.txt` says
`opencv-python>=4.8.0`, but **OpenCV 5.x removed `cv2.CascadeClassifier`**, which the repo's
`shorts_generator/local/clipper.py` uses (face-crop path). Pip may install 5.x. **Pin after install:**
```bash
pip install "opencv-python==4.10.0.84"
python -c "import cv2; print(hasattr(cv2, 'CascadeClassifier'))"   # MUST print True
python -c "import faster_whisper, cv2, yt_dlp, openai; print('deps OK')"
```
Expected: `True` then `deps OK`. Do NOT proceed until both pass.

### Step 4 — Create `.env` (copy `.env.example`, then set)
```bash
cp .env.example .env
```
Set exactly these values (keep the rest as in the example):
```
LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen3:14b
OPENAI_API_KEY=ollama
```
(`OPENAI_API_KEY` is required by the client but **ignored** by Ollama — any value works.)
`.env` is gitignored — keep it local.

### Step 5 — Verify committed Phases 1–3 work on THIS machine
Do NOT re-implement anything — just prove it runs here. Use any local `.mp4` you own (short, with
actual speech), or a short public YouTube URL. Example for a local file:
```bash
source venv/bin/activate
python main.py "/path/to/your/test.mp4" --mode local --num-clips 1 --format 480
```
Expected end state:
- `output/<stem>.srt` and `output/<stem>.words.json` exist (word timestamps, Phase 2).
- `output/short_01.mp4` exists; `ffprobe` shows video ~9:16 (e.g. 572×1020) with audio (Phase 1).
- The LLM ranking step talked to `qwen3:14b` (you'll see scores in the log).

**If the run fails with `Connection error.`**: hit the Step-2 curl again — if that works, it was a
transient cold-load; just re-run (model is warm now, ~2s). If curl fails, Ollama isn't listening on
11434 — fix that first.

### Step 6 — Implement Phase 4: 24/7 worker + launchd (plan §Phase 4)
The plan file contains **verbatim code blocks** — copy them exactly, do not rewrite:
1. **`stage.py`** (repo root) — build job JSONs from a source video: creates the job + placeholder
   files under `queue/` (plan Step 4.1, verbatim).
2. **`worker.py`** (repo root) — the one-video-at-a-time loop: picks up `queue/inbox`, runs
   download→transcribe→rank→clip→burn, moves job through `processing/` → `done/` or `failed/`
   (plan Step 4.2, verbatim). It uses the committed modules (`subtitles`, `shorts_generator.local.*`).
3. **Queue dirs** (plan Step 4.3):
   ```bash
   mkdir -p queue/inbox queue/processing queue/done queue/failed
   ```
4. **LaunchAgent** (plan Step 4.4) — write `~/Library/LaunchAgents/com.user.shorts.pipeline.plist`
   verbatim, then:
   ```bash
   launchctl load ~/Library/LaunchAgents/com.user.shorts.pipeline.plist
   launchctl list | grep shorts        # confirm loaded
   ```
   Verify the worker actually idles correctly (see plan's Verify, Step 4, items 1–4: import clean,
   submit a job JSON into `queue/inbox`, run worker once in foreground, it processes then idles).

### Step 7 — Implement Phase 5: hardening (plan §Phase 5)
- Add the `.env` retry knobs from plan Step 5.1 (`PIPE_RETRY_LIMIT=3` etc.).
- **Pin model caches (plan Step 5.2) — REQUIRED on this Mac:** storage is 256 GB total, and whisper's
  HF cache can balloon. Follow the plan's instructions to pin the cache dirs so nothing surprises the
  disk. Verify free space with `df -h /` before/after.

### Step 8 — End-to-end acceptance test (plan §10 checklist)
1. With the worker running, submit a real job: paste a real YouTube URL (any ~2–20 min video with
   speech) into a job JSON in `queue/inbox` (or use `stage.py`).
2. Wait for it to finish; confirm the job lands in `queue/done`.
3. Confirm N clips in `output/` (`short_01.mp4`, `short_02.mp4`, …), each:
   - `ffprobe` → 1080×1920 (Phase 3 pads to that) or ~9:16, with audio;
   - captions visible: extract a frame during a spoken word and confirm text appears near the bottom
     (e.g. `ffmpeg -ss <t> -i output/short_01.mp4 -frames:v 1 /tmp/f.png` and inspect);
   - duration ≤ ~60s.
4. Extract one frame and **show it to the user** to confirm captions look right.

### Step 9 — Commit & push
```bash
git add stage.py worker.py .env.example  # and any plist if it belongs in-repo (plan says it lives in ~/Library/LaunchAgents — keep out of git)
git commit -m "feat: Phase 4 worker + launchd 24/7 orchestration"
git push origin main
```
Also update the plan's **§9 changelog** rows for Phases 4/5 to ✅ with today's date and a 1-line
summary, and include that in the commit. Then confirm on GitHub:
`curl -s https://api.github.com/repos/Mitrakulal/YT_clips/commits/main` shows your new commit.

---

## 5. Pitfalls & environment notes (learned the hard way)

1. **OpenCV 5.x breaks the pipeline** — always verify `cv2.CascadeClassifier` exists after install;
   pin `opencv-python==4.10.0.84`. (On the dev machine this showed up as a misleading `[WinError 32]
   file in use`; root cause was the cascade import crash. On macOS expect a plain AttributeError at
   `clipper.py` line ~69 if unpinned.)
2. **Hermes shell environment:** if you run pipeline commands from inside a Hermes terminal and get
   weird import errors (pydantic/venv mismatch), the shell may pre-export `PYTHONPATH` pointing at
   Hermes' own venv. Fix: prefix commands with `PYTHONPATH=` (e.g. `PYTHONPATH= venv/bin/python main.py ...`).
3. **Ollama cold-load:** the first LLM call after the model was evicted takes ~20s (14B). If the
   worker's first job fails with a connection error, warm it: `curl -s http://localhost:11434/api/generate
   -d '{"model":"qwen3:14b","prompt":"warm","stream":false}'` then re-run.
4. **faster-whisper downloads the `small` model (~460 MB) from HuggingFace on first transcribe** — needs
   internet the first time; cached afterwards under `~/.cache/huggingface/`.
5. **Do not commit**: `.env`, `output/`, `venv/`, `test_videos/`, queue job payloads (or add them to
   `.gitignore` if not already).
6. **Do not modify** `shorts_generator/config.py`, `shorts_generator/local/llm.py`,
   `shorts_generator/local/transcriber.py`, or `subtitles.py` unless a bug is proven — they are
   committed and verified. Fix forward in new files.

## 6. Definition of done

- [ ] venv + deps OK, `cv2.CascadeClassifier` True
- [ ] `qwen3:14b` answers via `http://localhost:11434/v1`
- [ ] Phases 1–3 verified running on the Mac (srt + words.json + a clip produced)
- [ ] `stage.py` + `worker.py` exist, import clean
- [ ] LaunchAgent loaded; worker picks a job from inbox and moves it through done/failed
- [ ] Real YouTube URL → ≥1 finished subtitled clip, 1080×1920, captions visible
- [ ] Changelog §9 rows for Phases 4/5 marked ✅; committed and pushed; GitHub shows new HEAD

**If you hit a blocker:** fix the root cause, don't work around it. When stuck after honest attempts,
report back with the exact error, the step number, and what you tried — do NOT fake a pass.
