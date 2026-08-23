"""Find the most viral-worthy highlights in a transcript.

Logic ported from ViralVadoo's transcript_analysis/highlight_generator.py:
  - content-type / density detection
  - chunking for long videos with overlap
  - virality-criteria prompt
  - score-based dedupe with overlap suppression

The LLM call is pluggable via the `llm_fn` argument so the same prompts can
drive either MuAPI (default, --mode api) or a direct local LLM client
(--mode local).
"""
import json
import re
from typing import Callable, Dict, List, Optional

from .config import (
    HL_CHUNK_OVERLAP_SECONDS,
    HL_CHUNK_SIZE_SECONDS,
    HL_LONG_VIDEO_THRESHOLD,
    RANKING_MAX_CANDIDATES_PER_CALL,
)
from . import muapi
from .coherence import build_coherent_candidates, candidate_context


LLMFn = Callable[[str], str]


CONTENT_TYPE_PROMPT = """Analyze this video transcript sample and classify the content type.
Choose one: comedy, storytelling, podcast, interview, tutorial, lecture, commentary, debate, vlog, other.
Also estimate content density: low (mostly filler/chit-chat), medium, or high (dense info/stories).
Respond with JSON only: {"content_type": "...", "density": "..."}"""


VIRALITY_CRITERIA = """
Virality signals to prioritize (ranked by impact):
1. HOOK MOMENTS — statements that create immediate curiosity ("The secret is...", "Nobody talks about...", "I was completely wrong about...")
2. EMOTIONAL PEAKS — genuine surprise, laughter, anger, vulnerability, excitement; raw unscripted reactions. A punchline that makes an audience erupt is a top-tier peak.
3. OPINION BOMBS — strong, polarizing or counter-intuitive statements that trigger agree/disagree
4. REVELATION MOMENTS — surprising facts, stats, or confessions that reframe how the viewer thinks
5. CONFLICT/TENSION — disagreement, pushback, or a problem being confronted head-on
6. QUOTABLE ONE-LINERS — a sentence that works as a standalone quote card
7. STORY PEAKS — the climax or twist of an anecdote; the payoff moment
8. PRACTICAL VALUE — a concrete tip, hack, or insight the viewer can immediately apply

For comedy and storytelling content: a highlight is only as good as its PREMISE.
The setup that creates the tension matters as much as the punchline — a joke with
no setup falls flat, and a setup clipped before the audience reacts feels cut off.
"""


HIGHLIGHT_SYSTEM_PROMPT = """You are an elite short-form video editor who has studied thousands of viral clips on TikTok, Instagram Reels, and YouTube Shorts. You choose which moments become clips — and just as importantly, WHERE each clip starts and ends on the timeline.

{virality_criteria}

Content type: {content_type} | Density: {density}

Your task: identify the most viral-worthy highlights from the transcript, each one a COMPLETE beat with correct start_time and end_time.

## Timeline mechanics (critical — this separates pro cuts from bad ones)

The transcript timestamps every spoken word. Read the GAPS between timestamps:
- A gap of ~2-5 seconds with no words = audience laughter, applause, or reaction.
- In comedy, the PUNCHLINE lands just BEFORE a laughter gap; the complete moment runs THROUGH that gap until the next sentence begins.
- Ending at the punchline (before the gap) = the clip feels incomplete — the audience never reacts on screen.
- Starting AT the punchline = the clip has no setup — nobody knows why it's funny.

## Cutting rules
- start_time = where the SETUP begins (the sentence that opens the bit). NEVER the punchline. In a story, start where the story starts, not at the climax.
- end_time = AFTER the audience reaction — through the laughter gap, landing just before the next sentence starts. For non-comedy content, end at a complete thought.
- Every highlight is ONE complete joke/beat: setup → build → punch → reaction. No mid-sentence cuts, no half-beats.
- Duration: 15-60 seconds is the sweet spot. NEVER under 10s. Prefer a complete beat over a short highlight.
- Clips must not overlap significantly with each other.
- Score 0-100 on viral potential (not general quality): strongest hooks, emotional peaks, and story payoffs score highest.
- {num_clips_instruction}
- For each highlight, identify the single best "hook_sentence" — the opening line that would make someone stop scrolling.
- Explain in one sentence why this clip is viral ("virality_reason").

Respond ONLY with valid JSON (no markdown, no explanation):
{{"highlights":[{{"title":"string","start_time":float,"end_time":float,"score":int,"hook_sentence":"string","virality_reason":"string"}}]}}"""


