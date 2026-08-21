"""End-to-end orchestrator.

Two modes:
  * mode="api"   (default) — MuAPI does download / transcribe / LLM / autocrop.
                              Fast, no local deps, pay-per-call.
  * mode="local"            — yt-dlp + faster-whisper + OpenAI or Gemini + ffmpeg/opencv.
                              Self-hosted, LLM_PROVIDER selects OpenAI or Gemini.
"""
from pathlib import Path
from typing import Dict, List, Optional

from .clipper import crop_highlights
from .downloader import download_youtube
from .highlights import call_muapi_llm, get_highlights
from .transcriber import transcribe


def _run_local(
    youtube_url: str,
    num_clips: int,
    aspect_ratio: str,
    download_format: str,
    language: Optional[str],
) -> Dict:
    from .local.clipper import crop_highlights_local
    from .local.downloader import download_youtube_local
    from .local.llm import call_local_llm
    from .local.segment import compute_boundaries
    from .local.transcriber import transcribe_local

    from subtitles import subtitle_burn_stage
    from thumbnail import thumbnail_stage
    from .local.validate import validate_clip

    from .config import SEGMENTATION_SERVICE, LOCAL_OUTPUT_DIR

    source_path = download_youtube_local(youtube_url, fmt=download_format)

    transcript = transcribe_local(source_path, language=language)
    if not transcript["segments"]:
        raise RuntimeError(
            "Whisper produced no segments. The video may have no detectable speech."
        )

    boundaries = compute_boundaries(transcript) if SEGMENTATION_SERVICE != "off" else []
    print(f"[pipeline/local] segmentation {'on' if boundaries else 'off'}: {len(boundaries)} boundary(ies)", flush=True)
    if boundaries:
        print("[pipeline/local] at " + ", ".join(f"{b:.1f}s" for b in boundaries), flush=True)

    highlights_result = get_highlights(
        transcript,
        num_clips=num_clips,
        llm_fn=call_local_llm,
        boundaries=boundaries,
    )
    all_highlights: List[Dict] = highlights_result.get("highlights", [])
    if not all_highlights:
        raise RuntimeError("Highlight generator returned zero clips.")

    top = sorted(all_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)[:num_clips]
    print(f"[pipeline/local] cropping {len(top)} of {len(all_highlights)} candidates", flush=True)

    # Use the same effective boundaries that candidate construction used.
    render_boundaries = highlights_result.get("effective_boundaries", boundaries)
    shorts = crop_highlights_local(source_path, top, aspect_ratio=aspect_ratio, boundaries=render_boundaries)

    # Finished treatment (the "upload-ready" pass): burn hook + captions,
    # loudnorm to -14 LUFS, pad to 1080x1920, lock 30fps — the same stage the
    # queue/publish path runs. Local mode now produces delivery-ready clips.
    words = [w for seg in transcript.get("segments", []) for w in seg.get("words", [])]
    for short in shorts:
        if not short.get("clip_url"):
            continue
        clip = short["clip_url"]
        captioned = str(Path(clip).with_suffix("")) + "_captioned.mp4"
        try:
            subtitle_burn_stage(
                clip, words,
                float(short["start_time"]), float(short["end_time"]),
                captioned,
                hook_text=short.get("title"),
            )
            validate_clip(captioned)
            short["clip_url"] = captioned
            thumb = thumbnail_stage(
                captioned, short.get("title"),
                f"{Path(captioned).with_suffix('')}.jpg",
                enabled=True,
            )
            if thumb:
                short["thumbnail_url"] = thumb
        except Exception as e:
            short["clip_url"] = None
            short["error"] = f"treatment: {e}"
            print(f"[pipeline/local] treatment failed for {clip}: {e}", flush=True)

    return {
        "mode": "local",
        "source_video_url": source_path,
        "transcript": transcript,
        "highlights": all_highlights,
        "shorts": shorts,
    }


def _run_api(
    youtube_url: str,
    num_clips: int,
    aspect_ratio: str,
    download_format: str,
    language: Optional[str],
) -> Dict:
    source_url = download_youtube(youtube_url, fmt=download_format)

    transcript = transcribe(source_url, language=language)
    if not transcript["segments"]:
        raise RuntimeError(
            "Whisper produced no segments. The video may have no detectable speech."
        )

    highlights_result = get_highlights(
        transcript,
        num_clips=num_clips,
        llm_fn=call_muapi_llm,
        boundaries=[],
    )
    all_highlights: List[Dict] = highlights_result.get("highlights", [])
    if not all_highlights:
        raise RuntimeError("Highlight generator returned zero clips.")

    top = sorted(all_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)[:num_clips]
    print(f"[pipeline] cropping {len(top)} of {len(all_highlights)} candidates", flush=True)

    shorts = crop_highlights(source_url, top, aspect_ratio=aspect_ratio)

    return {
        "mode": "api",
        "source_video_url": source_url,
        "transcript": transcript,
        "highlights": all_highlights,
        "shorts": shorts,
    }


def generate_shorts(
    youtube_url: str,
    num_clips: int = 3,
    aspect_ratio: str = "9:16",
    download_format: str = "720",
    language: Optional[str] = None,
    mode: str = "api",
) -> Dict:
    """Run the full pipeline and return a structured result.

    Args:
        youtube_url: source URL.
        num_clips: how many shorts to render.
        aspect_ratio: e.g. "9:16", "1:1".
        download_format: source resolution ("360" / "480" / "720" / "1080").
        language: ISO-639-1 to force Whisper language detection.
        mode: "api" (default, MuAPI) or "local" (yt-dlp + faster-whisper +
            OpenAI or Gemini + ffmpeg).

    Returns:
        {
          "mode": "api" | "local",
          "source_video_url": str,   # hosted URL (api) or local path (local)
          "transcript": {...},
          "highlights": [...],       # all candidates ranked
          "shorts": [...],           # top `num_clips` with clip_url / local path
        }
    """
    mode = (mode or "api").lower()
    if mode == "local":
        return _run_local(youtube_url, num_clips, aspect_ratio, download_format, language)
    if mode == "api":
        return _run_api(youtube_url, num_clips, aspect_ratio, download_format, language)
    raise ValueError(f"Unknown mode: {mode!r}. Use 'api' or 'local'.")
