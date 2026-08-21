"""Output validation for production clip artifacts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict

from ..config import COHERENCE_MAX_SECONDS, OUTPUT_FPS


def probe_media(path: str) -> Dict:
    result = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
            "-of", "json", path,
        ],
        text=True,
    )
    return json.loads(result)


def expected_dimensions(aspect_ratio: str) -> tuple[int, int]:
    return (1080, 1080) if aspect_ratio == "1:1" else (1080, 1920)


def validate_clip(
    path: str,
    min_seconds: float = 1.0,
    max_seconds: float = COHERENCE_MAX_SECONDS,
    aspect_ratio: str = "9:16",
) -> Dict:
    clip = Path(path)
    if not clip.exists() or clip.stat().st_size <= 0:
        raise RuntimeError(f"clip artifact missing or empty: {path}")
    data = probe_media(str(clip))
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError(f"clip has no video stream: {path}")
    if not audio:
        raise RuntimeError(f"clip has no audio stream: {path}")
    target_width, target_height = expected_dimensions(aspect_ratio)
    if [video.get("width"), video.get("height")] != [target_width, target_height]:
        raise RuntimeError(f"clip is not {target_width}x{target_height}: {path}")
    fps = video.get("r_frame_rate")
    if fps != f"{OUTPUT_FPS}/1":
        raise RuntimeError(f"clip is not {OUTPUT_FPS} fps: {path} ({fps})")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if not min_seconds <= duration <= max_seconds + 0.25:
        raise RuntimeError(f"clip duration out of bounds: {path} ({duration:.2f}s)")
    return data
