# AutoClip comparison decision record

**Decision:** Keep the YT_clips coherence-first pipeline as the clipping core. Do not replace it with AutoClip’s outline → LLM-timeline → scoring flow. Adopt only product-level ideas that improve local usability without weakening context preservation or local privacy.

## Evidence reviewed

This comparison is based on AutoClip’s English project documentation, its current repository metadata, and the implementation of its outline, timeline, scoring, video-generation, processing-task, and desktop configuration modules. The review distinguishes documented capabilities from inspected code paths.

| Dimension | AutoClip | Local YT_clips | Decision |
|---|---|---|---|
| Core clip selection | Builds a 30-minute SRT chunk outline, then asks an LLM to return topic `start_time` and `end_time` values. It validates timestamp syntax and clamps values to a chunk boundary. [1] [2] | Builds contiguous sentence-like transcript units, applies safe topic/pause boundaries, merges short fragments, and makes the model select only a pre-built `candidate_id`. The model cannot create or move timestamps. | **Keep YT_clips.** This directly addresses the random-start/random-end failure that motivated this project. |
| Story and comedy context | The inspected timeline path does not show sentence-completion, setup/payoff, or laughter-reaction boundary protection. [2] | Explicitly preserves setup → payoff/reaction beats and disables hard pause splits for comedy/storytelling candidates. | **Keep YT_clips.** It is better matched to the required output quality. |
| Output acceptance | The inspected video step invokes batch extraction and reports successful paths; no output-media probe is shown there. [3] | Finished clips are captioned, then validated for video/audio streams, duration, 30 fps, and correct 9:16 or 1:1 canvas before appearing in the dashboard. | **Keep YT_clips.** Validation is a non-negotiable safety gate. |
| Local job persistence | FastAPI, Celery, Redis, SQLite, and project/task entities provide a scalable multi-process design. [1] [4] | A single local WSGI process with SQLite, transactional job claiming, versioned migrations, per-job artifacts, logs, and one-at-a-time execution. | **Keep YT_clips for a Mac Mini.** Celery and Redis add operational overhead without increasing clip quality for one local worker. |
| Live interface | React/TypeScript interface, project management, collections, and a Tauri desktop shell. [1] [5] | Local browser dashboard with submission, exact stage tracking, live log polling, job history, results, and retry. | **Defer Tauri packaging.** It is a usability improvement, not a clipping-quality improvement. |
| Input breadth | YouTube, Bilibili, and local-file upload are documented. [1] | YouTube is the current dashboard input, while the pipeline itself can already accept local file paths. | **Defer local-file upload UI.** It is the highest-value future enhancement after live acceptance testing. |
| Privacy posture | The current repository includes a PostHog-related frontend commit, but this review did not verify its runtime configuration. [6] | Dashboard binds to `127.0.0.1`; job data and assets stay under local `studio_data/`; local result URLs use a private HMAC secret. | **Do not adopt AutoClip wholesale.** The local-only privacy promise must remain the governing constraint. |

## Accepted, deferred, and rejected ideas

| Idea | Status | Rationale and acceptance check |
|---|---|---|
| Project-level artifact folders and task history | **Already present** | Each YT_clips job has its own local directory, SQLite state, logs, and validated result records. Regression tests cover state creation, retry, and migration behavior. |
| Duplicate-work protection | **Already present** | The local worker claims one queued job transactionally and processes one job at a time; this avoids concurrent media/model contention on the Mac Mini. |
| Local file-upload / drag-and-drop input | **Deferred** | Valuable for privacy and testing, but it does not solve the clip-boundary problem. Add only with a file-size policy and integration tests that confirm no source file leaves the Mac. |
| Editable title, hook, and trim-review interface | **Deferred** | This is a useful human-in-the-loop quality layer. It should operate on candidate boundaries and preserve the existing validation gate. |
| Tauri desktop packaging | **Deferred** | A native launcher could simplify operation, but the current `start_studio.command` and optional launch agent are already reliable. Packaging should follow real user acceptance testing, not precede it. |
| Celery + Redis + multi-worker concurrency | **Rejected for this deployment** | It adds services, memory use, recovery complexity, and potential GPU/model contention. The local Mac Mini requirement benefits from serial processing rather than distributed concurrency. |
| LLM-selected free-form start/end timestamps | **Rejected** | This is the exact mechanism most likely to reintroduce cut-off context. YT_clips must remain candidate-first. |
| Collections, upload automation, and Bilibili account management | **Deferred / out of scope** | These features do not improve the current personal, local-only YouTube-to-clips workflow. |

## Final assessment

AutoClip is **broader and more polished as a general desktop product**, particularly for multi-platform sources, project organization, collections, and possible native-app distribution. It is **not better for the specific failure mode that mattered most here**: producing coherent clips with proper beginnings and endings.

For clip quality, the current YT_clips pipeline is the stronger foundation because safe transcript candidates are constructed before ranking, their spans are preserved through rendering, and final media is validated. The correct next step is to run several real videos through the local dashboard, score the outputs manually for context completeness, and then add a review/edit layer only where real failure patterns remain.

## Validation trace for the no-replacement decision

The decision not to replace the local YT_clips pipeline with AutoClip’s free-form timeline flow is backed by the following committed checks. These tests and fixtures are the acceptance evidence for preserving the current local-first architecture.

| Required property | Existing evidence | What it proves |
|---|---|---|
| Local-only job persistence and signed asset access | `tests/test_local_studio.py` | Local SQLite job creation, exact stage records, retry reset behavior, HMAC-signed local media URLs, and versioned migration safety are tested without cloud storage. |
| Candidate-first, context-safe ranking | `tests/test_production_quality.py` | The ranker ignores model timestamps in favor of built candidates; short fragments merge; and comedy pauses stay inside the full setup/punchline/reaction candidate. |
| Pipeline bridge parity | `tests/test_pipeline_progress_bridge.py` | The local dashboard bridge emits `downloading → transcribing → segmenting → ranking → cropping → captioning` in order and routes artifacts into a job-specific directory. |
| Final media quality | `tests/render_fixture.py` and `tests/render_square_fixture.py` | Real FFmpeg fixtures validate captioned 30 fps video with audio at 1080×1920 and 1080×1080 before a result can be published. |

No AutoClip-style pipeline change is accepted at this point because it would remove one or more of these protections without providing evidence of improved context completeness.

## References

[1] [AutoClip English README](https://github.com/zhouxiaoka/autoclip/blob/main/.github/README-EN.md)

[2] [AutoClip timeline extractor](https://raw.githubusercontent.com/zhouxiaoka/autoclip/main/backend/pipeline/step2_timeline.py)

[3] [AutoClip video generator](https://raw.githubusercontent.com/zhouxiaoka/autoclip/main/backend/pipeline/step6_video.py)

[4] [AutoClip processing task](https://raw.githubusercontent.com/zhouxiaoka/autoclip/main/backend/tasks/processing.py)

[5] [AutoClip Tauri configuration](https://raw.githubusercontent.com/zhouxiaoka/autoclip/main/src-tauri/tauri.conf.json)

[6] [AutoClip current repository activity](https://github.com/zhouxiaoka/autoclip)
