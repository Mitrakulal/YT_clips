# ba2cbab6 — AEKZzyu03h8 "MORNING MOTIVATION" (6:00)

5 requested clips → **3 distinct, complete clips** delivered (content-limited:
this 6-min monologue has ~3 natural sections).

## Clips (1080x1920 · 30fps · ~-14 LUFS)
| # | File | Window (s) | Duration | Note |
|---|------|-----------|----------|------|
| 1 | short_01_captioned.mp4 | 0.00 – 120.00 | 120s (cap) | "The Secret to Motivation (No, It's Not Waiting for It)" |
| 2 | short_02_captioned.mp4 | 271.22 – 351.64 | 80.4s | "The Fear That Outweighed My Comfort Zone" — ends at closing line |
| 3 | short_03_captioned.mp4 | 125.80 – 245.80 | 120.0s | Discipline section (density fallback) |

- **Complete endings**: `align_end_complete` extends the end to a natural pause
  (gap >= 0.8s) up to the 120s cap — cuts land on finished thoughts.
- **Distinct clips**: dedupe runs on *extended* spans; crop only clamps
  (never re-aligns) so picks can't collide with slightly-different .srt splits.
- Real formation of 5 excluded (video lacks a 5th non-overlapping section).

## Pipeline changes this run (committed with this sample)
- `shorts_generator/config.py`: `SHORTS_MAX_SECONDS=120` (user: ≤2min), floor
  reaches MIN by pulling START back — never breaks the ending.
- `shorts_generator/local/clipper.py`: `align_start_to_sentence` +
  `align_end_complete`; `crop_clip_local` now CLAMPS only (trusts highlights.json
  window) instead of re-extending.
- `worker.py`: extend candidates to complete spans before dedupe
  (`_dedupe_highlights`), `_pad_highlights` overlap-guard on growing picked list.
- `subtitles.py`: bolder caption style (thin outline, tighter 4-word/32-char
  chunks, single clean line).
- Ranker: `OPENAI_MODEL=qwen3:14b` (reliable strict-JSON; phi3 flaky).