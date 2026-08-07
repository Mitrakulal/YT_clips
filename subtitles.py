"""Subtitle burn-in stage: words.json -> ASS -> ffmpeg burn onto each short clip.

Inputs per highlight:
  - words:      list of {"start","end","word"} in the SOURCE video timeline
  - clip_start: source-time where the clip begins
  - clip_end:   source-time where the clip ends
  - clip_path:  the 9:16 mp4 produced by the crop stage
  - out_path:   where the captioned mp4 goes

The ASS file is rendered at 1080x1920; ffmpeg scales each clip into that canvas,
so caption positions are consistent across clips regardless of source resolution.
"""
import os
import subprocess
from typing import Dict, List

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,96,&H00FFFFFF,&H00FFE600,&H00141414,&H96000000,-1,0,0,0,100,100,0,0,1,3,0,2,40,40,220,1
"""


def _ts(seconds: float) -> str:
    """ASS timestamp H:MM:SS.cs (centiseconds)."""
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _escape(text: str) -> str:
    """Escape ASS tag braces so plain text renders literally."""
    return text.replace("{", "\\{").replace("}", "\\}")


def build_ass(words: List[Dict], clip_start: float, clip_end: float, out_path: str) -> str:
    """Group clip-relative words into <=8-word chunks; one static Dialogue line per chunk.

    (Static phrase captions, not karaoke pop — timing-exact and simple. Karaoke
    highlighting is a documented later upgrade, not part of this spec.)
    """
    rel = [w for w in words if clip_start <= w["start"] < clip_end]
    chunks = [rel[i:i + 8] for i in range(0, len(rel), 8)]
    body = []
    for chunk in chunks:
        if not chunk:
            continue
        start = max(0.0, chunk[0]["start"] - clip_start)
        end = chunk[-1]["end"] - clip_start
        text = _escape(" ".join(w["word"] for w in chunk))
        body.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{text}")
    events = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(body)
        + "\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER + events)
    return out_path


def burn_subtitles(clip_path: str, ass_path: str, out_path: str) -> str:
    """Burn the ASS onto the clip; canvas is padded to 1080x1920.

    ffmpeg's ass= filter parses ':' as an option separator, which breaks absolute
    Windows paths (C:\\dir\\f.ass). Running ffmpeg with cwd=ass dir and passing only
    the basename avoids colons in the filter graph entirely and works on both
    Windows and macOS (plain POSIX paths work there too).
    """
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", clip_path,
        "-vf", f"ass={os.path.basename(ass_path)},scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy",
        out_path,
    ]
    subprocess.run(cmd, check=True, cwd=os.path.dirname(os.path.abspath(ass_path)))
    return out_path


def subtitle_burn_stage(
    clip_path: str,
    words: List[Dict],
    clip_start: float,
    clip_end: float,
    out_path: str,
) -> str:
    """Build the ASS then burn it. ASS is kept next to the output for inspection."""
    ass_path = out_path + ".ass"
    build_ass(words, clip_start, clip_end, ass_path)
    burn_subtitles(clip_path, ass_path, out_path)
    return out_path
