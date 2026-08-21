"""Fully local dashboard for running the YT_clips production pipeline on a Mac.

The server binds to 127.0.0.1 by default. Jobs, logs, source downloads,
transcripts, and final clips remain in this repository's studio_data directory.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, render_template_string, request, send_file

from shorts_generator.local.validate import validate_clip
from shorts_generator.pipeline import generate_shorts

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / "studio_data"
JOBS_ROOT = DATA_ROOT / "jobs"
DB_PATH = DATA_ROOT / "studio.sqlite3"
SECRET_PATH = DATA_ROOT / ".local-url-secret"
MIGRATIONS_DIR = ROOT / "migrations"
STAGE_SEQUENCE = [
    "queued",
    "downloading",
    "transcribing",
    "segmenting",
    "ranking",
    "cropping",
    "captioning",
    "done",
]
ALL_STAGES = STAGE_SEQUENCE + ["failed"]
POLL_SECONDS = 1.0

app = Flask(__name__)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def now() -> float:
    return time.time()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_storage() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        os.chmod(SECRET_PATH, 0o600)
    with db() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, filename TEXT NOT NULL, applied_at REAL NOT NULL)"
        )
        applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
        for migration in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(migration.name.split("_", 1)[0])
            if version in applied:
                continue
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations (version, filename, applied_at) VALUES (?, ?, ?)",
                (version, migration.name, now()),
            )


def log_event(job_id: str, stage: str, message: str) -> None:
    with db() as connection:
        connection.execute(
            "INSERT INTO job_logs (job_id, timestamp, stage, message) VALUES (?, ?, ?, ?)",
            (job_id, now(), stage, message),
        )


def set_stage(job_id: str, stage: str, message: str = "") -> None:
    if stage not in ALL_STAGES:
        raise ValueError(f"Unsupported pipeline stage: {stage}")
    timestamp = now()
    with db() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise ValueError("Job does not exist")
        if stage == "failed":
            if job["active_stage"] in STAGE_SEQUENCE:
                connection.execute(
                    "UPDATE job_stages SET status = 'error', completed_at = ?, message = ? WHERE job_id = ? AND stage = ?",
                    (timestamp, message, job_id, job["active_stage"]),
                )
            connection.execute(
                "UPDATE jobs SET status = 'failed', active_stage = 'failed', completed_at = ?, error_stage = ?, error_message = ? WHERE id = ?",
                (timestamp, job["active_stage"], message, job_id),
            )
            connection.execute(
                "INSERT INTO job_stages (job_id, stage, status, started_at, completed_at, message) VALUES (?, 'failed', 'error', ?, ?, ?) ON CONFLICT(job_id, stage) DO UPDATE SET status='error', started_at=excluded.started_at, completed_at=excluded.completed_at, message=excluded.message",
                (job_id, timestamp, timestamp, message),
            )
        elif stage == "done":
            connection.execute(
                "UPDATE jobs SET status = 'done', active_stage = 'done', completed_at = ? WHERE id = ?",
                (timestamp, job_id),
            )
            connection.execute(
                "UPDATE job_stages SET status = 'complete', completed_at = ? WHERE job_id = ? AND stage = 'captioning'",
                (timestamp, job_id),
            )
            connection.execute(
                "INSERT INTO job_stages (job_id, stage, status, started_at, completed_at, message) VALUES (?, 'done', 'complete', ?, ?, ?) ON CONFLICT(job_id, stage) DO UPDATE SET status='complete', started_at=excluded.started_at, completed_at=excluded.completed_at, message=excluded.message",
                (job_id, timestamp, timestamp, message),
            )
        else:
            previous = job["active_stage"]
            if previous in STAGE_SEQUENCE and previous != stage:
                connection.execute(
                    "UPDATE job_stages SET status = 'complete', completed_at = ? WHERE job_id = ? AND stage = ? AND status = 'active'",
                    (timestamp, job_id, previous),
                )
            connection.execute(
                "UPDATE jobs SET status = ?, active_stage = ?, started_at = COALESCE(started_at, ?) WHERE id = ?",
                (stage, stage, timestamp, job_id),
            )
            connection.execute(
                "INSERT INTO job_stages (job_id, stage, status, started_at, message) VALUES (?, ?, 'active', ?, ?) ON CONFLICT(job_id, stage) DO UPDATE SET status='active', started_at=COALESCE(job_stages.started_at, excluded.started_at), message=excluded.message",
                (job_id, stage, timestamp, message),
            )
    log_event(job_id, stage, message or f"Entered {stage} stage")


def safe_youtube_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    host = (parsed.netloc or "").lower().removeprefix("www.")
    return parsed.scheme in {"http", "https"} and host in {"youtube.com", "m.youtube.com", "youtu.be"}


def create_job(source_url: str, num_clips: int, aspect_ratio: str, quality: str) -> str:
    source_url = source_url.strip()
    if not safe_youtube_url(source_url):
        raise ValueError("Enter a valid YouTube or youtu.be URL.")
    if num_clips not in range(1, 11):
        raise ValueError("Number of clips must be between 1 and 10.")
    if aspect_ratio not in {"9:16", "1:1"}:
        raise ValueError("Aspect ratio must be 9:16 or 1:1.")
    if quality not in {"720", "1080"}:
        raise ValueError("Quality must be 720p or 1080p.")
    job_id = uuid.uuid4().hex[:12]
    output_dir = JOBS_ROOT / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now()
    with db() as connection:
        connection.execute(
            "INSERT INTO jobs (id, source_url, num_clips, aspect_ratio, quality, status, active_stage, created_at, output_dir) VALUES (?, ?, ?, ?, ?, 'queued', 'queued', ?, ?)",
            (job_id, source_url, num_clips, aspect_ratio, quality, timestamp, str(output_dir)),
        )
        for stage in ALL_STAGES:
            status = "active" if stage == "queued" else "pending"
            started_at = timestamp if stage == "queued" else None
            connection.execute(
                "INSERT INTO job_stages (job_id, stage, status, started_at, message) VALUES (?, ?, ?, ?, ?)",
                (job_id, stage, status, started_at, "Waiting for local worker" if stage == "queued" else ""),
            )
    log_event(job_id, "queued", "Job accepted locally and added to the pipeline queue")
    return job_id


def row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def local_secret() -> bytes:
    return SECRET_PATH.read_text(encoding="utf-8").encode("utf-8")


def sign_media(job_id: str, filename: str, expires_at: int) -> str:
    payload = f"{job_id}:{filename}:{expires_at}".encode("utf-8")
    return hmac.new(local_secret(), payload, hashlib.sha256).hexdigest()


def signed_media_url(job_id: str, filename: str, lifetime_seconds: int = 6 * 60 * 60) -> str:
    expires_at = int(now()) + lifetime_seconds
    signature = sign_media(job_id, filename, expires_at)
    return f"/media/{job_id}/{filename}?exp={expires_at}&sig={signature}"


def is_valid_signature(job_id: str, filename: str, expires_at: str, signature: str) -> bool:
    try:
        expiry = int(expires_at)
    except (TypeError, ValueError):
        return False
    if expiry < int(now()):
        return False
    return hmac.compare_digest(sign_media(job_id, filename, expiry), signature or "")


def job_detail(job_id: str) -> Optional[Dict[str, Any]]:
    with db() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            return None
        stages = connection.execute(
            "SELECT stage, status, started_at, completed_at, message FROM job_stages WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        stage_map = {row["stage"]: dict(row) for row in stages}
        ordered_stages = [stage_map.get(stage, {"stage": stage, "status": "pending"}) for stage in ALL_STAGES]
        logs = connection.execute(
            "SELECT timestamp, stage, message FROM job_logs WHERE job_id = ? ORDER BY id DESC LIMIT 80",
            (job_id,),
        ).fetchall()
        clips = connection.execute(
            "SELECT * FROM clips WHERE job_id = ? ORDER BY created_at ASC",
            (job_id,),
        ).fetchall()
    job_data = row_to_job(job)
    job_data["stages"] = ordered_stages
    job_data["logs"] = [dict(row) for row in reversed(logs)]
    job_data["clips"] = [
        {
            **dict(clip),
            "stream_url": signed_media_url(job_id, clip["filename"]),
            "download_url": signed_media_url(job_id, clip["filename"]),
        }
        for clip in clips
    ]
    return job_data


def reset_for_retry(job_id: str) -> bool:
    with db() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job or job["status"] != "failed":
            return False
        output_dir = Path(job["output_dir"])
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        connection.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        connection.execute("DELETE FROM job_stages WHERE job_id = ?", (job_id,))
        for stage in ALL_STAGES:
            status = "active" if stage == "queued" else "pending"
            connection.execute(
                "INSERT INTO job_stages (job_id, stage, status, started_at, message) VALUES (?, ?, ?, ?, ?)",
                (job_id, stage, status, now() if stage == "queued" else None, "Retry queued" if stage == "queued" else ""),
            )
        connection.execute(
            "UPDATE jobs SET status='queued', active_stage='queued', started_at=NULL, completed_at=NULL, retry_count=retry_count+1, error_stage=NULL, error_message=NULL, clip_count=0 WHERE id=?",
            (job_id,),
        )
    log_event(job_id, "queued", "Retry requested; job re-queued from the beginning")
    return True


def claim_next_job() -> Optional[Dict[str, Any]]:
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1").fetchone()
        if not row:
            return None
        connection.execute("UPDATE jobs SET status='downloading', active_stage='downloading', started_at=? WHERE id=? AND status='queued'", (now(), row["id"]))
        connection.execute("UPDATE job_stages SET status='complete', completed_at=? WHERE job_id=? AND stage='queued'", (now(), row["id"]))
        connection.execute("UPDATE job_stages SET status='active', started_at=?, message=? WHERE job_id=? AND stage='downloading'", (now(), "Worker claimed local job", row["id"]))
        claimed = connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
    log_event(row["id"], "downloading", "Local worker claimed job")
    return row_to_job(claimed)


def persist_results(job_id: str, result: Dict[str, Any]) -> int:
    valid = []
    for short in result.get("shorts", []):
        path_value = short.get("clip_url")
        if not path_value:
            continue
        file_path = Path(path_value).resolve()
        job_root = JOBS_ROOT / job_id
        try:
            file_path.relative_to(job_root.resolve())
            validate_clip(str(file_path))
        except (ValueError, RuntimeError):
            continue
        valid.append((short, file_path))
    if not valid:
        raise RuntimeError("No validated captioned clips were produced.")
    with db() as connection:
        connection.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        for short, path in valid:
            connection.execute(
                "INSERT INTO clips (id, job_id, file_path, filename, title, score, hook_sentence, start_time, end_time, duration, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    job_id,
                    str(path),
                    path.name,
                    str(short.get("title") or "Untitled clip"),
                    int(short.get("score") or 0),
                    str(short.get("hook_sentence") or ""),
                    float(short.get("start_time") or 0),
                    float(short.get("end_time") or 0),
                    float(short.get("end_time") or 0) - float(short.get("start_time") or 0),
                    now(),
                ),
            )
        connection.execute("UPDATE jobs SET clip_count=? WHERE id=?", (len(valid), job_id))
    return len(valid)


def run_job(job: Dict[str, Any]) -> None:
    job_id = job["id"]
    output_dir = Path(job["output_dir"])

    def progress(stage: str, message: str) -> None:
        set_stage(job_id, stage, message)

    try:
        result = generate_shorts(
            job["source_url"],
            num_clips=int(job["num_clips"]),
            aspect_ratio=job["aspect_ratio"],
            download_format=job["quality"],
            mode="local",
            output_dir=str(output_dir),
            progress_callback=progress,
        )
        count = persist_results(job_id, result)
        set_stage(job_id, "done", f"Validated and stored {count} finished clip(s) locally")
    except Exception as exc:
        set_stage(job_id, "failed", str(exc))


def worker_loop() -> None:
    while True:
        job = claim_next_job()
        if job:
            run_job(job)
        else:
            time.sleep(POLL_SECONDS)


def ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=worker_loop, name="yt-clips-local-worker", daemon=True)
        _worker.start()


@app.get("/")
def dashboard() -> str:
    return render_template_string(PAGE_TEMPLATE, page="dashboard")


@app.get("/jobs/<job_id>")
def job_page(job_id: str) -> str:
    return render_template_string(PAGE_TEMPLATE, page="job", job_id=job_id)


@app.get("/api/jobs")
def api_jobs() -> Response:
    with db() as connection:
        rows = connection.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
    return jsonify([row_to_job(row) for row in rows])


@app.post("/api/jobs")
def api_create_job() -> Response:
    payload = request.get_json(silent=True) or {}
    try:
        job_id = create_job(
            str(payload.get("source_url", "")),
            int(payload.get("num_clips", 3)),
            str(payload.get("aspect_ratio", "9:16")),
            str(payload.get("quality", "1080")),
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": job_id}), 201


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str) -> Response:
    detail = job_detail(job_id)
    if not detail:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(detail)


@app.post("/api/jobs/<job_id>/retry")
def api_retry(job_id: str) -> Response:
    if not reset_for_retry(job_id):
        return jsonify({"error": "Only failed jobs can be retried."}), 400
    return jsonify({"id": job_id, "status": "queued"})


@app.get("/media/<job_id>/<filename>")
def media(job_id: str, filename: str):
    if not is_valid_signature(job_id, filename, request.args.get("exp", ""), request.args.get("sig", "")):
        abort(403)
    with db() as connection:
        clip = connection.execute("SELECT file_path FROM clips WHERE job_id=? AND filename=?", (job_id, filename)).fetchone()
    if not clip:
        abort(404)
    path = Path(clip["file_path"]).resolve()
    try:
        path.relative_to((JOBS_ROOT / job_id).resolve())
    except ValueError:
        abort(403)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="video/mp4", conditional=True, as_attachment=request.args.get("download") == "1", download_name=filename)


PAGE_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cutroom — Local Clip Studio</title><link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700;800&family=Playfair+Display:ital,wght@0,600;0,700;1,600&display=swap" rel="stylesheet">
<style>
:root{--ink:#111520;--ink-2:#1b2130;--cream:#f6f3ed;--paper:#fffdf9;--muted:#7a817f;--line:#e7e1d7;--lime:#c9ef6f;--lime-dark:#7b9c2d;--gold:#dcaa5b;--rose:#db6e76;--blue:#87b7dc}*{box-sizing:border-box}body{margin:0;background:var(--cream);color:var(--ink);font-family:Manrope,Arial,sans-serif}.shell{min-height:100vh;display:grid;grid-template-columns:250px 1fr}.side{background:var(--ink);color:#eef1eb;padding:28px 18px;display:flex;flex-direction:column;position:sticky;top:0;height:100vh}.brand{font-size:22px;letter-spacing:-1px;font-weight:800;padding:4px 10px 34px}.brand i{font-family:'Playfair Display',serif;color:var(--lime);font-weight:600}.brand-mark{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:7px;background:var(--lime);color:var(--ink);font-size:12px;margin-right:8px}.nav-label{font:10px 'DM Mono',monospace;letter-spacing:1.3px;text-transform:uppercase;color:#8e98a7;padding:0 10px 10px}.nav a{color:#abb3bf;text-decoration:none;display:flex;gap:10px;align-items:center;border-radius:11px;padding:12px 10px;margin:4px 0;font-size:14px;font-weight:600}.nav a.active,.nav a:hover{color:#111520;background:#f4f5f0}.nav-dot{width:7px;height:7px;border-radius:99px;background:var(--lime)}.side-foot{margin-top:auto;border-top:1px solid #2d3444;padding:18px 10px 2px;font-size:12px;color:#99a3b0;line-height:1.5}.main{padding:42px clamp(22px,5vw,76px) 70px;max-width:1580px;width:100%;margin:0 auto}.topline{display:flex;justify-content:space-between;align-items:center;margin-bottom:40px}.eyebrow{font:11px 'DM Mono',monospace;letter-spacing:1.3px;text-transform:uppercase;color:#72797d}.local-pill{display:flex;gap:8px;align-items:center;background:#e9f6cf;border:1px solid #d8ebaf;color:#496618;padding:8px 11px;border-radius:999px;font:11px 'DM Mono',monospace}.pulse{width:7px;height:7px;border-radius:50%;background:#6f9b20;box-shadow:0 0 0 4px #d8edb6}h1{font:700 clamp(38px,5vw,68px)/.98 'Playfair Display',serif;letter-spacing:-2.6px;margin:0 0 15px}.lede{max-width:650px;color:#66706e;font-size:16px;line-height:1.7;margin:0}.dash-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(300px,.75fr);gap:22px;margin-top:38px}.card{background:var(--paper);border:1px solid var(--line);border-radius:22px;box-shadow:0 8px 30px #5f523612}.submit{padding:28px}.card-top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:26px}.card-title{font-size:18px;font-weight:800;letter-spacing:-.7px}.card-sub{font-size:13px;color:var(--muted);margin-top:4px}.form-label{font:11px 'DM Mono',monospace;letter-spacing:1px;text-transform:uppercase;color:#6f7673;margin:18px 0 8px;display:block}.url-row{display:flex;gap:10px}.url-input{flex:1;border:1px solid #d8d4ca;border-radius:12px;padding:15px 16px;font:14px Manrope;background:#fff;outline:none;transition:.18s}.url-input:focus{border-color:#9ec33e;box-shadow:0 0 0 4px #eaf6c9}.choice-row{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.choice-row.two{grid-template-columns:repeat(2,1fr)}.choice{border:1px solid #dcd7ce;border-radius:12px;padding:12px 10px;text-align:center;cursor:pointer;background:#fff;font-size:13px;font-weight:700;transition:.16s}.choice.active{background:var(--ink);border-color:var(--ink);color:var(--cream)}.choice:hover{transform:translateY(-1px)}.submit-bar{display:flex;gap:13px;align-items:center;border-top:1px solid var(--line);margin-top:25px;padding-top:20px}.primary{border:0;background:var(--lime);color:var(--ink);border-radius:12px;padding:13px 18px;font-weight:800;font-family:Manrope;cursor:pointer;transition:.16s;box-shadow:inset 0 -2px #9ab84d}.primary:hover{background:#d7f68d;transform:translateY(-1px)}.primary:active{transform:scale(.97)}.form-note{font-size:12px;color:#78817f}.flow-card{background:var(--ink-2);color:#f8faf3;padding:27px;overflow:hidden;position:relative}.flow-card:after{content:'';position:absolute;width:230px;height:230px;background:var(--lime);filter:blur(0);border-radius:50%;right:-130px;bottom:-140px;opacity:.12}.flow-title{font:600 28px/1.04 'Playfair Display',serif;letter-spacing:-.9px;margin:0 0 18px}.flow-list{position:relative;z-index:1}.flow-step{display:flex;align-items:center;gap:11px;padding:10px 0;border-bottom:1px solid #303849;font:12px 'DM Mono',monospace;color:#bac2cd}.flow-step:last-child{border-bottom:0}.flow-num{width:22px;height:22px;display:grid;place-items:center;border:1px solid #596477;border-radius:50%;font-size:10px;color:var(--lime)}.history{margin-top:42px}.section-head{display:flex;justify-content:space-between;align-items:end;margin-bottom:15px}.section-head h2{margin:0;font:700 29px 'Playfair Display',serif;letter-spacing:-1px}.section-head p{margin:0;font-size:13px;color:var(--muted)}.job-list{background:var(--paper);border:1px solid var(--line);border-radius:20px;overflow:hidden}.job-row{display:grid;grid-template-columns:92px minmax(120px,1.3fr) 100px 100px 125px 36px;gap:16px;align-items:center;padding:18px 22px;border-bottom:1px solid #eee9e0;text-decoration:none;color:inherit;transition:.15s}.job-row:last-child{border-bottom:0}.job-row:hover{background:#faf8f3}.job-id{font:11px 'DM Mono',monospace;color:#777}.job-url{font-size:13px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.job-meta{font-size:12px;color:#777}.badge{display:inline-flex;justify-content:center;width:max-content;padding:6px 9px;border-radius:999px;font:10px 'DM Mono',monospace;text-transform:uppercase;letter-spacing:.4px}.badge.queued,.badge.downloading,.badge.transcribing,.badge.segmenting,.badge.ranking,.badge.cropping,.badge.captioning{background:#eef3ff;color:#416c9d}.badge.done{background:#e9f7d2;color:#557d1e}.badge.failed{background:#ffe9eb;color:#ad4550}.arrow{font-size:22px;color:#81908e}.empty{padding:48px 30px;color:#79817d;text-align:center}.empty strong{display:block;color:#303938;margin-bottom:5px}.job-hero{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:30px}.back{color:#626a68;text-decoration:none;font:12px 'DM Mono',monospace}.job-url-big{max-width:700px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#6f7776;font-size:14px;margin-top:10px}.job-actions{display:flex;gap:10px;align-items:center}.outline{border:1px solid #cfc9bf;background:#fff;border-radius:11px;padding:11px 14px;font:700 12px Manrope;cursor:pointer}.outline:hover{background:#f6f4ee}.progress-panel{background:var(--paper);border:1px solid var(--line);padding:25px;border-radius:22px}.progress-head{display:flex;justify-content:space-between;align-items:center}.elapsed{font:12px 'DM Mono',monospace;color:#6e7674}.stage-track{display:grid;grid-template-columns:repeat(8,minmax(76px,1fr));gap:5px;margin:26px 0 6px;overflow:auto;padding-bottom:10px}.stage{min-width:82px;border:1px solid #e2ddd4;border-radius:13px;padding:11px 9px;background:#fff}.stage-name{font:10px 'DM Mono',monospace;text-transform:uppercase;letter-spacing:.2px;color:#717977;white-space:nowrap}.stage-dot{display:block;width:8px;height:8px;border-radius:50%;background:#d8d9d3;margin-bottom:9px}.stage.active{border-color:#bbd97c;background:#f6fce8}.stage.active .stage-dot{background:#85ad2f;box-shadow:0 0 0 4px #e7f4c9}.stage.complete{border-color:#d2e9aa;background:#fbfff3}.stage.complete .stage-dot{background:#9fc847}.stage.error{border-color:#f2b3b7;background:#fff7f7}.stage.error .stage-dot{background:#d85e68}.detail-grid{display:grid;grid-template-columns:.9fr 1.1fr;gap:22px;margin-top:22px}.log-card{background:var(--ink);border-radius:20px;padding:23px;min-height:340px;color:#dce3dd}.log-title{font:11px 'DM Mono',monospace;letter-spacing:1px;text-transform:uppercase;color:#8f9da4;margin-bottom:15px}.log{font:12px/1.65 'DM Mono',monospace;max-height:370px;overflow:auto}.log-line{padding:8px 0;border-bottom:1px solid #263142}.log-time{color:#778692}.log-stage{color:var(--lime);margin:0 7px}.results-card{background:var(--paper);border:1px solid var(--line);border-radius:20px;padding:23px}.results-title{font:700 21px 'Playfair Display',serif;margin:0 0 16px}.clip-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}.clip{border:1px solid #e3ded6;border-radius:15px;overflow:hidden;background:#fff}.clip video{display:block;width:100%;aspect-ratio:9/16;object-fit:cover;background:#111}.clip-info{padding:12px}.clip-name{font-weight:800;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.clip-metrics{display:flex;gap:8px;margin:8px 0;color:#6f7775;font:11px 'DM Mono',monospace}.download{display:inline-flex;font-size:11px;font-weight:800;color:#4a641b;text-decoration:none;margin-top:4px}.error-box{background:#fff0f1;border:1px solid #f1c1c5;border-radius:14px;padding:14px;font-size:13px;color:#963c47;margin-top:18px}@media(max-width:920px){.shell{grid-template-columns:1fr}.side{height:auto;position:static;padding:18px;display:block}.brand{padding:0 0 12px}.nav{display:flex;gap:5px}.nav-label,.side-foot{display:none}.nav a{margin:0}.main{padding:28px 18px}.dash-grid,.detail-grid{grid-template-columns:1fr}.job-row{grid-template-columns:72px 1fr 78px 30px}.job-row .hide-small{display:none}.stage-track{grid-template-columns:repeat(8,96px)}.job-hero{display:block}.job-actions{margin-top:18px}}
</style></head><body><div class="shell"><aside class="side"><div class="brand"><span class="brand-mark">▶</span>cut<i>room</i></div><div class="nav-label">Workspace</div><nav class="nav"><a class="{{ 'active' if page=='dashboard' else '' }}" href="/"><span class="nav-dot"></span>New project</a><a class="{{ 'active' if page=='job' else '' }}" href="/"><span>◌</span>Job history</a></nav><div class="side-foot">LOCAL-ONLY MODE<br>Mac Mini • 127.0.0.1<br>All files stay on this machine</div></aside><main class="main">{% if page == 'dashboard' %}<div class="topline"><span class="eyebrow">Personal clip factory / v1.0</span><span class="local-pill"><span class="pulse"></span>Local worker online</span></div><h1>From long-form<br>to <i>finished</i> moments.</h1><p class="lede">Submit a YouTube link. Your Mac runs the complete context-safe clipping workflow locally — then gives you captioned vertical clips ready to review.</p><section class="dash-grid"><form id="submit-form" class="card submit"><div class="card-top"><div><div class="card-title">Start a new cut</div><div class="card-sub">Every job stays on this Mac Mini.</div></div><span class="badge queued">local</span></div><label class="form-label">YouTube source</label><div class="url-row"><input required id="source_url" class="url-input" type="url" placeholder="https://youtube.com/watch?v=..."></div><label class="form-label">Finished clips</label><div id="count-choices" class="choice-row">{% for n in range(1,11) %}<button class="choice {{ 'active' if n==3 else '' }}" data-value="{{n}}" type="button">{{n}}</button>{% endfor %}</div><label class="form-label">Frame</label><div id="ratio-choices" class="choice-row two"><button class="choice active" data-value="9:16" type="button">9:16 &nbsp; Vertical</button><button class="choice" data-value="1:1" type="button">1:1 &nbsp; Square</button></div><label class="form-label">Source quality</label><div id="quality-choices" class="choice-row two"><button class="choice" data-value="720" type="button">720p</button><button class="choice active" data-value="1080" type="button">1080p</button></div><div class="submit-bar"><button class="primary" type="submit">Start local pipeline&nbsp; →</button><span class="form-note" id="form-note">Jobs process one at a time for stable local rendering.</span></div></form><aside class="card flow-card"><div class="eyebrow" style="color:#97a4ae">How this one runs</div><h2 class="flow-title">A complete<br>edit, not a<br>random cut.</h2><div class="flow-list">{% for stage in ['Download source','Transcribe words','Segment context','Rank complete beats','Crop and reframe','Caption and validate'] %}<div class="flow-step"><span class="flow-num">{{loop.index}}</span>{{stage}}</div>{% endfor %}</div></aside></section><section class="history"><div class="section-head"><div><h2>Recent jobs</h2><p>Your local archive of completed and in-progress cuts.</p></div><span class="eyebrow" id="job-count">0 jobs</span></div><div class="job-list" id="job-list"><div class="empty"><strong>No clips in the room yet.</strong>Submit a source above to start your first local job.</div></div></section>{% else %}<a class="back" href="/">← Back to all jobs</a><section class="job-hero"><div><div class="topline" style="margin:18px 0 0"><span class="eyebrow">Local pipeline job</span><span id="job-badge" class="badge queued">Loading</span></div><h1 style="font-size:clamp(34px,4vw,55px);margin-top:13px">Your cut is<br><i id="job-state">getting ready</i>.</h1><div id="job-url" class="job-url-big">Loading job details…</div></div><div class="job-actions"><span id="job-elapsed" class="elapsed">00:00</span><button id="retry" class="outline" hidden>Retry from beginning</button></div></section><section class="progress-panel"><div class="progress-head"><div><div class="card-title">Pipeline progress</div><div class="card-sub">Exactly where your Mac is in the workflow.</div></div><span class="eyebrow" id="clip-count"></span></div><div class="stage-track" id="stage-track"></div><div id="error-box"></div></section><section class="detail-grid"><section class="log-card"><div class="log-title">Live local log</div><div class="log" id="log">Connecting to local job record…</div></section><section class="results-card"><h2 class="results-title">Finished clips</h2><div id="results"><div class="empty">Validated clips will appear here as each job finishes.</div></div></section></section>{% endif %}</main></div><script>
const currentJob={{ job_id|tojson if page=='job' else 'null' }};let selection={num_clips:3,aspect_ratio:'9:16',quality:'1080'};
function fmtTime(seconds){if(!seconds)return'00:00';let s=Math.max(0,Math.floor(seconds)),m=Math.floor(s/60);return String(m).padStart(2,'0')+':'+String(s%60).padStart(2,'0')}
function fmtDate(ts){return new Date(ts*1000).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}
function badge(status){return '<span class="badge '+status+'">'+status+'</span>'}
function initChoices(id,key){document.querySelectorAll('#'+id+' .choice').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('#'+id+' .choice').forEach(x=>x.classList.remove('active'));b.classList.add('active');selection[key]=key==='num_clips'?Number(b.dataset.value):b.dataset.value}))}
async function loadJobs(){let list=document.getElementById('job-list');if(!list)return;let r=await fetch('/api/jobs'),jobs=await r.json();document.getElementById('job-count').textContent=jobs.length+' job'+(jobs.length===1?'':'s');list.innerHTML=jobs.length?jobs.map(j=>'<a class="job-row" href="/jobs/'+j.id+'"><span class="job-id">#'+j.id.slice(-6)+'</span><span class="job-url" title="'+j.source_url+'">'+j.source_url+'</span><span class="hide-small">'+badge(j.status)+'</span><span class="job-meta hide-small">'+j.clip_count+' clip'+(j.clip_count===1?'':'s')+'</span><span class="job-meta">'+fmtDate(j.created_at)+'</span><span class="arrow">→</span></a>').join(''):'<div class="empty"><strong>No clips in the room yet.</strong>Submit a source above to start your first local job.</div>'}
async function submitJob(e){e.preventDefault();let note=document.getElementById('form-note'),button=e.target.querySelector('button[type=submit]');button.disabled=true;note.textContent='Adding job to local queue…';let r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...selection,source_url:document.getElementById('source_url').value})});let x=await r.json();if(!r.ok){note.textContent=x.error||'Could not create job.';button.disabled=false;return}window.location='/jobs/'+x.id}
function stageClass(s){return s.status==='complete'?'complete':s.status==='active'?'active':s.status==='error'?'error':''}
async function loadJob(){let r=await fetch('/api/jobs/'+currentJob);if(!r.ok)return;let j=await r.json();document.getElementById('job-badge').outerHTML=badge(j.status);document.getElementById('job-state').textContent=j.status==='done'?'ready.':j.status==='failed'?'paused.':j.status+'…';document.getElementById('job-url').textContent=j.source_url;document.getElementById('job-elapsed').textContent=fmtTime((j.completed_at||Date.now()/1000)-(j.started_at||j.created_at));document.getElementById('clip-count').textContent=j.clip_count?j.clip_count+' validated clip'+(j.clip_count===1?'':'s'):'';document.getElementById('stage-track').innerHTML=j.stages.map(s=>'<div class="stage '+stageClass(s)+'"><span class="stage-dot"></span><span class="stage-name">'+s.stage+'</span></div>').join('');let err=document.getElementById('error-box');err.innerHTML=j.status==='failed'?'<div class="error-box"><strong>'+j.error_stage+'</strong> — '+j.error_message+'</div>':'';let retry=document.getElementById('retry');retry.hidden=j.status!=='failed';retry.onclick=async()=>{retry.disabled=true;await fetch('/api/jobs/'+currentJob+'/retry',{method:'POST'});loadJob()};let log=document.getElementById('log');log.innerHTML=j.logs.length?j.logs.map(x=>'<div class="log-line"><span class="log-time">'+fmtDate(x.timestamp)+'</span><span class="log-stage">'+x.stage+'</span>'+x.message+'</div>').join(''):'Waiting for first local worker event…';log.scrollTop=log.scrollHeight;let results=document.getElementById('results');results.innerHTML=j.clips.length?'<div class="clip-grid">'+j.clips.map(c=>'<article class="clip"><video controls preload="metadata" src="'+c.stream_url+'"></video><div class="clip-info"><div class="clip-name">'+c.title+'</div><div class="clip-metrics"><span>'+c.score+'/100</span><span>'+c.duration.toFixed(1)+'s</span></div><div style="font-size:11px;color:#68706e;line-height:1.4">'+(c.hook_sentence||'Validated local clip')+'</div><a class="download" href="'+c.download_url+'&download=1">Download MP4 ↓</a></div></article>').join('')+'</div>':'<div class="empty">Validated clips will appear here as each job finishes.</div>';if(!['done','failed'].includes(j.status))setTimeout(loadJob,2000)}
if(currentJob){loadJob()}else{initChoices('count-choices','num_clips');initChoices('ratio-choices','aspect_ratio');initChoices('quality-choices','quality');document.getElementById('submit-form').addEventListener('submit',submitJob);loadJobs();setInterval(loadJobs,4000)}
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the fully local YT Clips Studio dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Default is localhost only")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    init_storage()
    ensure_worker()
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"YT Clips Studio is local-only at {url}", flush=True)
    try:
        from waitress import serve
    except ImportError as exc:
        raise RuntimeError("waitress is required for the local dashboard. Install requirements-local.txt.") from exc
    serve(app, host=args.host, port=args.port, threads=8)


if __name__ == "__main__":
    main()
