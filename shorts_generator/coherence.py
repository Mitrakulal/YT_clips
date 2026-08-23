"""Segmentation-first candidate construction for production clip selection.

The ranker never chooses arbitrary timestamps. It selects from contiguous,
source-faithful candidates whose starts and ends are already aligned to
transcript units and safe boundary points.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import COHERENCE_MAX_SECONDS, COHERENCE_MIN_SECONDS, COHERENCE_TARGET_SECONDS, SHORTS_MIN_SECONDS

_SENTENCE_ENDINGS = ".!?…。！？"
_CONTEXT_DEPENDENT_OPENING = re.compile(
    r"^\s*(?:and|but|so|because|also|then|anyway|okay|yeah|yes|no|right|well|i mean|i thought it was|it was|that's|this is)\b",
    re.IGNORECASE,
)


def _valid_segments(transcript: Dict) -> List[Dict]:
    out: List[Dict] = []
    for raw in transcript.get("segments", []) or []:
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = " ".join(str(raw.get("text", "")).split())
        if end > start and text:
            out.append({"start": start, "end": end, "text": text})
    return sorted(out, key=lambda s: (s["start"], s["end"]))


def build_transcript_units(transcript: Dict, pause_seconds: float = 1.2) -> List[Dict]:
    """Merge ASR segments into timestamped, sentence-like units.

    A unit closes at sentence punctuation, at a substantial pause, or at a
    conservative 25-second ceiling. The function never invents text.
    """
    segments = _valid_segments(transcript)
    units: List[Dict] = []
    current: List[str] = []
    start: Optional[float] = None
    end = 0.0

    def flush() -> None:
        nonlocal current, start, end
        if current and start is not None and end > start:
            units.append({"start": start, "end": end, "text": " ".join(current).strip()})
        current = []
        start = None
        end = 0.0

    for seg in segments:
        if start is None:
            start = seg["start"]
        elif seg["start"] - end >= pause_seconds:
            flush()
            start = seg["start"]
        current.append(seg["text"])
        end = seg["end"]
        if seg["text"].rstrip().endswith(tuple(_SENTENCE_ENDINGS)) or end - start >= 90.0:
            flush()
    flush()
    return units


def _split_by_boundaries(units: Sequence[Dict], boundaries: Iterable[float]) -> List[List[Dict]]:
    boundary_values = sorted(float(b) for b in boundaries)
    sections: List[List[Dict]] = []
    current: List[Dict] = []
    for unit in units:
        if current and any(current[-1]["end"] <= b <= unit["start"] for b in boundary_values):
            sections.append(current)
            current = []
        current.append(unit)
    if current:
        sections.append(current)
    return sections


def _section_to_candidate(units: Sequence[Dict], index: int, prefix: str = "candidate") -> Dict:
    return {
        "candidate_id": f"{prefix}_{index:03d}",
        "start_time": float(units[0]["start"]),
        "end_time": float(units[-1]["end"]),
        "text": " ".join(u["text"] for u in units).strip(),
        "unit_count": len(units),
    }


def _merge_short_sections(sections: List[List[Dict]], min_seconds: float) -> List[List[Dict]]:
    """Merge short sections into the nearest neighbor without dropping text."""
    sections = [list(s) for s in sections if s]
    if len(sections) < 2:
        return sections
    changed = True
    while changed and len(sections) > 1:
        changed = False
        for i, section in enumerate(sections):
            duration = section[-1]["end"] - section[0]["start"]
            if duration >= min_seconds:
                continue
            if i == 0:
                sections[1] = section + sections[1]
                del sections[0]
            elif i == len(sections) - 1:
                sections[i - 1] = sections[i - 1] + section
                del sections[i]
            else:
                left = sections[i - 1][-1]["end"] - sections[i - 1][0]["start"]
                right = sections[i + 1][-1]["end"] - sections[i + 1][0]["start"]
                if left <= right:
                    sections[i - 1].extend(section)
                else:
                    sections[i + 1] = section + sections[i + 1]
                del sections[i]
            changed = True
            break
    return sections


def _split_long_section(section: Sequence[Dict], target_seconds: float, max_seconds: float) -> List[List[Dict]]:
    """Split only at transcript-unit boundaries, preferring target duration."""
    if not section:
        return []
    if section[-1]["end"] - section[0]["start"] <= max_seconds:
        return [list(section)]
    pieces: List[List[Dict]] = []
    current: List[Dict] = []
    for unit in section:
        if current:
            current_duration = unit["end"] - current[0]["start"]
            if current_duration > max_seconds or (
                current_duration >= target_seconds and len(current) >= 2
            ):
                pieces.append(current)
                current = []
        current.append(unit)
    if current:
        pieces.append(current)
    return pieces


def build_coherent_candidates(
    transcript: Dict,
    boundaries: Optional[Iterable[float]] = None,
    min_seconds: float = COHERENCE_MIN_SECONDS,
    target_seconds: float = COHERENCE_TARGET_SECONDS,
    max_seconds: float = COHERENCE_MAX_SECONDS,
    pause_seconds: float = 1.2,
) -> List[Dict]:
    """Build contiguous, context-complete candidates for ranking.

    Semantic/pause boundaries are treated as preferred section breaks. Short
    sections are merged instead of emitted as slivers. Long sections are split
    only at transcript-unit boundaries, never at arbitrary model timestamps.
    """
    units = build_transcript_units(transcript, pause_seconds=pause_seconds)
    if not units:
        return []
    source_duration = units[-1]["end"] - units[0]["start"]
    effective_min = min(min_seconds, source_duration) if source_duration >= SHORTS_MIN_SECONDS else min_seconds
    sections = _split_by_boundaries(units, boundaries or [])
    sections = _merge_short_sections(sections, effective_min)
    expanded: List[List[Dict]] = []
    for section in sections:
        expanded.extend(_split_long_section(section, target_seconds, max_seconds))
    expanded = _merge_short_sections(expanded, effective_min)

    candidates: List[Dict] = []
    for index, section in enumerate(expanded, start=1):
        candidate = _section_to_candidate(section, index)
        if candidate["end_time"] - candidate["start_time"] >= effective_min:
            candidates.append(candidate)
    return candidates


def build_human_editor_candidates(
    transcript: Dict,
    min_seconds: float = SHORTS_MIN_SECONDS,
    max_seconds: float = COHERENCE_MAX_SECONDS,
    pause_seconds: float = 1.2,
    boundaries: Optional[Iterable[float]] = None,
) -> List[Dict]:
    """Build editorial candidates for comedy/storytelling without arbitrary cuts.

    A human editor does not start a crowd-work clip on an answer, a reaction, or
    a connective fragment just because it happened to be exciting. This helper
    starts only at likely standalone premises, then offers every later complete
    transcript-unit ending in the allowed duration range. The ranker can choose
    the strongest setup→payoff/reaction endpoint, but can never move either edge
    inside a spoken unit or force the next unrelated topic into the clip.
    """
    units = build_transcript_units(transcript, pause_seconds=pause_seconds)
    boundary_values = sorted(float(boundary) for boundary in (boundaries or []))
    candidates: List[Dict] = []
    index = 1
    for start_index, start_unit in enumerate(units):
        if _CONTEXT_DEPENDENT_OPENING.match(start_unit["text"]):
            continue
        for end_index in range(start_index, len(units)):
            end_unit = units[end_index]
            # For non-comedy sources, semantic/pause boundaries signal a topic
            # change. Do not construct a candidate that crosses one, because a
            # human editor would not carry an unrelated next answer into the
            # current clip merely to make it longer.
            if end_index > start_index and any(
                units[end_index - 1]["end"] <= boundary <= end_unit["start"]
                for boundary in boundary_values
            ):
                break
            duration = float(end_unit["end"]) - float(start_unit["start"])
            if duration > max_seconds:
                break
            if duration < min_seconds:
                continue
            # If the transcript has multiple units, a lone eligible first unit
            # is usually setup without its payoff. Human editors wait for the
            # next complete unit instead of publishing that dangling premise.
            if len(units) > 1 and end_index == start_index:
                continue
            candidates.append(
                {
                    "candidate_id": f"candidate_{index:03d}",
                    "start_time": float(start_unit["start"]),
                    "end_time": float(end_unit["end"]),
                    "text": " ".join(unit["text"] for unit in units[start_index:end_index + 1]).strip(),
                    "unit_count": end_index - start_index + 1,
                }
            )
            index += 1
    if not candidates and units:
        # Preserve one-unit monologues as a fallback rather than emitting no
        # clip at all; the normal multi-unit path above remains the preference.
        for unit in units:
            duration = float(unit["end"]) - float(unit["start"])
            if min_seconds <= duration <= max_seconds and not _CONTEXT_DEPENDENT_OPENING.match(unit["text"]):
                candidates.append(_section_to_candidate([unit], len(candidates) + 1, prefix="candidate"))
    return candidates


def candidate_context(candidates: Sequence[Dict], index: int, radius: int = 1) -> Dict[str, str]:
    """Return neighboring context for ranking without changing candidate spans."""
    before = candidates[index - radius]["text"] if index >= radius else ""
    after = candidates[index + radius]["text"] if index + radius < len(candidates) else ""
    return {"before": before, "after": after}
