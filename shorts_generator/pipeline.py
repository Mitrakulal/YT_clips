"""Pipeline facade for hosted and fully local clip generation modes."""
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .clipper import crop_highlights
from .downloader import download_youtube
from .highlights import call_muapi_llm, get_highlights
from .transcriber import transcribe

ProgressCallback = Callable[[str, str], None]


def _emit(progress_callback: Optional[ProgressCallback], stage: str, message: str) -> None:
    if progress_callback:
        progress_callback(stage, message)


def _run_local(
    youtube_url: str,
    num_clips: int,
    aspect_ratio: str,
    download_format: str,
    language: Optional[str],
    output_dir: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    caption_mode: str = "generated",
) -> Dict:
    from .config import LOCAL_OUTPUT_DIR, REACTION_TAIL_SECONDS, SEGMENTATION_SERVICE
    from .local.clipper import crop_highlights_local
    from .local.downloader import download_youtube_local
    from .local.llm import call_local_llm
    from .local.segment import compute_boundaries
    from .local.transcriber import transcribe_local
    from .local.validate import validate_clip
    from subtitles import subtitle_burn_stage
    from thumbnail import thumbnail_stage

    job_dir = Path(output_dir or LOCAL_OUTPUT_DIR)
    job_dir.mkdir(parents=True, exist_ok=True)

    _emit(progress_callback, "downloading", f"Downloading source at {download_format}p")
    source_path = download_youtube_local(
        youtube_url,
        fmt=download_format,
        out_dir=str(job_dir / "source"),
    )

    _emit(progress_callback, "transcribing", "Creating transcript and word timestamps")
    transcript = transcribe_local(
        source_path,
        language=language,
        cache_dir=str(job_dir / "transcript"),
    )
    if not transcript["segments"]:
        raise RuntimeError("Whisper produced no segments. The video may have no detectable speech.")

    _emit(progress_callback, "segmenting", "Building safe topic and pause boundaries")
    boundaries = compute_boundaries(transcript) if SEGMENTATION_SERVICE != "off" else []
    print(f"[pipeline/local] segmentation {'on' if boundaries else 'off'}: {len(boundaries)} boundary(ies)", flush=True)

    _emit(progress_callback, "ranking", "Ranking complete context-preserving candidates")
    highlights_result = get_highlights(
        transcript,
        num_clips=num_clips,
        llm_fn=call_local_llm,
        boundaries=boundaries,
    )
    all_highlights: List[Dict] = highlights_result.get("highlights", [])
    if not all_highlights:
        raise RuntimeError("Highlight generator returned zero clips.")

    content_type = str(highlights_result.get("content_info", {}).get("content_type", "other"))
    reaction_tail = REACTION_TAIL_SECONDS if content_type in {"comedy", "storytelling"} else 0.0
    top = [
        {**highlight, "reaction_tail_seconds": reaction_tail}
        for highlight in sorted(all_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)[:num_clips]
    ]
    render_boundaries = highlights_result.get("effective_boundaries", boundaries)
    _emit(progress_callback, "cropping", f"Rendering {len(top)} vertical clip candidate(s)")
    shorts = crop_highlights_local(
        source_path,
        top,
        aspect_ratio=aspect_ratio,
        boundaries=render_boundaries,
        out_dir=str(job_dir / "clips"),
    )

    caption_mode = (caption_mode or "generated").strip().lower()
    if caption_mode not in {"generated", "source"}:
        raise ValueError("caption_mode must be 'generated' or 'source'.")
    treatment = "Preserving source captions and validating output" if caption_mode == "source" else "Burning generated captions, normalizing audio, and validating output"
    _emit(progress_callback, "captioning", treatment)
    words = [word for segment in transcript.get("segments", []) for word in segment.get("words", [])]
    for short in shorts:
        if not short.get("clip_url"):
            continue
        raw_clip = short["clip_url"]
        try:
            if caption_mode == "source":
                final_clip = raw_clip
            else:
                final_clip = str(Path(raw_clip).with_suffix("")) + "_captioned.mp4"
                subtitle_burn_stage(
                    raw_clip,
                    words,
                    float(short["start_time"]),
                    float(short["end_time"]),
                    final_clip,
                    hook_text=short.get("title"),
                    aspect_ratio=aspect_ratio,
                )
            validate_clip(final_clip, aspect_ratio=aspect_ratio)
            short["clip_url"] = final_clip
            thumbnail = thumbnail_stage(
                final_clip,
                short.get("title"),
                f"{Path(final_clip).with_suffix('')}.jpg",
                enabled=True,
            )
            if thumbnail:
                short["thumbnail_url"] = thumbnail
        except Exception as exc:
            short["clip_url"] = None
            short["error"] = f"treatment: {exc}"
            print(f"[pipeline/local] treatment failed for {raw_clip}: {exc}", flush=True)

    return {
        "mode": "local",
        "source_video_url": source_path,
        "transcript": transcript,
        "highlights": all_highlights,
        "shorts": shorts,
        "caption_mode": caption_mode,
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
        raise RuntimeError("Whisper produced no segments. The video may have no detectable speech.")
    highlights_result = get_highlights(transcript, num_clips=num_clips, llm_fn=call_muapi_llm, boundaries=[])
    all_highlights: List[Dict] = highlights_result.get("highlights", [])
    if not all_highlights:
        raise RuntimeError("Highlight generator returned zero clips.")
    top = sorted(all_highlights, key=lambda h: int(h.get("score", 0)), reverse=True)[:num_clips]
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
    output_dir: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    caption_mode: str = "generated",
) -> Dict:
    """Run the full pipeline and optionally emit local job-stage progress."""
    mode = (mode or "api").lower()
    if mode == "local":
        return _run_local(
            youtube_url,
            num_clips,
            aspect_ratio,
            download_format,
            language,
            output_dir=output_dir,
            progress_callback=progress_callback,
            caption_mode=caption_mode,
        )
    if mode == "api":
        return _run_api(youtube_url, num_clips, aspect_ratio, download_format, language)
    raise ValueError(f"Unknown mode: {mode!r}. Use 'api' or 'local'.")
