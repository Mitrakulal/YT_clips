"""Topic-shift segmentation for local mode.

Fixes the "merged clips" bug: an LLM-picked window can span two different
topics / speakers and silently becomes one clip. This stage computes a list
of **boundaries** on the source timeline (semantic shifts via
nomic-embed-text similarity dips + real pauses), and the clipper then splits
any window that crosses a boundary so clips never mix two different moments.

Free stack only: Ollama `nomic-embed-text` (already installed locally) for
embeddings; numpy for the cosine-similarity math. No API keys, no models to
download.
"""
import json
import os
import re
import statistics
import urllib.request
from typing import Dict, List, Optional, Tuple

from ..config import (
    SEGMENTATION_SERVICE,
    TOPIC_SIM_SIGMAS,
    PAUSE_BOUNDARY_SECONDS,
    BOUNDARY_MIN_GAP_SECONDS,
)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("SEGMENTATION_EMBED_MODEL", "nomic-embed-text")
_EMBED_BATCH = 32
_SENTENCE_END = re.compile(r"[.!?…]+[\"'\u201d\u2019)]*\s*$")


# ---------------------------------------------------------------------------
# 1. Sentence units — whisper segments -> complete sentences with timestamps.
# ---------------------------------------------------------------------------
def build_sentences(segments: List[Dict]) -> List[Dict]:
    """Merge whisper segments into sentence units.

    Returns [{start, end, text}]. A unit ends when the accumulated text ends
    with sentence punctuation, on a >= PAUSE_BOUNDARY_SECONDS gap, or when it
    would grow past ~90s. Without word timestamps the unit boundary is the
    start of the next whisper segment (close enough for clip splits).
    """
    units: List[Dict] = []
    cur_text: List[str] = []
    cur_start: Optional[float] = None
    cur_end: float = 0.0

    def flush():
        nonlocal cur_text, cur_start
        if cur_text:
            units.append({
                "start": cur_start or 0.0,
                "end": cur_end,
                "text": " ".join(cur_text).strip(),
            })
        cur_text = []
        cur_start = None

    for seg in segments:
        s, e, t = float(seg["start"]), float(seg["end"]), (seg.get("text") or "").strip()
        if not t:
            continue
        if cur_start is None:
            cur_start = s
        # A real pause is a hard unit boundary (audio evidence of a completed
        # thought), independent of punctuation.
        if cur_end and (s - cur_end) >= PAUSE_BOUNDARY_SECONDS:
            flush()
            cur_start = s
        cur_text.append(t)
        cur_end = e
        if _SENTENCE_END.search(t) or (e - (cur_start or 0.0)) >= 90.0:
            flush()
    flush()
    return units


