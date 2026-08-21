"""Stage-machine helpers for the pipeline worker.

Rules (LOCKED):
1. Idempotent: a stage whose status == "done" (and whose artifact exists) is skipped.
2. Atomic: state is written to a tmp file then os.replace()d — no half-files on crash.
3. Resumable: on boot, the worker re-queues every job in queue/active/ whose status
   != "done"; it resumes from the last completed stage.
"""
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


def new_state(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": job.get("job_id", ""),
        "source_url": job.get("source_url", ""),
        "num_clips": int(job.get("num_clips", 3)),
        "aspect_ratio": job.get("aspect_ratio", "9:16"),
        "format": job.get("format"),
        "status": "running",  # pending | running | done | failed
        "stages": {
            "download": {},
            "transcribe": {},
            "segment": {},
            "highlight_llm": {},
            "crop": {},
            "subtitle_burn": {},
            "cleanup": {},
        },
        "error": None,
    }


def load_state(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(path: Path, state: Dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def stage_done(state: Dict[str, Any], name: str) -> bool:
    info = state["stages"].get(name) or {}
    artifact = info.get("artifact")
    return info.get("status") == "done" and bool(artifact) and Path(str(artifact)).exists()


def mark_stage(
    state: Dict[str, Any],
    name: str,
    status: str,
    artifact: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    state["stages"][name] = {"status": status, "artifact": artifact, "error": error}
    return state
