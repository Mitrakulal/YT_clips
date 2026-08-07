"""Local clipping: ffmpeg subclip + OpenCV face-aware vertical crop.

Two stages per highlight:
  1. Cut the source video to [start, end] with ffmpeg (re-encoded, audio kept).
  2. Reframe the cut to the target aspect ratio. For 9:16 we slide a vertical
     window horizontally across the frame to keep faces centred (Haar
     cascade — same approach as the original repo, no external models).
"""
import os
import subprocess
from typing import Dict, List, Optional, Tuple

from ..config import (
    LOCAL_OUTPUT_DIR,
    SHORTS_MAX_SECONDS,
    SHORTS_MIN_SECONDS,
    DYNAMIC_ZOOM,
    ZOOM_MAX,
)


def _ratio(aspect_ratio: str) -> float:
    """Parse '9:16' → 9/16, '1:1' → 1.0."""
    try:
        w, h = aspect_ratio.split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return 9.0 / 16.0


# ---------------------------------------------------------------------------
# #2 Sentence-boundary trimming: snap a [start,end] window onto word timestamps
# so clips never open/close mid-word. Then enforce cap (60s) + floor (8s).
# ---------------------------------------------------------------------------
_EPS = 0.12  # seconds of tolerance when matching a neighbouring word boundary


def align_to_words(start: float, end: float, words: Optional[List[Dict]]) -> Tuple[float, float]:
    """Snap start to a word START and end to a word END on the source timeline.

    Falls back to the raw window when no word timestamps are available or the
    snapped window is degenerate (e.g. there is a long musical gap).
    """
    if not words:
        return start, end
    new_start, new_end = start, end
    for w in words:
        if w["start"] >= start - _EPS:
            new_start = w["start"]
            break
    for w in reversed(words):
        if w["end"] <= end + _EPS:
            new_end = w["end"]
            break
    new_start = max(start - _EPS, new_start)
    new_end = min(end + _EPS, new_end)
    if new_end - new_start < 1.0:  # degenerate (quiet gap) -> keep raw window
        return start, end
    return new_start, new_end


def enforce_limits(start: float, end: float, source_duration: Optional[float] = None) -> Tuple[float, float]:
    """Cap duration at SHORTS_MAX_SECONDS, raise it to SHORTS_MIN_SECONDS."""
    duration = end - start
    if duration > SHORTS_MAX_SECONDS:
        end = start + SHORTS_MAX_SECONDS
    if duration < SHORTS_MIN_SECONDS:
        extend = SHORTS_MIN_SECONDS - duration
        room = (source_duration - end) if source_duration else extend
        end = min(end + (extend if room >= extend else room or extend), (source_duration or end + extend))
    return start, end


def normalize_window(
    start: float,
    end: float,
    words: Optional[List[Dict]] = None,
    source_duration: Optional[float] = None,
) -> Tuple[float, float]:
    """Full window cleanup: sentence-boundary snap, then cap + floor."""
    start, end = align_to_words(start, end, words)
    start, end = enforce_limits(start, end, source_duration)
    return start, end


