# Production Validation Report

**Commit:** `4a054cd788d80b4598fb42e8b9966a37514bc0ec`  
**Repository:** `Mitrakulal/YT_clips`  
**Status:** validated and pushed to `main`

## Acceptance results

| Check | Result |
|---|---|
| Python compilation | Passed with `python3 -m compileall -q .` |
| Unit/regression tests | **6 passed** |
| Candidate-only ranking | Passed; model-supplied timestamps were ignored and the persisted candidate span was used. |
| Short leading/middle/trailing fragment handling | Passed; no short sliver remained in the regression cases. |
| Comedy/storytelling pause policy | Passed; internal pause boundaries were not used to split the complete beat. |
| Worker restart recovery | Passed; persisted crop and caption artifacts were reloaded and published after simulated restart. |
| Synthetic media render | Passed; output contained video and audio, was 1080×1920, 30 fps, and 14.5 seconds. |
| Real ASR media render | Passed using faster-whisper `tiny.en` on a speech sample; 2 transcript segments and 22 word timestamps were produced, then a valid 1080×1920, 30 fps, audio-bearing 10.5-second captioned MP4 was rendered. |
| CLI/import smoke checks | Passed. |
| GitHub attribution | Passed; commit author is `Mitrakulal <kulalmitra@gmail.com>` and GitHub linked it to the `Mitrakulal` account. |

## What is now guaranteed by code

The model ranks coherent candidate IDs rather than arbitrary timestamps. Candidate spans are contiguous transcript units built before ranking, and the selected span is authoritative for rendering. The worker and one-shot path use the same candidate-first logic. Stage artifacts are persisted as JSON files and must exist before a stage is skipped. Output validation rejects missing audio/video, wrong dimensions, wrong frame rate, and duration violations before publication.

## Known boundary of this validation

The test environment did not run a real Ollama ranking call or a full YouTube download. The ranking interface was exercised with deterministic fake model responses, while faster-whisper, FFmpeg, OpenCV, subtitle burn-in, and FFprobe were exercised with real media. A production Mac run should still verify the local Ollama model name and `ffmpeg` libass support once before processing live sources.
