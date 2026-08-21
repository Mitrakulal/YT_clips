# Production Guide: Coherent Context-Preserving Clips

## What changed

The production pipeline now follows a **segmentation-first** design. It does not ask the language model to invent arbitrary timestamps. It first builds contiguous transcript candidates from ASR segments, sentence endings, pauses, and semantic boundaries. The language model then ranks those complete candidates by hook strength, payoff, emotion, and usefulness. The selected candidate’s original start and end are authoritative for rendering.

This directly addresses the main failure mode in the previous implementation: a model could identify an interesting sentence but choose a timestamp that started after the setup or ended before the conclusion. The new contract is that ranking may select a `candidate_id`, but it cannot change the candidate’s timestamps.

The design follows the principle used in established transcript-segmentation research: create logical, coherent passages first and select excerpts from those passages afterward. [1] Raw Whisper timing is also treated as approximate; forced alignment remains an optional future precision upgrade for cases where word timing must be exact. [2]

## Production flow

```text
source media
  -> faster-whisper transcript + word timestamps
  -> topic/pause boundary detection
  -> coherent candidate builder
       - sentence-like units
       - short-section merging
       - long-section splitting only at safe units
       - comedy/storytelling pause policy
  -> candidate-only LLM ranking
  -> non-overlapping selection
  -> vertical crop/reframe
  -> ASS captions + hook + loudness + 30 fps
  -> media validation and publish artifact
```

Both `python main.py --mode local ...` and `worker.py` now use the same candidate-first ranking contract. The worker also persists `boundaries.json`, `highlights.json`, `clips.json`, and `captioned.json`, so a restart can reload structured results instead of continuing with empty lists.

## Recommended local setup

Use a clean virtual environment and install the pinned local requirements:

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-local.txt
cp .env.example .env
```

The intended default is local Ollama:

```dotenv
LLM_PROVIDER=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen3:14b
OPENAI_API_KEY=ollama
LOCAL_WHISPER_MODEL=small
LOCAL_WHISPER_DEVICE=cpu
SEGMENTATION_SERVICE=auto
```

For production, keep `opencv-python==4.10.0.84`. The reframe path uses the Haar cascade API, and the repository’s deployment notes identify incompatibility risk with newer OpenCV major versions.

## Candidate sizing

The defaults are deliberately conservative:

| Setting | Default | Purpose |
|---|---:|---|
| `COHERENCE_MIN_SECONDS` | 12 | Prevents tiny fragments from becoming clips. |
| `COHERENCE_TARGET_SECONDS` | 45 | Preferred candidate size when a section is long. |
| `COHERENCE_MAX_SECONDS` | 120 | Hard context cap. |
| `SHORTS_MIN_SECONDS` | 8 | Final output floor. |
| `SHORTS_MAX_SECONDS` | 120 | Final output cap. |
| `PAUSE_BOUNDARY_SECONDS` | 1.2 | Pause signal for section boundaries. |
| `BOUNDARY_MIN_GAP_SECONDS` | 8 | Prevents interview turn-taking from over-fragmenting the source. |

A source shorter than the normal candidate minimum is admitted as one complete candidate when it is at least the final output floor. This prevents a valid short source from being rejected merely because the normal long-form candidate floor is 12 seconds.

## Content-specific policy

For tutorials, lectures, commentary, interviews, and most monologues, semantic and pause boundaries are useful section separators. For comedy and storytelling, a pause can be laughter or audience reaction inside a single beat. The candidate builder therefore removes hard boundary splitting for those content types after the classification pass, preserving setup → development → punchline/payoff → reaction as one candidate.

The model is instructed to reject greetings, filler, mid-thought fragments, and moments that require context outside the candidate. It receives neighboring text as context, but neighboring text is not included in the rendered clip unless it belongs to the candidate itself.

## Queue operation

Create the runtime directories and submit a job:

```bash
mkdir -p queue/inbox queue/active queue/done queue/failed jobs published logs
cat > queue/inbox/example.json <<'JSON'
{
  "job_id": "example",
  "source_url": "/absolute/path/to/video.mp4",
  "num_clips": 3,
  "aspect_ratio": "9:16",
  "format": "720"
}
JSON
```

Run the worker in the foreground during initial verification:

```bash
python worker.py
```

The job state lives in `jobs/example/state.json`. The worker’s stage artifacts are:

| Stage | Artifact |
|---|---|
| Download | source media path |
| Transcribe | `.words.json` |
| Segment | `boundaries.json` |
| Highlight | `highlights.json` with candidate metadata and effective boundaries |
| Crop | `clips.json` |
| Subtitle burn | `captioned.json` |
| Cleanup | `published/<job_id>/manifest.json` |

A stage is skipped only when its status is `done` and its artifact exists. This is important: a directory alone is not considered sufficient proof that a stage completed correctly.

## Acceptance criteria

A production run is accepted only when all of the following are true:

1. Every selected clip has a start and end copied from a coherent candidate, not directly from free-form model timestamps.
2. The clip begins at a transcript-unit boundary and does not end inside a candidate’s final sentence or payoff.
3. The selected clips do not overlap materially.
4. Each rendered file is a valid MP4 with video and audio, 1080×1920 output, 30 fps, and duration within the configured limits.
5. Caption burning succeeds and produces a captioned artifact; a job with zero captioned clips is failed rather than marked successful.
6. A simulated restart after crop or subtitle burn reloads persisted artifacts and still publishes the expected clips.
7. The worker and one-shot path produce the same candidate and boundary behavior for the same transcript/configuration.

## Remaining optional upgrade

If boundary timing is still visibly late or early on difficult recordings, add a forced-alignment stage after faster-whisper. WhisperX documents that raw Whisper timestamps can be inaccurate and that forced phoneme alignment improves word timing; it also provides diarization support for multi-speaker material. [2] This is a precision upgrade, not a substitute for the candidate-first design.

## References

[1]: https://aclanthology.org/J97-1003/ "Hearst, Text Tiling: Segmenting Text into Multi-paragraph Subtopic Passages"
[2]: https://arxiv.org/abs/2303.00747 "Bain et al., WhisperX: Time-Accurate Speech Transcription of Long-Form Audio"