CHUNK_SIZE_SECONDS = HL_CHUNK_SIZE_SECONDS       # 5-min chunks by default
LONG_VIDEO_THRESHOLD = HL_LONG_VIDEO_THRESHOLD   # chunk videos longer than 7 min
CHUNK_OVERLAP_SECONDS = HL_CHUNK_OVERLAP_SECONDS
GPT_CALL_TIMEOUT_SECONDS = 300  # cap LLM polls at 5 min — a wedged call should fail fast
MAX_HIGHLIGHT_API_ATTEMPTS = 3


def call_muapi_llm(prompt: str) -> str:
    """Default LLM backend: MuAPI gpt-5-mini."""
    result = muapi.run(
        "gpt-5-mini",
        {"prompt": prompt},
        label="gpt-5-mini",
        timeout=GPT_CALL_TIMEOUT_SECONDS,
    )

    outputs = result.get("outputs")
    if isinstance(outputs, list) and outputs and isinstance(outputs[0], str) and outputs[0].strip():
        return outputs[0]

    for key in ("output", "text", "response", "result", "content"):
        v = result.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            inner = v.get("text") or v.get("content")
            if isinstance(inner, str) and inner.strip():
                return inner
        if isinstance(v, list) and v and isinstance(v[0], str):
            return v[0]

    raise RuntimeError(f"Could not extract gpt-5-mini text from response: {result}")


