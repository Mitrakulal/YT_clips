#!/usr/bin/env python3
"""Push a completed job's clips to the private YT_clips_media repo.

Usage: push_job_media.py <job_id>
Safe to call from the pipeline: all failures are printed, never raised.
Auth uses the stored macOS keychain credential (same as manual pushes).
"""
import datetime
import sqlite3
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
YT = HOME / "YT_clips"
DB = YT / "studio_data/studio.sqlite3"
MIRROR = HOME / "YT_clips_media"
REMOTE = "https://github.com/Mitrakulal/YT_clips_media.git"


def sh(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(
        list(args), cwd=cwd, capture_output=True, text=True, timeout=600
    )
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def main(job_id: str) -> int:
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    job = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        print(f"[media-push] unknown job {job_id}")
        return 1
    clips = con.execute(
        "SELECT file_path, title FROM clips WHERE job_id=?", (job_id,)
    ).fetchall()
    if not clips:
        print(f"[media-push] no clips for {job_id}, skipping")
        return 0

    if not (MIRROR / ".git").exists():
        print("[media-push] cloning media mirror ...")
        sh("git", "clone", "-q", REMOTE, str(MIRROR))
    sh("git", "-C", str(MIRROR), "fetch", "-q", "origin")
    sh("git", "-C", str(MIRROR), "checkout", "-q", "-B", "main", "origin/main")

    dest = MIRROR / job_id
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for row in clips:
        src = Path(row["file_path"])
        if not src.exists():
            print(f"[media-push] missing {src.name}, skipping")
            continue
        data = src.read_bytes()
        (dest / src.name).write_bytes(data)
        # matching thumbnail, if present
        thumb = src.with_suffix("").with_suffix(".jpg")
        if thumb.name != src.name and thumb.exists():
            (dest / thumb.name).write_bytes(thumb.read_bytes())
        else:
            alt = Path(str(src).replace(".mp4", ".jpg"))
            if alt.exists():
                (dest / alt.name).write_bytes(alt.read_bytes())
        n += 1
    (dest / "SOURCE.md").write_text(
        f"source_url: {job['source_url']}\n"
        f"job_id: {job_id}\n"
        f"clips: {n}\n"
        f"generated: {datetime.date.today().isoformat()}\n"
        f"pipeline: YT_clips local studio\n"
    )
    status = sh("git", "-C", str(MIRROR), "status", "--porcelain")
    if not status.strip():
        print(f"[media-push] {job_id}: already backed up, nothing new")
        return 0
    sh("git", "-C", str(MIRROR), "add", ".")
    sh(
        "git", "-C", str(MIRROR),
        "-c", "user.name=Mitrakulal",
        "-c", "user.email=mitrakulal@users.noreply.github.com",
        "commit", "-q", "-m", f"job {job_id}: {n} clips",
    )
    sh("git", "-C", str(MIRROR), "push", "-q", "origin", "main")
    print(f"[media-push] {job_id}: pushed {n} clips")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: push_job_media.py <job_id>")
        sys.exit(2)
    try:
        sys.exit(main(sys.argv[1]))
    except Exception as exc:  # never break the caller
        print(f"[media-push] FAILED: {exc}")
        sys.exit(1)
