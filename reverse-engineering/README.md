# AI YouTube Shorts Generator — Reverse Engineering & Implementation Pack

Target repo (the reference, keep as-is except the 4 small edits in the plan):
**`SamurAIGPT / AI-Youtube-Shorts-Generator`**

Goal: turn the repo's `--mode local` into a **fully-automated, zero-API-cost** shorts pipeline
(YouTube URL in → finished, word-level-captioned vertical clips out), running on a Mac Mini
(Apple Silicon, 16 GB, 256 GB) using local Ollama for the LLM step. Instagram/YouTube upload is
**manual** in the committed scope; the publish automation is documented as an optional Phase 6.

---

## Files in this pack

| File | What it is |
|---|---|
| `01-RESEARCH-REPORT.md` | Findings: what the pipeline does, the 3 code changes, free LLM swap, orchestration, storage, Instagram posting options, copyright + account-health realities. |
| `02-IMPLEMENTATION-PLAN.md` | **Zero-guess phased spec** — an executing agent (Claude, Cursor, Codex, Copilot) can run top-to-bottom with no guessing. Every file, every exact code block, every verify command is given. |

## How to use this pack

- Read `01` for context/decisions (research, trade-offs, "why").
- Execute `02` phase-by-phase. Each phase = exact files → exact code → exact verify command →
  expected output. LOCKED items are not to be re-litigated; `<your discretion>` items are free to tweak.
- The cloned repo stays as reference; only these files are modified/added (documented in `02 §0.2`):
  - `shorts_generator/config.py`
  - `shorts_generator/local/llm.py`
  - `shorts_generator/local/transcriber.py`
  - new: `subtitles.py`, `stage.py`, `worker.py` (repo root)
  - new: `reverse-engineering/` (this pack)

---

### Verified sources (primary, fetched live Aug 2026 — nothing fabricated)
- Ollama OpenAI-compat: `https://docs.ollama.com/api/openai-compatibility`
- faster-whisper word timestamps: `https://github.com/SYSTRAN/faster-whisper` (README §"Word-level timestamps")
- mlx-whisper: `https://github.com/ml-explore/mlx-examples/blob/main/whisper/README.md`
- whisperX: `https://github.com/m-bain/whisperX`
- qwen3:14b size (9.3 GB): `https://ollama.com/library/qwen3`
- Meta Content Publishing, App Review, platform overview (archived 2024-2026)
- Buffer & Later pricing pages (live); Postiz + instagrapi READMEs (live, master)

### Flagged as unverified / to confirm at runtime (do not treat as locked)
- Exact word-timestamp WER of `faster-whisper` vs `whisperX`
- `large-v3` CPU RAM/speed estimates (~3.5-4 GB RAM, ~3-5× realtime)
- qwen3 `think: false` behavior on your specific Ollama build (verify with the Phase 1 test)
- Whether Instagram Login publishing is enabled in your region (only relevant if you do Phase 6)