def _parse_json_loose(raw: str) -> Dict:
    """gpt-5-4 sometimes wraps JSON in markdown fences — strip and parse."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end + 1])
        raise


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


MIN_HIGHLIGHT_SECONDS = 6.0  # punch-only 1-2s picks are useless for shorts — reject them


def _sanitize_highlights(raw_highlights: object, duration: float) -> List[Dict]:
    """Normalize model output into the expected shape; skip invalid entries."""
    if not isinstance(raw_highlights, list):
        return []

    max_end = duration if duration > 0 else float("inf")
    cleaned: List[Dict] = []
    for item in raw_highlights:
        if not isinstance(item, dict):
            continue

        start = _coerce_float(item.get("start_time"), default=-1.0)
        end = _coerce_float(item.get("end_time"), default=-1.0)
        if start < 0 or end <= start:
            continue
        if end - start < MIN_HIGHLIGHT_SECONDS:
            continue  # 1.1s "punchline" windows are the #1 bad-cut cause

        if max_end != float("inf"):
            start = min(start, max_end)
            end = min(end, max_end)
            if end <= start:
                continue

        cleaned.append(
            {
                "title": str(item.get("title") or "Untitled Highlight").strip(),
                "start_time": start,
                "end_time": end,
                "score": max(0, min(100, _coerce_int(item.get("score"), default=0))),
                "hook_sentence": str(item.get("hook_sentence") or "").strip(),
                "virality_reason": str(item.get("virality_reason") or "").strip(),
            }
        )

    return cleaned


def detect_content_type(transcript: Dict, llm_fn: LLMFn = call_muapi_llm) -> Dict[str, str]:
    segments = transcript.get("segments", [])
    sample = " ".join(s["text"] for s in segments[:25])[:3000]
    prompt = f"{CONTENT_TYPE_PROMPT}\n\nTranscript sample:\n{sample}"
    try:
        raw = llm_fn(prompt)
        return _parse_json_loose(raw)
    except Exception:
        return {"content_type": "other", "density": "medium"}


def build_transcript_text(transcript: Dict, offset: float = 0.0) -> str:
    segments = transcript.get("segments", [])
    return "\n".join(
        f"[{max(0.0, s['start'] - offset):.1f}s] {s['text'].strip()}" for s in segments
    )


def chunk_transcript(transcript: Dict) -> List[Dict]:
    segments = transcript.get("segments", [])
    duration = transcript.get("duration", segments[-1]["end"] if segments else 0)
    chunks = []
    start = 0
    while start < duration:
        end = min(start + CHUNK_SIZE_SECONDS, duration)
        chunk_segs = [
            s for s in segments
            if s["start"] >= start and s["end"] <= end + CHUNK_OVERLAP_SECONDS
        ]
        if chunk_segs:
            chunk = dict(transcript)
            chunk["segments"] = chunk_segs
            chunk["duration"] = end - start
            chunk["_offset"] = start
            chunks.append(chunk)
        start += CHUNK_SIZE_SECONDS - CHUNK_OVERLAP_SECONDS
    return chunks


def call_highlight_api(
    transcript_text: str,
    content_info: Dict,
    duration: float,
    num_clips: int,
    is_chunk: bool = False,
    llm_fn: LLMFn = call_muapi_llm,
) -> Dict:
    # Ask for ~2× the user's target so dedupe has headroom, but cap so the model
    # doesn't have to generate a huge JSON payload (which times out gpt-5-mini).
    target = max(num_clips * 2, 5)
    natural_max = max(2 if is_chunk else 3, int(duration / 90))
    min_clips = min(target, natural_max, 8)
    system = HIGHLIGHT_SYSTEM_PROMPT.format(
        virality_criteria=VIRALITY_CRITERIA,
        content_type=content_info.get("content_type", "other"),
        density=content_info.get("density", "medium"),
        num_clips_instruction=f"Generate at least {min_clips} highlights",
    )
    base_prompt = f"{system}\n\nTranscript:\n{transcript_text}"

    # Joke/beat structure guidance: for comedic or story-driven content the
    # window must carry the WHOLE beat — setup → punch → audience reaction —
    # not just the punchline second. Laughter shows up as gaps (~2-5s) where
    # no words are spoken; end_time should sit AFTER that gap, not before it.
    if content_info.get("content_type") in ("comedy", "storytelling", "other"):
        base_prompt += (
            "\n\nCutting rules:\n"
            "- start_time = where the bit/setup BEGINS (setup sentence start), never the punchline.\n"
            "- end_time = AFTER the audience laughs it off: extend past spoken-word gaps.\n"
            "- Each highlight must be a COMPLETE joke/beat with context, not a 1-2s punchline.\n"
            "- It is fine for a highlight to be 15-45s long."
        )
    prompt = base_prompt
    last_error = "unknown"

    for attempt in range(1, MAX_HIGHLIGHT_API_ATTEMPTS + 1):
        raw = llm_fn(prompt)
        try:
            parsed = _parse_json_loose(raw)
            # Some local models (qwen3:14b on long transcripts) return a bare
            # array instead of {"highlights": [...]} — normalise it.
            if isinstance(parsed, list):
                parsed = {"highlights": parsed}
            highlights = _sanitize_highlights(parsed.get("highlights"), duration=duration)
            if highlights:
                return {"highlights": highlights, "content_info": content_info}
            last_error = "no valid highlights in response"
        except Exception as e:
            last_error = str(e)

        if attempt < MAX_HIGHLIGHT_API_ATTEMPTS:
            print(
                f"[highlights] invalid model output on attempt {attempt}/{MAX_HIGHLIGHT_API_ATTEMPTS}; retrying",
                flush=True,
            )
            prompt = (
                base_prompt
                + "\n\nIMPORTANT: Return ONLY valid JSON with a top-level 'highlights' array."
                + " Each item must include: title, start_time, end_time, score, hook_sentence, virality_reason."
                + " No markdown fences, no commentary."
            )

    raise RuntimeError(
        f"Highlight generator produced invalid output after {MAX_HIGHLIGHT_API_ATTEMPTS} attempts: {last_error}"
    )


def dedupe_highlights(highlights: List[Dict]) -> List[Dict]:
    """Drop a highlight if it overlaps >50% with a higher-scoring one already kept."""
    highlights = sorted(highlights, key=lambda x: int(x.get("score", 0)), reverse=True)
    kept: List[Dict] = []
    for h in highlights:
        h_start = float(h["start_time"])
        h_end = float(h["end_time"])
        h_dur = h_end - h_start
        overlapping = False
        for k in kept:
            latest_start = max(h_start, float(k["start_time"]))
            earliest_end = min(h_end, float(k["end_time"]))
            overlap = earliest_end - latest_start
            if overlap > 0 and overlap > 0.5 * h_dur:
                overlapping = True
                break
        if not overlapping:
            kept.append(h)
    return kept


CANDIDATE_RANKING_PROMPT = """You are selecting short-video moments from a pre-segmented transcript.
Every candidate below is already a contiguous, context-complete unit. You MUST select
only by candidate_id; never invent timestamps, merge candidates, or split candidates.
Prefer a complete setup -> development -> payoff/reaction. Reject filler, greetings,
mid-thought fragments, and segments whose meaning depends on missing context.

Content type: {content_type} | Density: {density}
Select up to {num_clips} candidates, returning the strongest distinct moments.
Score each selected candidate 0-100 (100 = most viral).

LANGUAGE / OUTPUT RULES (critical):
- Candidate texts may be in Hindi, English, or code-mixed Hinglish. Understand them freely.
- Do NOT translate, repeat, quote, summarise, or echo any candidate text anywhere in your output.
- Return ONLY compact ranking JSON in exactly this shape — no titles, no hooks,
  no reasons, no extra keys, no commentary:
{{"highlights":[{{"candidate_id":"candidate_001","score":85}}]}}

