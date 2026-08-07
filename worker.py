"""24/7 pipeline worker: file-queue in -> stage machine -> done/failed.

Design (LOCKED):
- queue/inbox/<job_id>.json : job spec {"source_url": ..., "num_clips": 3, ...}
- queue/active/<job_id>.json : in-flight (claimed via os.replace = atomic)
- queue/done|failed/        : terminal
- jobs/<job_id>/state.json  : per-stage state (source of truth)
- On boot: rescue any queue/active/* job (re-run unfinished stages).
- Idle: housekeeping (disk warn + publish pruning); heartbeat file touched.
- One bad job never kills the loop: every job runs inside try/except.
"""
import json
import os
import sys
import time
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from shorts_generator.highlights import get_highlights
from shorts_generator.local.clipper import crop_highlights_local, parse_srt, align_start_to_sentence, align_end_complete
from shorts_generator.local.downloader import download_youtube_local
from shorts_generator.local.llm import call_local_llm
from shorts_generator.local.transcriber import transcribe_local
from shorts_generator.config import DOWNLOAD_FORMAT, SUBTITLE_LANGUAGE, SHORTS_MAX_SECONDS, SHORTS_MIN_SECONDS
import subtitles
import stage as st

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
RETRY_LIMIT = 3


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)


def load_seen() -> set:
    if SEEN_PATH.exists():
        try:
            return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_seen(seen: set) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=1), encoding="utf-8")


def claim_next_job() -> Path:
    """Atomically move the first inbox job to active. Returns active path or None."""
    for inbox_path in sorted(INBOX.glob("*.json")):
        active_path = ACTIVE / inbox_path.name
        try:
            os.replace(inbox_path, active_path)
            return active_path
        except OSError:
            continue
    return None


def rescue_active() -> None:
    """Re-queue jobs stranded in active/ (worker crashed mid-job)."""
    for active_path in sorted(ACTIVE.glob("*.json")):
        job = json.loads(active_path.read_text(encoding="utf-8"))
        job_dir = JOBS / job["job_id"]
        state_path = job_dir / "state.json"
        if state_path.exists():
            state = st.load_state(state_path)
            if state.get("status") == "done":
                os.replace(active_path, DONE / active_path.name)
                continue
        os.replace(active_path, INBOX / active_path.name)
        log(f"rescued job {job.get('job_id')} -> inbox")


def classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if any(k in msg for k in ("downloaderror", "no segments", "private", "age-restricted",
                              "region", "invalid", "does not exist", "404")):
        return "permanent"
    return "transient"


def run_stage_download(job, state, job_dir):
    # Legacy jobs default "format": "480" in new_state; treat that as
    # "unspecified" and use the configured DOWNLOAD_FORMAT instead.
    fmt = state.get("format", DOWNLOAD_FORMAT)
    if not fmt or fmt == "480":
        fmt = DOWNLOAD_FORMAT
    source = download_youtube_local(job["source_url"], fmt=fmt)
    st.mark_stage(state, "download", "done", artifact=str(source))
    return source


def run_stage_transcribe(job, state, job_dir, source):
    transcript = transcribe_local(source, language=SUBTITLE_LANGUAGE)
    words_path = Path(source).with_suffix(".words.json")
    if not words_path.exists():
        # words.json lives next to the .srt cache in LOCAL_OUTPUT_DIR
        import glob
        candidates = sorted(Path("output").glob(f"*.words.json"))
        words_path = candidates[0] if candidates else None
    st.mark_stage(state, "transcribe", "done", artifact=str(words_path) if words_path else "")
    return transcript, words_path


def _dedupe_highlights(highlights, n, min_gap=3.0):
    """Greedy non-overlapping selection by score so the returned clips cover
    DISTINCT moments of the video (fixes 'the clips are repetitive'). Any pick
    that overlaps an already-kept window is skipped; picks are kept in timeline
    order."""
    picked = []  # list of (start, end)
    for h in sorted(highlights, key=lambda h: int(h.get("score", 0)), reverse=True):
        if not h.get("start_time") and not h.get("end_time"):
            continue
        s, e = float(h["start_time"]), float(h["end_time"])
        overlap = any(not (e <= ks - min_gap or s >= ke + min_gap) for ks, ke, _ in picked)
        if overlap:
            continue
        picked.append((s, e, h))
        if len(picked) >= n:
            break
    return [h for _, _, h in sorted(picked, key=lambda t: t[0])]