def _cut_subclip(source_path: str, start: float, end: float, out_path: str) -> str:
    """ffmpeg -ss start -to end → re-encoded mp4 with audio."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", source_path,
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path


def _reframe_vertical(in_path: str, out_path: str, aspect_ratio: str) -> str:
    """Crop the cut clip to the target aspect ratio, tracking faces if possible."""
    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "opencv-python is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    target_ratio = _ratio(aspect_ratio)
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {in_path}")

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    # Keep the SOURCE frame rate here so clip duration is preserved; the burn
    # stage converts to OUTPUT_FPS with a duration-preserving fps filter.
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Compute the largest crop that fits inside the frame at the target ratio.
    if target_ratio < src_w / src_h:
        crop_h = src_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = src_w
        crop_h = int(crop_w / target_ratio)
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    silent_path = out_path + ".silent.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(silent_path, fourcc, fps, (crop_w, crop_h))

    last_center: Optional[Tuple[int, int]] = None
    smoothing = 0.15  # how aggressively to chase a new face position
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        if len(faces) > 0:
            # Pick the largest face — usually the speaker.
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            cx = x + w // 2
            cy = y + h // 2
            if last_center is None:
                last_center = (cx, cy)
            else:
                lx, ly = last_center
                last_center = (
                    int(lx + (cx - lx) * smoothing),
                    int(ly + (cy - ly) * smoothing),
                )
        if last_center is None:
            last_center = (src_w // 2, src_h // 2)

        cx, cy = last_center
        x0 = max(0, min(src_w - crop_w, cx - crop_w // 2))
        y0 = max(0, min(src_h - crop_h, cy - crop_h // 2))
        cropped = frame[y0:y0 + crop_h, x0:x0 + crop_w]

        # #4 Dynamic zoom: slow push-in (Ken Burns) so frames are never static.
        if DYNAMIC_ZOOM:
            t = frame_idx / total_frames if total_frames > 1 else 1.0
            zf = 1.0 + ZOOM_MAX * min(1.0, t)  # 1.0 -> 1+ZOOM_MAX across the clip
            zoom_w = int(crop_w * zf)
            zoom_h = int(crop_h * zf)
            big = cv2.resize(cropped, (zoom_w, zoom_h), interpolation=cv2.INTER_LANCZOS4)
            bx0 = (zoom_w - crop_w) // 2
            by0 = (zoom_h - crop_h) // 2
            cropped = big[by0:by0 + crop_h, bx0:bx0 + crop_w]

        writer.write(cropped)
        frame_idx += 1

    cap.release()
    writer.release()

    # Mux audio from the cut clip back onto the silent reframed video.
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", silent_path,
        "-i", in_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0?",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    os.remove(silent_path)
    return out_path


def crop_clip_local(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
    words: Optional[List[Dict]] = None,
    source_duration: Optional[float] = None,
) -> str:
    """Cut + reframe one highlight, returning the local mp4 path.

    start_time/end_time are first passed through normalize_window: snapped to
    word boundaries (#2), capped at SHORTS_MAX_SECONDS and raised to
    SHORTS_MIN_SECONDS (#1).
    """
    start_time, end_time = normalize_window(start_time, end_time, words, source_duration)
    print(
        f"[clip/local] window {start_time:.2f}->{end_time:.2f}s "
        f"({end_time - start_time:.1f}s after normalize)",
        flush=True,
    )
    cut_path = out_path + ".cut.mp4"
    try:
        _cut_subclip(source_path, start_time, end_time, cut_path)
        _reframe_vertical(cut_path, out_path, aspect_ratio)
    finally:
        if os.path.exists(cut_path):
            os.remove(cut_path)
    return out_path


def crop_highlights_local(
    source_path: str,
    highlights: List[Dict],
    aspect_ratio: str = "9:16",
    out_dir: Optional[str] = None,
    words: Optional[List[Dict]] = None,
) -> List[Dict]:
    out_dir = out_dir or LOCAL_OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    source_duration = words[-1]["end"] if words else None
    results: List[Dict] = []
    for i, h in enumerate(highlights, 1):
        out_path = os.path.join(out_dir, f"short_{i:02d}.mp4")
        print(f"[clip/local] {i}/{len(highlights)}: {h.get('title', '(untitled)')}", flush=True)
        try:
            crop_clip_local(
                source_path,
                float(h["start_time"]),
                float(h["end_time"]),
                aspect_ratio,
                out_path,
                words=words,
                source_duration=source_duration,
            )
            results.append({**h, "clip_url": out_path})
        except Exception as e:
            print(f"[clip/local] {i} failed: {e}", flush=True)
            results.append({**h, "clip_url": None, "error": str(e)})
    return results
