import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import stage
import worker


class WorkerResumeTests(unittest.TestCase):
    def test_resume_after_crop_and_caption_stages_publishes_clip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inbox = root / "inbox"
            done = root / "done"
            failed = root / "failed"
            jobs = root / "jobs"
            published = root / "published"
            logs = root / "logs"
            for path in (inbox, done, failed, jobs, published, logs):
                path.mkdir(parents=True)

            job_id = "resume1"
            job = {"job_id": job_id, "source_url": "local.mp4", "num_clips": 1}
            active = root / "active.json"
            active.write_text(json.dumps(job), encoding="utf-8")
            source = root / "source.mp4"
            source.write_bytes(b"source")
            words = root / "words.json"
            words.write_text("[]", encoding="utf-8")
            boundaries = jobs / job_id / "boundaries.json"
            highlight = jobs / job_id / "highlights.json"
            clips = jobs / job_id / "clips.json"
            captioned = jobs / job_id / "captioned.json"
            state_path = jobs / job_id / "state.json"
            (jobs / job_id).mkdir(parents=True)
            for path, payload in (
                (boundaries, {"boundaries": []}),
                (highlight, {"highlights": [{"title": "x", "start_time": 0, "end_time": 10}]}),
                (clips, {"shorts": [{"title": "x", "start_time": 0, "end_time": 10, "clip_url": str(root / "clip.mp4")}]}),
            ):
                path.write_text(json.dumps(payload), encoding="utf-8")
            (root / "clip.mp4").write_bytes(b"clip")
            captioned.write_text(json.dumps({"shorts": [{"title": "x", "start_time": 0, "end_time": 10, "clip_url": str(root / "captioned.mp4")}]}), encoding="utf-8")
            (root / "captioned.mp4").write_bytes(b"captioned")
            for name, artifact in (
                ("download", source),
                ("transcribe", words),
                ("segment", boundaries),
                ("highlight_llm", highlight),
                ("crop", clips),
                ("subtitle_burn", captioned),
            ):
                pass
            state = stage.new_state(job)
            state["stages"]["download"] = {"status": "done", "artifact": str(source)}
            state["stages"]["transcribe"] = {"status": "done", "artifact": str(words)}
            state["stages"]["segment"] = {"status": "done", "artifact": str(boundaries)}
            state["stages"]["highlight_llm"] = {"status": "done", "artifact": str(highlight)}
            state["stages"]["crop"] = {"status": "done", "artifact": str(clips)}
            state["stages"]["subtitle_burn"] = {"status": "done", "artifact": str(captioned)}
            stage.save_state(state_path, state)

            with patch.object(worker, "JOBS", jobs), patch.object(worker, "PUBLISHED", published), patch.object(worker, "DONE", done), patch.object(worker, "FAILED", failed), patch.object(worker, "SEEN_PATH", root / "seen.json"), patch.object(worker, "transcribe_local", return_value={"duration": 10, "segments": []}):
                result = worker.run_job(active, set())

            self.assertTrue(result)
            self.assertTrue((published / job_id / "captioned.mp4").exists())
            self.assertTrue((done / active.name).exists())


if __name__ == "__main__":
    unittest.main()
