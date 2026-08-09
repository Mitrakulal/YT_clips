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
import re
import subprocess
from typing import Dict, List, Optional

from shorts_generator.config import (
    KEYWORD_EMPHASIS,
    HOOK_TEXT,
    HOOK_SECONDS,
    HOOK_FONT_SIZE,
    LOUDNESS_FILTER,
    OUTPUT_FPS,
    KARAOKE,
)

# Keyword highlight colour (ASS AARRGGBB) — bright yellow, pops off the picture.
_KEYWORD_COL = "&H00FFDC00&"

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,84,&H00FFFFFF,&H00FFE600,&H00000000,&H90000000,-1,0,0,0,100,100,0.6,0,1,4,1,2,40,40,230,1
Style: Hook,Arial,__HOOKSIZE__,&H00FFFFFF,&H0000FFC8,&H00000000,&HB0000000,-1,0,0,0,100,100,1,0,4,2,0,5,60,60,0,1
"""

_STOPWORDS = {
    "the", "and", "that", "this", "with", "you", "your", "for", "have", "are",
    "was", "but", "not", "what", "how", "can", "will", "from", "they", "them",
    "there", "because", "going", "just", "know", "think", "really", "very",
}


def _keyword_tokens(word: str) -> Optional[str]:
    """Return a lowercased keyword token if it's a meaningful English word."""
    tok = re.sub(r"[^A-Za-z']", "", word or "").strip("'").lower()
    if len(tok) < 4 or not tok.isascii() or tok in _STOPWORDS:
        return None
    return tok


def _highlight_tokens(words_tokens: List[str], keywords: set) -> List[str]:
    """Return per-token ASS-safe text with keywords wrapped in a colour override."""
    out = []
    for tok in words_tokens:
        key = _keyword_tokens(tok)
        if KEYWORD_EMPHASIS and key and key in keywords:
            out.append("{\\c%s}%s{\\c}" % (_KEYWORD_COL, _escape(tok)))
        else:
            out.append(_escape(tok))
    return out


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


def build_ass(
    words: List[Dict],
    clip_start: float,
    clip_end: float,
    out_path: str,
    hook_text: Optional[str] = None,
) -> str:
    """Group clip-relative words into <=8-word / <=40-char chunks; one Dialogue
    line per chunk. Keywords matching the hook/title are colour-emphasised (#7).
    If hook_text is set and HOOK_TEXT is on, a big bold hook line is burned over
    the first HOOK_SECONDS of the clip (#3).
    """
    header = ASS_HEADER.replace("__HOOKSIZE__", str(HOOK_FONT_SIZE))

    keywords: set = set()
    if KEYWORD_EMPHASIS and hook_text:
        keywords = {k for k in (_keyword_tokens(w) for w in hook_text.split()) if k}

    rel = [w for w in words if clip_start <= w["start"] < clip_end]
    chunks: List[List[Dict]] = []
    cur: List[Dict] = []
    cur_len = 0
    for w in rel:
        wlen = len(w["word"])
        if cur and (len(cur) >= 6 or cur_len + wlen > 32):
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(w)
        cur_len += wlen + 1
    if cur:
        chunks.append(cur)

    events = []
    for chunk in chunks:
        if not chunk:
            continue
        start = max(0.0, chunk[0]["start"] - clip_start)
        end = chunk[-1]["end"] - clip_start
        if KARAOKE:
            # Word-by-word pop (Hormozi style), built WITHOUT libass {\k} fills —
            # those silently don't render on many ffmpeg builds. Instead, emit
            # one Dialogue per word: the newest word is yellow, prior words stay
            # white, so the accent visibly sweeps across the phrase. Deterministic.
            window = chunk
            for i, w in enumerate(window):
                w_start = max(0.0, w["start"] - clip_start)
                if i + 1 < len(window):
                    w_end = max(w_start + 0.15, window[i + 1]["start"] - clip_start)
                else:
                    w_end = max(w_start + 0.25, end)
                parts = []
                for j, pw in enumerate(window[: i + 1]):
                    seg = _escape(pw["word"])
                    if j == i:
                        seg = "{\\c&H00FFE6&}" + seg + "{\\c&HFFFFFF&}"
                    parts.append(seg)
                events.append(
                    f"Dialogue: 0,{_ts(w_start)},{_ts(w_end)},Caption,,0,0,0,,{' '.join(parts)}"
                )
        else:
            text = " ".join(_highlight_tokens([w["word"] for w in chunk], keywords))
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{text}")

    # Don't stack a caption under the hook title during the opening beat —
    # this was what was producing the doubled-subtitle look (#3). Only drop
    # hook-zone captions when there is a REAL beat after the hook (>=2 events
    # or >1s of caption time beyond it); otherwise (short 1-6s highlights) the
    # clip would lose almost everything and look mute for its first seconds.
    if HOOK_TEXT and hook_text and events:
        beyond = [e for e in events if _event_start_secs(e) >= HOOK_SECONDS]
        beyond_dur = sum(
            max(0.0, _event_end_secs(e) - _event_start_secs(e)) for e in beyond
        )
        if len(beyond) >= 2 or beyond_dur > 1.0:
            events = beyond  # keep only post-hook captions

    if HOOK_TEXT and hook_text:
        hook = _escape(" ".join(hook_text.split())[:60])
        events.append(
            f"Dialogue: 0,0:00:00.00,{_ts(HOOK_SECONDS)},Hook,,0,0,0,,{{\\fad(120,240)}}{hook}"
        )

    body = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(events)
        + "\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + body)
    return out_path


