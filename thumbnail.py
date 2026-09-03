"""Thumbnail stage: grab the caption-visible frame and stamp a title band on it."""

import os
import subprocess
from typing import List, Optional

from shorts_generator.config import HOOK_SECONDS


def make_thumbnail(clip_path: str, title: Optional[str], out_path: str) -> str:
    """Extract a frame just after the hook beat and overlay the title as a
    bottom band (semi-transparent). The clip is already 1080x1920 at this
    point, so thumbnails share the upload dimensions."""
    grab_at = max(1.0, HOOK_SECONDS + 1.0)  # after the hook text clears
    frame_path = out_path + ".frame.jpg"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{grab_at:.2f}", "-i", clip_path,
            "-frames:v", "1", "-q:v", "3", frame_path,
        ],
        check=True,
    )
    text = " ".join((title or "").split())[:80]
    if text:
        _stamp_title(frame_path, text, out_path)
        if os.path.exists(frame_path):
            # _stamp_title consumes the frame on its Pillow-missing fallback
            # (os.replace into out_path); only clean up if still there.
            os.remove(frame_path)
    else:
        os.replace(frame_path, out_path)
    return out_path


def _wrap(text: str, width: int) -> List[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def _stamp_title(frame_path: str, text: str, out_path: str) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except ImportError:
        # No Pillow? Ship the plain frame rather than fail the pipeline.
        os.replace(frame_path, out_path)
        return
    img = Image.open(frame_path).convert("RGB")
    w, h = img.size
    band_h = int(h * 0.30)
    overlay = Image.new("RGBA", (w, band_h), (0, 0, 0, 160))
    img.paste(overlay, (0, h - band_h), overlay)
    draw = ImageDraw.Draw(img)
    font = _load_font(int(w * 0.055))
    chars_per_line = max(10, int(w / (int(w * 0.055) * 0.6)))
    lines = _wrap(text, chars_per_line)
    y = h - band_h + int(band_h * 0.12)
    for line in lines[:3]:
        draw.text((int(w * 0.05), y), line, fill=(255, 224, 0), font=font)
        y += int(w * 0.055) * 1.35
    img.save(out_path, "JPEG", quality=92)


def _load_font(px: int):
    for p in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(p):
            try:
                from PIL import ImageFont
                return ImageFont.truetype(p, px)
            except Exception:
                pass
    from PIL import ImageFont
    return ImageFont.load_default()


def thumbnail_stage(
    clip_path: str,
    title: Optional[str],
    out_path: str,
    enabled: bool = True,
) -> Optional[str]:
    """Stage wrapper — returns the thumb path or None when disabled."""
    if not enabled:
        return None
    try:
        return make_thumbnail(clip_path, title, out_path)
    except Exception as e:
        print(f"[thumb] failed for {clip_path}: {e}", flush=True)
        return None