def _pad_highlights(highlights, n, transcript, max_dur=SHORTS_MAX_SECONDS):
    """Guarantee at least `n` clips. If the LLM under-produces (phi3 is
    notorious for this), fill the gap with density-ranked transcript segments so
    the user always gets the requested number of complete, distinct clips. Each
    fallback pick is extended to its complete span and kept non-overlapping."""
    hs = list(highlights)
    if not n or len(hs) >= n:
        return hs
    picked = [(float(h["start_time"]), float(h["end_time"])) for h in hs if h.get("start_time") is not None]
    segs = transcript.get("segments", []) if isinstance(transcript, dict) else []
    cands = []
    for seg in segs:
        s = align_start_to_sentence(float(seg["start"]), segs)
        e = align_end_complete(s, float(seg["end"]), segs, max_dur)
        if any(not (e <= ps - 3.0 or s >= pe + 3.0) for ps, pe in picked):
            continue
        text = seg.get("text", "")
        cands.append((len(text.split()), s, e, text))
    cands.sort(reverse=True)  # richest sentences first
    for _words_, s, e, text in cands:
        if len(hs) >= n:
            break
        if any(not (e <= ps - 3.0 or s >= pe + 3.0) for ps, pe in picked):
            continue  # overlaps an already-chosen clip
        title = " ".join(text.split())[:70]
        hs.append({"title": title or "Key Moment", "start_time": s, "end_time": e,
                   "score": 0, "reason": "density-fallback"})
        picked.append((s, e))
    return hs


def run_stage_highlight(job, state, job_dir, transcript):
    n = state.get("num_clips", 3)
    result = get_highlights(transcript, num_clips=n, llm_fn=call_local_llm)
    highlights = result.get("highlights", [])

    # Extend every candidate to its COMPLETE span (sentence-aligned start, end
    # carried to a natural pause) BEFORE dedupe, so overlap is measured on the
    # real windows that will be cropped — this is what keeps clips distinct.
    segments = transcript.get("segments", []) if isinstance(transcript, dict) else []
    pre = []
    for h in highlights:
        try:
            s = float(h.get("start_time", 0.0))
            e = float(h.get("end_time", s + SHORTS_MIN_SECONDS))
        except (TypeError, ValueError):
            continue
        if segments:
            s = align_start_to_sentence(s, segments)
            e = align_end_complete(s, e, segments, SHORTS_MAX_SECONDS)
        pre.append({**h, "start_time": s, "end_time": e})

    top = _dedupe_highlights(pre, n)   # distinct, non-overlapping moments
    top = _pad_highlights(top, n, transcript)  # guarantee at least n
    art = job_dir / "highlights.json"
    art.write_text(json.dumps({"highlights": top}, indent=2, ensure_ascii=False), encoding="utf-8")
    st.mark_stage(state, "highlight_llm", "done", artifact=str(art))
    return top


def _load_words(words_path: Optional[Path]) -> List:
    if words_path and words_path.exists():
        try:
            return json.loads(words_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def run_stage_crop(job, state, job_dir, source, top, words=None, segments=None):
    aspect = state.get("aspect_ratio", "9:16")
    shorts = crop_highlights_local(source, top, aspect_ratio=aspect, out_dir=str(job_dir), words=words, segments=segments)
    st.mark_stage(state, "crop", "done", artifact=str(job_dir))
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
                clip, words,
                float(short["start_time"]), float(short["end_time"]),
                captioned,
                hook_text=short.get("title"),
            )
            short["clip_url"] = captioned
        except Exception as e:
            short["clip_url"] = None
            short["error"] = f"subtitle_burn: {e}"
        final.append(short)
    st.mark_stage(state, "subtitle_burn", "done", artifact=str(job_dir))
    return final


