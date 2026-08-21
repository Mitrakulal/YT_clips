import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_studio


class LocalStudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(local_studio, "DATA_ROOT", root / "data"),
            patch.object(local_studio, "JOBS_ROOT", root / "data" / "jobs"),
            patch.object(local_studio, "DB_PATH", root / "data" / "studio.sqlite3"),
            patch.object(local_studio, "SECRET_PATH", root / "data" / ".secret"),
        ]
        for item in self.patches:
            item.start()
        local_studio.init_storage()
        self.client = local_studio.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_creates_job_with_exact_stage_sequence(self):
        response = self.client.post("/api/jobs", json={
            "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
            "num_clips": 3,
            "aspect_ratio": "9:16",
            "quality": "1080",
        })
        self.assertEqual(response.status_code, 201)
        job = self.client.get(f"/api/jobs/{response.json['id']}").json
        self.assertEqual([stage["stage"] for stage in job["stages"]], local_studio.ALL_STAGES)
        self.assertEqual(job["status"], "queued")

    def test_rejects_non_youtube_source(self):
        response = self.client.post("/api/jobs", json={
            "source_url": "https://example.com/video",
            "num_clips": 3,
            "aspect_ratio": "9:16",
            "quality": "1080",
        })
        self.assertEqual(response.status_code, 400)

    def test_signed_local_media_url_expires_and_validates(self):
        job_id = local_studio.create_job("https://youtu.be/abcdefghijk", 1, "9:16", "720")
        url = local_studio.signed_media_url(job_id, "short_01_captioned.mp4", lifetime_seconds=60)
        self.assertIn("sig=", url)
        self.assertTrue(local_studio.is_valid_signature(job_id, "short_01_captioned.mp4", url.split("exp=")[1].split("&")[0], url.split("sig=")[1]))

    def test_retry_resets_a_failed_job_to_queued(self):
        job_id = local_studio.create_job("https://youtu.be/abcdefghijk", 1, "9:16", "720")
        local_studio.set_stage(job_id, "downloading", "Starting source download")
        local_studio.set_stage(job_id, "failed", "Source is unavailable")
        response = self.client.post(f"/api/jobs/{job_id}/retry")
        self.assertEqual(response.status_code, 200)
        job = self.client.get(f"/api/jobs/{job_id}").json
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["active_stage"], "queued")
        self.assertEqual(job["retry_count"], 1)

    def test_versioned_migration_applies_without_losing_existing_rows(self):
        with local_studio.db() as connection:
            connection.execute("CREATE TABLE preserved_note (message TEXT NOT NULL)")
            connection.execute("INSERT INTO preserved_note (message) VALUES ('keep me')")
        local_studio.init_storage()
        with local_studio.db() as connection:
            migration = connection.execute("SELECT version, filename FROM schema_migrations").fetchone()
            preserved = connection.execute("SELECT message FROM preserved_note").fetchone()
            jobs_table = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
        self.assertEqual(migration["version"], 1)
        self.assertEqual(migration["filename"], "001_initial.sql")
        self.assertEqual(preserved["message"], "keep me")
        self.assertIsNotNone(jobs_table)


if __name__ == "__main__":
    unittest.main()