def burn_subtitles(clip_path: str, ass_path: str, out_path: str) -> str:
    """Burn the ASS onto the clip; canvas is padded to 1080x1920, frame rate
    locked to OUTPUT_FPS (#5) and audio loudness-normalized (#5).

    ffmpeg's ass= filter parses ':' as an option separator, which breaks absolute
    Windows paths (C:\\dir\\f.ass). Running ffmpeg with cwd=ass dir and passing only
    the basename avoids colons in the filter graph entirely and works on both
    Windows and macOS (plain POSIX paths work there too).
    """
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", clip_path,
        "-vf",
        f"ass={os.path.basename(ass_path)},"
        f"scale=1080:1920:flags=lanczos:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
        f"fps={OUTPUT_FPS}",
        "-af", LOUDNESS_FILTER,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k",
        out_path,
    ]
    subprocess.run(cmd, check=True, cwd=os.path.dirname(os.path.abspath(ass_path)))
    return out_path


def _event_start_secs(line: str) -> float:
    """Start time of a Dialogue line ('Dialogue: 0,0:01:02.50,...') in seconds."""
    try:
        ts = line.split(",", 2)[1]
        h, m, rest = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    except Exception:
        return 0.0


def _event_end_secs(line: str) -> float:
    """End time of a Dialogue line (second comma field)."""
    try:
        ts = line.split(",", 3)[2]
        h, m, rest = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    except Exception:
        return 0.0


def subtitle_burn_stage(
    clip_path: str,
    words: List[Dict],
    clip_start: float,
    clip_end: float,
    out_path: str,
    hook_text: Optional[str] = None,
) -> str:
    """Build the ASS then burn it. ASS is kept next to the output for inspection."""
    # burn_subtitles runs ffmpeg with cwd=ass dir (Windows-colon workaround), so
    # relative paths would resolve twice. Absolutize at the stage boundary.
    clip_path = os.path.abspath(clip_path)
    out_path = os.path.abspath(out_path)
    ass_path = out_path + ".ass"
    build_ass(words, clip_start, clip_end, ass_path, hook_text=hook_text)
    burn_subtitles(clip_path, ass_path, out_path)
    return out_path
