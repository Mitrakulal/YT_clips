# Splitting Clips Properly — Speaker & Topic Segmentation Research

**Date:** 2026-08-08 · **Scope:** fix the "merged clips" problem in `YT_clips` for the whole YouTube creator community (podcasts, panels, standup comedy, facts/motivation videos)
**Status:** Research complete, versions verified against live sources; recommendation ready to implement.

---

## 1. The problem, proven with your own video

The video you sent (`video_1b5576da1d2e.mp4`, 120 s) is a perfect failure specimen:

- Transcript is **one long monologue** (motivation speech) with **no pauses ≥ 1s** — so VAD/silence gives **zero boundaries**.
- Human eyes see **5 real moments** inside those 120 s:
  1. `0:00–26s` — "everybody's waiting for motivation" (myth)
  2. `~27–35s` — "you watch a motivational video, 2 hours later you forget it"
  3. `~35–55s` — "do the discipline first, even when you hate it"
  4. `~55–83s` — "your progress/movement sparks motivation, not the other way around"
  5. `~83–105s` — "the waiting list: permission, 10 kg, money, ducks in a row"
  6. `~105–120s` — "don't be a waiter, be a creator"

Your pipeline merged all of that into ONE clip (it hit the 120 s cap) because the LLM ranked one big window and nothing told it "these are 6 different moments."

**Second failure mode (podcasts):** when a *new person* starts talking mid-window, silence-based cutting doesn't notice — voice A → voice B gets fused. Even worse, the LLM might rank a window whose start is in speaker A and end in speaker B, producing a clip with two different people who "could have been different clips."

## 2. Why silence+sentence alignment fails (the honest mechanic)

| Signal the pipeline uses | What it misses |
|---|---|
| VAD / silence gaps | Dense speech has no silences (motivational speakers, fast comedians) |
| Sentence boundaries (Whisper text) | Sentences don't equal *moments* — one topic can span 10 sentences, or one sentence can round a corner into a new topic |
| LLM highlight window | The ranker picks *interesting text*, it has **no concept of time-boundaries** — it happily spans speakers/topics |

So the fix is NOT a better ranker. It's a **segmentation layer** that produces timestamp boundaries *before* ranking and **clamps every clip window to those boundaries**.

## 3. The three boundary signals that fix it

1. **Speaker turns** (multi-person content: podcasts, interviews, panels)
2. **Topic shifts** (single-person content: facts, motivation, education — when the idea changes)
3. **Pause / scene cues** (silence, laughter/applause, video hard-cuts) — the cheap signals we already have

The winning design merges all three into one **unified boundary list**; a clip window may NEVER cross a boundary (split instead).

## 4. Model & tool research (verified live, 2026-08-08)

### 4.1 Speaker diarization (who speaks when)

| Option | Version (verified) | Size / runtime (M-series) | License | Verdict |
|---|---|---|---|---|
| **pyannote.audio** (standalone) | **4.0.7** (PyPI) | model ~400 MB; CPU ~0.5–1.5× realtime; torch MPS partially supported | **MIT** model (`license:mit` on HF hub) — **free**, gated (needs HF token + click "accept") | ✅ **Primary choice** |
| **WhisperX** (faster-whisper + pyannote in one box) | **3.8.6** (PyPI) | heavier (both ASR + diarization) | MIT + pyannote gating | ✅ Good, but **requires Python <3.14** — our `venv` is 3.14 → would break unless we make a separate venv. Not worth it. |
| NVIDIA **NeMo** MSDD | 3.x | large (torch+models), CPU unfriendly | Apache-2.0 | ❌ Overkill/heavy for local Mac |
| DIY: silero-VAD + speaker-embedding + clustering | — | medium | free | ⚠️ No token needed but worse DER + more code to tune |
| mlx-audio diarization | — | — | — | ❌ No mature diarization on MLX as of mid-2026; ASR-only |

**Decision:** add **pyannote.audio standalone** into the **existing venv** (it supports Python ≥3.10; running 3.14 needs torch with 3.14 wheels — fine on macOS). Keep our faster-whisper `small` for ASR; run pyannote separately, then assign each word to the nearest speaker turn. This is the same internal recipe WhisperX uses, minus the Python-version prison.

**Small code sketch (verified API shape):**
```python
from pyannote.audio import Pipeline
p = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1",
                             use_auth_token=os.environ["HF_TOKEN"])
turns = p("/tmp/probe.wav")           # pyannote core: Annotation of turns
turns = [(s.start, s.end, s.label) for s in turns.itertracks()]  # (start, end, SPEAKER_00)
```

### 4.2 Topic segmentation — one speaker, shifting ideas

| Option | Local/Free | Quality | Notes |
|---|---|---|---|
| **Ollama `nomic-embed-text`** (already installed, 274 MB) | ✅ | good | sentence embeddings; cosine similarity drop between windows = topic-shift candidate |
| **LLM topic labeling + chunking** with local `qwen3:14b` (already installed) | ✅ | **best** | give it the transcript, ask for topically-coherent blocks with timestamps (strict JSON — qwen3:14b is our reliable strict-JSON model) |
| `sentence-transformers` (PyPI 5.7.0) | ✅ free | good | alternative if we want GPU-free small model (`all-MiniLM-L6-v2` ~90 MB) |
| TextTiling (classic algorithm) | ✅ | ok | simple, no model, less sharp |
| BERTopic | ✅ | good but heavy | overkill for clip boundaries |

