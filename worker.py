"""Production queue worker for coherent, resumable local clip generation."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shorts_generator.config import (
    DOWNLOAD_FORMAT,
    SEGMENTATION_SERVICE,
    SUBTITLE_LANGUAGE,
)
from shorts_generator.highlights import get_highlights
from shorts_generator.local.clipper import crop_highlights_local, parse_srt
from shorts_generator.local.downloader import download_youtube_local
from shorts_generator.local.llm import call_local_llm
from shorts_generator.local.segment import compute_boundaries
from shorts_generator.local.transcriber import transcribe_local
from shorts_generator.local.validate import validate_clip
import stage as st
import subtitles

QUEUE = ROOT / "queue"
INBOX = QUEUE / "inbox"
ACTIVE = QUEUE / "active"
DONE = QUEUE / "done"
FAILED = QUEUE / "failed"
JOBS = ROOT / "jobs"
PUBLISHED = ROOT / "published"
SEEN_PATH = ROOT / "seen.json"
LOG_DIR = ROOT / "logs"
HEARTBEAT = LOG_DIR / "heartbeat"
INBOX_CAP = 20
MAX_CONSECUTIVE_FAILURES = 5
IDLE_SLEEP = 60


def log(msg: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def load_seen() -> set:
    if not SEEN_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(seen: set) -> None:
    tmp = SEEN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(seen), indent=1), encoding="utf-8")
    os.replace(tmp, SEEN_PATH)


def _read_json(path: Optional[Path], default):
    if not path or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value) -> Path:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def claim_next_job() -> Optional[Path]:
    for inbox_path in sorted(INBOX.glob("*.json")):
        active_path = ACTIVE / inbox_path.name
        try:
            os.replace(inbox_path, active_path)
            return active_path
        except OSError:
            continue
    return None


def rescue_active() -> None:
    for active_path in sorted(ACTIVE.glob("*.json")):
        job = _read_json(active_path, {})
        job_id = job.get("job_id", active_path.stem)
        state_path = JOBS / job_id / "state.json"
        state = st.load_state(state_path) if state_path.exists() else None
        if state and state.get("status") == "done":
            os.replace(active_path, DONE / active_path.name)
        else:
            os.replace(active_path, INBOX / active_path.name)
            log(f"rescued job {job_id} -> inbox")


def classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    permanent_markers = (
        "downloaderror", "no segments", "private", "age-restricted",
        "region", "invalid", "does not exist", "404", "no coherent transcript",
    )
    return "permanent" if any(marker in msg for marker in permanent_markers) else "transient"


def run_stage_download(job, state, job_dir):
    fmt = state.get("format") or DOWNLOAD_FORMAT
    source = download_youtube_local(job["source_url"], fmt=fmt)
    st.mark_stage(state, "download", "done", artifact=str(source))
    return source


def run_stage_transcribe(job, state, job_dir, source):
    transcript = transcribe_local(source, language=SUBTITLE_LANGUAGE)
    words_path = Path(source).with_suffix(".words.json")
    if not words_path.exists():
        candidates = sorted(Path("output").glob("*.words.json"))
        words_path = candidates[0] if candidates else None
    if not words_path:
        raise RuntimeError("word timestamp artifact was not produced")
    st.mark_stage(state, "transcribe", "done", artifact=str(words_path))
    return transcript, words_path


def run_stage_segment(job, state, job_dir, transcript):
    boundaries = compute_boundaries(transcript) if SEGMENTATION_SERVICE != "off" else []
    artifact = _write_json(job_dir / "boundaries.json", {"boundaries": boundaries})
    st.mark_stage(state, "segment", "done", artifact=str(artifact))
    log(f"[job {job['job_id']}] segmentation boundaries={len(boundaries)}")
    return boundaries


def run_stage_highlight(job, state, job_dir, transcript, boundaries):
    result = get_highlights(
        transcript,
        num_clips=int(state.get("num_clips", 3)),
        llm_fn=call_local_llm,
        boundaries=boundaries,
    )
    highlights = result.get("highlights", [])
    if not highlights:
        raise RuntimeError("candidate ranker returned zero coherent clips")
    artifact = _write_json(
        job_dir / "highlights.json",
        {
            "highlights": highlights,
            "content_info": result.get("content_info", {}),
            "candidates": result.get("candidates", []),
            "effective_boundaries": result.get("effective_boundaries", boundaries),
        },
    )
    st.mark_stage(state, "highlight_llm", "done", artifact=str(artifact))
    return highlights


def _load_words(words_path: Optional[Path]) -> List:
    value = _read_json(words_path, [])
    return value if isinstance(value, list) else []


def _segments_from_words_artifact(words_path: Optional[Path]) -> List[dict]:
    if not words_path:
        return []
    srt_path = Path(str(words_path).removesuffix(".words.json") + ".srt")
    return parse_srt(str(srt_path)) if srt_path.exists() else []


def run_stage_crop(job, state, job_dir, source, highlights, words, segments, boundaries):
    shorts = crop_highlights_local(
        source,
        highlights,
        aspect_ratio=state.get("aspect_ratio", "9:16"),
        out_dir=str(job_dir),
        words=words,
        segments=segments,
        boundaries=boundaries,
    )
    artifact = _write_json(job_dir / "clips.json", {"shorts": shorts})
    st.mark_stage(state, "crop", "done", artifact=str(artifact))
    return shorts


def run_stage_subtitles(job, state, job_dir, words_path, shorts):
    words = _load_words(words_path)
    final = []
    for short in shorts:
        clip = short.get("clip_url")
        if not clip:
            final.append(short)
            continue
        captioned = str(Path(clip).with_suffix("")) + "_captioned.mp4"
        try:
            subtitles.subtitle_burn_stage(
                clip,
                words,
                float(short["start_time"]),
                float(short["end_time"]),
                captioned,
                hook_text=short.get("title"),
            )
            validate_clip(captioned)
            short = {**short, "clip_url": captioned}
        except Exception as exc:
            short = {**short, "clip_url": None, "error": f"subtitle_burn: {exc}"}
        final.append(short)
    if not any(item.get("clip_url") for item in final):
        raise RuntimeError("subtitle stage produced no valid captioned clips")
    artifact = _write_json(job_dir / "captioned.json", {"shorts": final})
    st.mark_stage(state, "subtitle_burn", "done", artifact=str(artifact))
    return final


def run_stage_cleanup(job, state, job_dir, source, final):
    published = PUBLISHED / job["job_id"]
    published.mkdir(parents=True, exist_ok=True)
    copied = []
    for short in final:
        src = short.get("clip_url")
        if src and Path(src).exists():
            destination = published / Path(src).name
            shutil.copy2(src, destination)
            copied.append({"path": str(destination), "start_time": short.get("start_time"), "end_time": short.get("end_time"), "title": short.get("title")})
    if not copied:
        raise RuntimeError("cleanup found no captioned clips to publish")
    manifest = _write_json(published / "manifest.json", {"job_id": job["job_id"], "clips": copied})
    if source and Path(source).exists() and Path(source).name.startswith("source_"):
        Path(source).unlink(missing_ok=True)
    for pattern in ("*.cut.mp4", "*.silent.mp4"):
        for path in job_dir.glob(pattern):
            path.unlink(missing_ok=True)
    st.mark_stage(state, "cleanup", "done", artifact=str(manifest))
    return published


def run_job(active_path: Path, seen: set) -> bool:
    job = _read_json(active_path, {})
    job_id = job.get("job_id", active_path.stem)
    job["job_id"] = job_id
    job_dir = JOBS / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state_path = job_dir / "state.json"
    state = st.load_state(state_path) if state_path.exists() else st.new_state(job)

    log(f"[job {job_id}] start {job.get('source_url', '')}")
    try:
        source = state["stages"].get("download", {}).get("artifact")
        if not st.stage_done(state, "download"):
            source = run_stage_download(job, state, job_dir)
            st.save_state(state_path, state)

        transcript = None
        words_path = None
        if not st.stage_done(state, "transcribe"):
            transcript, words_path = run_stage_transcribe(job, state, job_dir, source)
            st.save_state(state_path, state)
        else:
            words_path = Path(state["stages"]["transcribe"]["artifact"])
            transcript = transcribe_local(source, language=SUBTITLE_LANGUAGE)

        if not st.stage_done(state, "segment"):
            boundaries = run_stage_segment(job, state, job_dir, transcript)
            st.save_state(state_path, state)
        else:
            segment_art = Path(state["stages"]["segment"]["artifact"])
            boundaries = _read_json(segment_art, {}).get("boundaries", [])

        if not st.stage_done(state, "highlight_llm"):
            highlights = run_stage_highlight(job, state, job_dir, transcript, boundaries)
            st.save_state(state_path, state)
        else:
            highlight_art = Path(state["stages"]["highlight_llm"]["artifact"])
            highlight_data = _read_json(highlight_art, {})
            highlights = highlight_data.get("highlights", [])
            boundaries = highlight_data.get("effective_boundaries", boundaries)
            if not highlights:
                raise RuntimeError("highlight artifact is empty")

        if not st.stage_done(state, "crop"):
            words = _load_words(words_path)
            segments = _segments_from_words_artifact(words_path)
            shorts = run_stage_crop(job, state, job_dir, source, highlights, words, segments, boundaries)
            st.save_state(state_path, state)
        else:
            clips_art = Path(state["stages"]["crop"]["artifact"])
            shorts = _read_json(clips_art, {}).get("shorts", [])
            if not shorts:
                raise RuntimeError("crop artifact is empty")

        if not st.stage_done(state, "subtitle_burn"):
            final = run_stage_subtitles(job, state, job_dir, words_path, shorts)
            st.save_state(state_path, state)
        else:
            caption_art = Path(state["stages"]["subtitle_burn"]["artifact"])
            final = _read_json(caption_art, {}).get("shorts", [])
            if not final:
                raise RuntimeError("caption artifact is empty")

        if not st.stage_done(state, "cleanup"):
            run_stage_cleanup(job, state, job_dir, source, final)
            st.save_state(state_path, state)

        state["status"] = "done"
        st.save_state(state_path, state)
        os.replace(active_path, DONE / active_path.name)
        seen.add(job.get("source_url", ""))
        save_seen(seen)
        log(f"[job {job_id}] DONE")
        return True
    except Exception as exc:
        kind = classify_error(exc)
        state["error"] = f"{kind}: {exc}"
        state["status"] = "failed" if kind == "permanent" else "error"
        st.save_state(state_path, state)
        os.replace(active_path, FAILED / active_path.name)
        log(f"[job {job_id}] FAILED ({kind}): {exc}")
        return kind == "permanent"


def housekeeping() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.touch()
    try:
        free_gb = shutil.disk_usage(ROOT).free / (1024 ** 3)
        if free_gb < 20:
            log(f"WARN low disk: {free_gb:.1f} GB free")
        entries = sorted(PUBLISHED.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if PUBLISHED.exists() else []
        for old in entries[50:]:
            shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    except Exception as exc:
        log(f"housekeeping error: {exc}")


def main() -> None:
    for directory in (INBOX, ACTIVE, DONE, FAILED, JOBS, PUBLISHED, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    rescue_active()
    consecutive_failures = 0
    log("worker started")
    while True:
        try:
            active_path = claim_next_job()
            if active_path is not None:
                handled = run_job(active_path, seen)
                consecutive_failures = 0 if handled else consecutive_failures + 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log("circuit breaker: sleeping 300s")
                    time.sleep(300)
                    consecutive_failures = 0
                continue
            housekeeping()
            time.sleep(IDLE_SLEEP)
        except KeyboardInterrupt:
            log("worker stopped by user")
            break
        except Exception as exc:
            log(f"worker loop error: {exc}; sleeping {IDLE_SLEEP}s")
            time.sleep(IDLE_SLEEP)


if __name__ == "__main__":
    main()