# ---------------------------------------------------------------------------
# 2. Embeddings via Ollama (free, local).
# ---------------------------------------------------------------------------
def _embed_batch(inputs: List[str]) -> List[List[float]]:
    body = json.dumps({"model": EMBED_MODEL, "input": inputs}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/embed", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    embs = data.get("embeddings") or []
    return [e if isinstance(e, list) else e.get("embedding") for e in embs]


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Batch-embed; falls back to empty vectors when Ollama is unreachable."""
    if not texts:
        return []
    try:
        out: List[List[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            out.extend(_embed_batch(texts[i:i + _EMBED_BATCH]))
        return out
    except Exception as e:  # Ollama down / model missing -> graceful no-op
        print(f"[segment] embedding unavailable: {e}", flush=True)
        return []


# ---------------------------------------------------------------------------
# 3. Boundary detection: windowed similarity dips + pause boundaries.
# ---------------------------------------------------------------------------
def find_topic_boundaries(
    sentences: List[Dict],
    embeddings: List[List[float]],
    sigmas: Optional[float] = None,
) -> List[float]:
    """Return boundary timestamps where topic shifts.

    For each sentence compute the mean cosine similarity to its neighbors in
    a sliding window (±2). Dips below (mean_sim - sigma*sd) mark a shift; the
    boundary is placed at the FIRST sentence start of the low-similarity run,
    and runs are merged (no two boundaries < 3 s apart).
    """
    if sigmas is None:
        sigmas = TOPIC_SIM_SIGMAS
    if len(sentences) < 4 or len(embeddings) != len(sentences):
        return []
    import numpy as np

    M = np.asarray(embeddings, dtype=np.float64)
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    U = M / norms
    sim = U @ U.T  # cosine similarity matrix

    n = len(sentences)
    windowed = np.zeros(n)
    for i in range(n):
        lo, hi = max(0, i - 2), min(n, i + 3)
        row = sim[i, lo:hi]
        row = row[row !=  1.0]  # drop self-similarity
        windowed[i] = float(row.mean()) if row.size else 0.0

    mean_sim, std_sim = float(windowed.mean()), float(windowed.std())
    if std_sim < 1e-6:
        std_sim = 1e-6
    threshold = mean_sim - std_sim * sigmas

    dips: List[int] = []
    for i in range(1, n):
        if windowed[i] < threshold and windowed[i] <= windowed[i - 1]:
            dips.append(i)
    if not dips:
        return []

    # Merge nearby dips into runs; boundary = sentence start of each run.
    runs: List[List[int]] = [[dips[0]]]
    for d in dips[1:]:
        if d - runs[-1][-1] <= 1:
            runs[-1].append(d)
        else:
            runs.append([d])
    boundaries = []
    for run in runs:
        b = sentences[run[0]]["start"]
        if not boundaries or b - boundaries[-1] >= 3.0:
            boundaries.append(round(b, 2))
    return boundaries


def find_pause_boundaries(segments: List[Dict]) -> List[float]:
    """Hard boundaries at real silences >= PAUSE_BOUNDARY_SECONDS."""
    bounds = []
    prev_end = None
    for seg in segments:
        s = float(seg["start"])
        if prev_end is not None and (s - prev_end) >= PAUSE_BOUNDARY_SECONDS:
            bounds.append(round(prev_end, 2))
        prev_end = float(seg["end"])
    return bounds


def compute_boundaries(transcript: Dict) -> List[float]:
    """Unified boundary list for the whole source timeline.

    semantic shifts (if SEGMENTATION_SERVICE allows) ∪ pause boundaries.
    Sorted, deduped.
    """
    segments = transcript.get("segments", [])
    if not segments:
        return []
    service = SEGMENTATION_SERVICE
    boundaries: List[float] = []

    if service in ("semantic", "auto"):
        sentences = build_sentences(segments)
        embs = embed_texts([s["text"] for s in sentences])
        boundaries += find_topic_boundaries(sentences, embs)

    boundaries += find_pause_boundaries(segments)
    if not boundaries:
        return []
    # Cluster rule: no two boundaries closer than BOUNDARY_MIN_GAP_SECONDS.
    # Turn-taking in interviews fires pauses constantly; dense clusters carry
    # no extra signal — keep the earliest of each cluster (greedy).
    out, last = [], -1e9
    for b in sorted(boundaries):
        b = round(float(b) + 0.04, 2)  # tiny nudge so splits never re-touch
        if b - last >= BOUNDARY_MIN_GAP_SECONDS:
            out.append(b)
            last = b
    return out


def split_window_at_boundaries(
    start: float, end: float,
    boundaries: List[float],
    min_dur: float,
) -> List[Tuple[float, float]]:
    """Split [start,end] at any contained boundary.

    A piece shorter than min_dur merges into the NEIGHBOR piece (so the user
    never gets a sub-8 second sliver); at least one piece is always returned.
    """
    inside = [b for b in boundaries if start < b < end]
    if not inside:
        return [(start, end)]
    cuts = [start] + inside + [end]
    pieces = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1)]
    merged: List[Tuple[float, float]] = []
    for p in pieces:
        if merged and (p[1] - p[0]) < min_dur:
            # garbage into the previous piece
            s0, e0 = merged[-1]
            merged[-1] = (s0, p[1])
        else:
            merged.append(p)
    return merged