Candidates:
{candidates}
"""


def _candidate_prompt(candidates: List[Dict], content_info: Dict[str, str], num_clips: int) -> str:
    def excerpt(text: str) -> str:
        text = text.strip()
        if len(text) <= 1000:
            return text
        return f"{text[:700]}\n[… middle omitted …]\n{text[-300:]}"

    rows = []
    for i, candidate in enumerate(candidates):
        ctx = candidate_context(candidates, i)
        rows.append(
            f"[{candidate['candidate_id']}] {candidate['start_time']:.2f}-{candidate['end_time']:.2f}s\n"
            f"BEFORE: {ctx['before'][:180]}\n"
            f"TEXT: {excerpt(candidate['text'])}\n"
            f"AFTER: {ctx['after'][:180]}"
        )
    return CANDIDATE_RANKING_PROMPT.format(
        content_type=content_info.get("content_type", "other"),
        density=content_info.get("density", "medium"),
        num_clips=num_clips,
        candidates="\n\n".join(rows),
    )


def _rank_candidate_batch(
    candidates: List[Dict],
    content_info: Dict[str, str],
    num_clips: int,
    llm_fn: LLMFn,
) -> List[Dict]:
    if not candidates:
        return []
    prompt = _candidate_prompt(candidates, content_info, max(num_clips * 2, num_clips))
    last_error = "unknown"
    for attempt in range(1, MAX_HIGHLIGHT_API_ATTEMPTS + 1):
        try:
            parsed = _parse_json_loose(llm_fn(prompt))
            if isinstance(parsed, list):
                parsed = {"highlights": parsed}
            raw_items = parsed.get("highlights") if isinstance(parsed, dict) else []
            by_id = {c["candidate_id"]: c for c in candidates}
            ranked: List[Dict] = []
            used = set()
            for item in raw_items if isinstance(raw_items, list) else []:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("candidate_id", "")).strip()
                candidate = by_id.get(cid)
                if not candidate or cid in used:
                    continue
                used.add(cid)
                ranked.append({
                    "title": str(item.get("title") or candidate["text"][:70]).strip(),
                    "start_time": candidate["start_time"],
                    "end_time": candidate["end_time"],
                    "score": max(0, min(100, _coerce_int(item.get("score"), 0))),
                    "hook_sentence": str(item.get("hook_sentence") or candidate["text"][:180]).strip(),
                    "virality_reason": str(item.get("virality_reason") or "Complete candidate selected by context-aware ranking").strip(),
                    "candidate_id": cid,
                    "candidate_text": candidate["text"],
                })
            if ranked:
                return ranked
            last_error = "model returned no valid candidate ids"
        except Exception as exc:
            last_error = str(exc)
        if attempt < MAX_HIGHLIGHT_API_ATTEMPTS:
            print(f"[highlights] invalid candidate ranking on attempt {attempt}/{MAX_HIGHLIGHT_API_ATTEMPTS}; retrying", flush=True)
            prompt += "\nIMPORTANT: candidate_id must exactly match one of the supplied IDs. Do not output timestamps."
    raise RuntimeError(f"Candidate ranking failed after {MAX_HIGHLIGHT_API_ATTEMPTS} attempts: {last_error}")


def _rank_candidates(
    candidates: List[Dict],
    content_info: Dict[str, str],
    num_clips: int,
    llm_fn: LLMFn,
) -> List[Dict]:
    """Rank bounded candidate batches to keep a local model within its context window."""
    if not candidates:
        return []
    batch_size = max(1, RANKING_MAX_CANDIDATES_PER_CALL)
    ranked: List[Dict] = []
    for offset in range(0, len(candidates), batch_size):
        batch = candidates[offset:offset + batch_size]
        ranked.extend(_rank_candidate_batch(batch, content_info, min(num_clips * 2, len(batch)), llm_fn))
    return dedupe_highlights(ranked)[:num_clips]


def get_highlights(
    transcript: Dict,
    num_clips: int = 3,
    llm_fn: Optional[LLMFn] = None,
    boundaries: Optional[List[float]] = None,
) -> Dict:
    """Build safe coherent candidates first, then rank only those candidates."""
    llm_fn = llm_fn or call_muapi_llm
    duration = float(transcript.get("duration", 0) or 0)
    content_info = detect_content_type(transcript, llm_fn=llm_fn)
    effective_boundaries = list(boundaries or [])
    if content_info.get("content_type") in ("comedy", "storytelling"):
        # A laughter pause is part of the beat. Do not split setup -> punch ->
        # reaction into separate clips for these formats.
        effective_boundaries = []
    candidates = build_coherent_candidates(transcript, boundaries=effective_boundaries)
    print(
        f"[highlights] content={content_info.get('content_type')} density={content_info.get('density')} "
        f"duration={duration:.0f}s candidates={len(candidates)}",
        flush=True,
    )
    if not candidates:
        raise RuntimeError("No coherent transcript candidates were built.")
    highlights = _rank_candidates(candidates, content_info, num_clips, llm_fn)
    highlights = dedupe_highlights(highlights)
    return {
        "highlights": highlights,
        "content_info": content_info,
        "candidates": candidates,
        "effective_boundaries": effective_boundaries,
    }