def run_stage_cleanup(job, state, job_dir, source, final):
    # Keep only the captioned shorts; delete source + intermediates.
    if source and Path(source).exists() and Path(source).name.startswith("source_"):
        try:
            Path(source).unlink()
        except OSError:
            pass
    for p in job_dir.glob("*.cut.mp4"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in job_dir.glob("*.silent.mp4"):
        try:
            p.unlink()
        except OSError:
            pass
    published = PUBLISHED / job["job_id"]
    published.mkdir(parents=True, exist_ok=True)
    for short in final:
        src = short.get("clip_url")
        if src and Path(src).exists():
            shutil.copy2(src, published / Path(src).name)
    st.mark_stage(state, "cleanup", "done", artifact=str(published))
    return published


def run_job(active_path: Path, seen: set) -> bool:
    job = json.loads(active_path.read_text(encoding="utf-8"))
    job_id = job.get("job_id", active_path.stem)
    job["job_id"] = job_id
    job_dir = JOBS / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    state_path = job_dir / "state.json"

    state = st.load_state(state_path) if state_path.exists() else st.new_state(job)

    log(f"[job {job_id}] start {job.get('source_url', '')}")
    try:
        source = state.get("stages", {}).get("download", {}).get("artifact")
        if not st.stage_done(state, "download"):
            source = run_stage_download(job, state, job_dir)
            st.save_state(state_path, state)

        transcript = None
        words_path = None
        if not st.stage_done(state, "transcribe"):
            transcript, words_path = run_stage_transcribe(job, state, job_dir, source)
            st.save_state(state_path, state)
        else:
            words_path = Path(state["stages"]["transcribe"]["artifact"]) if state["stages"]["transcribe"].get("artifact") else None

        top = None
        if not st.stage_done(state, "highlight_llm"):
            if transcript is None:
                transcript = transcribe_local(source)
            top = run_stage_highlight(job, state, job_dir, transcript)
            st.save_state(state_path, state)
        else:
            art = state["stages"]["highlight_llm"].get("artifact")
            top = json.loads(Path(art).read_text(encoding="utf-8"))["highlights"] if art and Path(art).exists() else None

        shorts = None
        if not st.stage_done(state, "crop"):
            if top is None:
                raise RuntimeError("no highlights to crop")
            # Load word timestamps + sentence segments so the crop stage can snap
            # to full-sentence boundaries (complete thoughts, not mid-word cuts).
            w_art = state.get("stages", {}).get("transcribe", {}).get("artifact")
            crop_words = _load_words(Path(w_art) if w_art else None)
            crop_segments = []
            if w_art:
                srt = w_art[:-len(".words.json")] + ".srt" if w_art.endswith(".words.json") else None
                if srt and Path(srt).exists():
                    crop_segments = parse_srt(srt)
            shorts = run_stage_crop(job, state, job_dir, source, top, words=crop_words, segments=crop_segments)
            st.save_state(state_path, state)
        else:
            shorts = []

        final = None
        if not st.stage_done(state, "subtitle_burn"):
            final = run_stage_subtitles(job, state, job_dir, words_path, shorts)
            st.save_state(state_path, state)

        if not st.stage_done(state, "cleanup"):
            published = run_stage_cleanup(job, state, job_dir, source, final or shorts)
            st.save_state(state_path, state)

        state["status"] = "done"
        st.save_state(state_path, state)
        os.replace(active_path, DONE / active_path.name)
        seen.add(job.get("source_url", ""))
        save_seen(seen)
        log(f"[job {job_id}] DONE")
        return True
    except Exception as e:
        err_type = classify_error(e)
        state["error"] = f"{err_type}: {e}"
        state["status"] = "failed" if err_type == "permanent" else "error"
        st.save_state(state_path, state)
        os.replace(active_path, FAILED / active_path.name)
        log(f"[job {job_id}] FAILED ({err_type}): {e}")
        return err_type == "permanent"  # permanent failure counts as handled


def housekeeping() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.touch()
    try:
        usage = shutil.disk_usage(ROOT)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 20:
            log(f"WARN low disk: {free_gb:.1f} GB free")
        # Prune published/ to newest 50 entries
        entries = sorted(PUBLISHED.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True) if PUBLISHED.exists() else []
        for old in entries[50:]:
            shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    except Exception as e:
        log(f"housekeeping error: {e}")


def main() -> None:
    for d in (INBOX, ACTIVE, DONE, FAILED, JOBS, PUBLISHED, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
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
                    log(f"circuit breaker: {consecutive_failures} consecutive failures, sleeping 300s")
                    time.sleep(300)
                    consecutive_failures = 0
                continue
            housekeeping()
            time.sleep(IDLE_SLEEP)
        except KeyboardInterrupt:
            log("worker stopped by user")
            break
        except Exception as e:
            log(f"worker loop error: {e}; sleeping {IDLE_SLEEP}s")
            time.sleep(IDLE_SLEEP)


if __name__ == "__main__":
    main()