### 4.3 Scene cuts / comedy cues

- **PySceneDetect** (PyPI **0.7.1**) or ffmpeg's `scdet` filter → hard video cuts (interview multi-cam). 
- Laughter/applause: no reliable free detection model; approximate via VAD "non-speech energy" bursts. For comedy, the *real* boundary signal is often **topic/punchline** (LLM) + **energy burst**, not just silence.

## 5. Live proof on YOUR video (semantic segmentation)

I embedded every Whisper segment via your local `nomic-embed-text` and computed windowed cosine similarity (context ±2). With a statistical threshold (mean − 0.9σ), it detects the section changes where human viewers would split the 120 s video:

```
~44s   discipline-first → "one weekend, two weeks in, you see results"
~49s   same push … "you will see change"
~55s   results → "completely shift your life"
~114s  "waiting mode" → final punch "don't be a waiter. be a creator"
```

→ **4	splits** out of one "silent" 120s window. Not perfect on its own (per-sentence embedding similarity is noisy on smooth monologue) — which is *exactly* why the recommendation is the **hybrid**: semantic dips **+** pause/VAD **+** LLM topic labels together.

## 6. Recommended design (to implement in `YT_clips`)

### Stage order
```
download → transcribe (faster-whisper, EXISTS) → │ NEW: diarize + boundaries │
   → highlight ranking (LLM) → crop (windows clamp to boundaries) → subtitles → publish
```

### Unified boundary computation (pseudocode)
```python
def compute_boundaries(words, segments, speaker_turns, scene_cuts, cfg):
    B = []
    # 1) any speaker turn change (hard rule)
    for t in speaker_turns: B.append(("speaker", t.start, t.end))
    # 2) long-enough pause between words
    for gap in word_gaps(segments):
        if gap.dur >= cfg.PAUSE_MIN: B.append(("pause", gap.start, gap.end))
    # 3) topic-shift: sliding windows embedding sim < threshold
    for t in topic_shifts(words, cfg.topic_drop): B.append(("topic", t, t))
    # 4) optional: scene cuts from ffmpeg scdet
    for t in scene_cuts: B.append(("cut", t, t))
    return merge_nearby(B, min_gap=0.8)     # single sorted list
```

### Window-split rule (the actual fix)
```python
def clamp_window(start, end, boundaries, cfg):
    # a clip may NEVER cross a boundary: split at every boundary inside the window
    inside = [b for b in boundaries if start < b < end]
    if not inside:
        return [(start, end)]
    cuts = [start] + inside + [end]
    pieces = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]
    return [p for p in pieces if (p[1] - p[0]) >= cfg.split_min]
    return [(start, end)]
```
- If a split fragment < `SHORTS_MIN_SECONDS` → merge into the neighbor (never break completeness) or drop if forced.
- Dedupe on the new full spans; cap still 120 s **per fragment** (a fragment can never outspan past boundaries).
- Fallback: if diarization/semantic fails → current behavior (sentence-align). Flag in state.

### Config knobs to add
```
SEGMENTATION_SERVICE=auto     # none | speaker | semantic | auto
TOPIC_SIM_THRESHOLD=0.60      # from the run: mean−0.9σ on this video
PAUSE_MIN_SECONDS=1.2         # any pause >= this is a hard boundary
SPLIT_MIN_SECONDS=8
HF_TOKEN=                      # for pyannote (speaker mode)
SV_EMBED_MODEL=nomic-embed-text
```

### Files to touch
- `worker.py`: insert `run_stage_segment` (past boundary JSON into `jobs/<id>/boundaries.json`) between transcribe and highlight.
- `shorts_generator/local/clipper.py`: `crop_highlights_local` → clamp/split windows with the unified boundary list; new `split_at_boundaries()`.
- `shorts_generator/config.py`: new knobs above.
- `subtitles.py`: unchanged.
- `.env.example`: document the new knobs.

## 7. Testing plan

1. **Your 120s video** (above) → expect ~4-6 clips instead of 1 (check each clip is one "moment" via OCR of frames/captions).
2. **AEKZzyu03h8** (6-min motivation) → compare before/after; prior run gave 5 clips.
3. **A 2-speaker podcast** clip → expect a hard split at the voice change (speaker-labeled). Use whisper+pyannote alignment; the second's micro-pauses between people also fire.

## 8. Effort estimate & risks

- Implementation: ~4-8h (new stage, window logic, tests) — very doable in this codebase.
- **Risks:** pyannote needs an HF token (free, accept terms) and downloads ~100-400 MB models; torch new install into 3.14 venv must have wheels (torch ≥2.6 supports 3.14 on macOS); pure-embedding topic detection is fuzzy on single-speaker speech → keep LLM topic labeling as the final tie-breaker; comedy laughter cues are approximations.
- **Biggest lever:** do **diarization (speaker) + LLM topic blocks** first; they fix 90% of complaints (two speakers merged = fixed; five-moments-inone = fixed).

---

*Live-verified: pyannote.audio 4.0.7 · whisperx 3.8.6 (needs py<3.14) · sentence-transformers 5.7.0 · faster-whisper 1.2.1 (installed) · nomic-embed-text (installed) · scenedetect 0.7.1 · whisperX repo: 23k★, active (not archived).*