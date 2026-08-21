# Cutroom Local Studio

`local_studio.py` is the personal, fully local interface for YT_clips. It is designed for a Mac Mini and binds to `127.0.0.1` by default. It is not deployed, does not expose a public web interface, and does not send job records, clips, downloads, transcripts, or logs to cloud storage.

## What stays on the Mac

The application stores the SQLite job database, a private HMAC signing secret, job logs, downloaded source files, transcript caches, crop intermediates, thumbnails, captioned MP4s, and result metadata inside `studio_data/` in the repository. Versioned SQL files in `migrations/` update the local database safely at application startup. Finished clip links are signed local URLs with a six-hour expiry. They are served only by the local dashboard and do not use a public bucket.

## One-time setup

Install the local prerequisites on the Mac Mini. You need Python 3, FFmpeg with `libass`, Ollama with the selected local ranking model, and the repository dependencies.

```bash
brew install python ffmpeg
ollama pull qwen3:14b
cd /path/to/YT_clips
chmod +x start_studio.command
./start_studio.command
```

The first launch creates `.venv`, installs the requirements, starts the local worker, and opens:

```text
http://127.0.0.1:8765
```

Use `Control+C` in the terminal to stop it. The next launch reuses the environment and preserves prior jobs in `studio_data/`.

## Optional automatic startup

After you have confirmed that the dashboard opens correctly once, double-click `install_local_service.command` in Finder or run it in Terminal. It creates a macOS user launch agent that starts the local dashboard after you sign in and restarts it if it exits. The dashboard remains bound to `127.0.0.1`, so it is reachable only from the Mac Mini.

Use `uninstall_local_service.command` if you later want to remove automatic startup.

## Job flow

Every submission creates one persisted SQLite job. The interface uses these exact status names:

```text
queued → downloading → transcribing → segmenting → ranking → cropping → captioning → done
```

Any exception marks the job `failed`, records the active error stage and error text, and leaves a complete log tail in the job detail view. Selecting **Retry from beginning** re-queues the same source and settings from the start with one click.

The worker processes one job at a time. This avoids simultaneous model loading and media encoding on the Mac Mini, which improves stability and prevents mixed job artifacts.

## Results safety

Before a clip is added to a completed job, the pipeline validates that it is an MP4 with video and audio, the expected vertical dimensions, the configured frame rate, and a valid duration. The results viewer displays only clips that pass validation.

## Files and recovery

| Path | Purpose |
|---|---|
| `studio_data/studio.sqlite3` | Local job history, stage state, logs, and clip metadata. |
| `studio_data/.local-url-secret` | Private signing secret for local asset URLs. Keep this private. |
| `studio_data/jobs/<job-id>/` | All job-specific source, transcript, clip, thumbnail, and caption artifacts. |
| `start_studio.command` | One-command startup for the local dashboard and worker. |

If the app is closed while a job is running, simply launch it again. A queued job is picked up automatically. A job that was interrupted while active is recorded as its last completed state; for a clean full restart, use the retry control in the interface.
