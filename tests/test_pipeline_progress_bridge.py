import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shorts_generator.pipeline import generate_shorts


class PipelineProgressBridgeTests(unittest.TestCase):
    def test_local_pipeline_emits_required_stage_sequence_and_uses_job_directory(self):
        transcript = {
            "duration": 20,
            "segments": [
                {
                    "start": 0.0,
                    "end": 20.0,
                    "text": "A complete thought with enough useful context for a short clip.",
                    "words": [],
                }
            ],
        }
        highlights = {
            "highlights": [
                {
                    "title": "Complete thought",
                    "start_time": 0.0,
                    "end_time": 20.0,
                    "score": 91,
                    "hook_sentence": "A complete thought",
                }
            ],
            "effective_boundaries": [],
        }
        stages = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            raw_clip = root / "clips" / "short_01.mp4"

            def fake_crop(*args, **kwargs):
                self.assertEqual(Path(kwargs["out_dir"]), root / "clips")
                return [{
                    "clip_url": str(raw_clip),
                    "title": "Complete thought",
                    "start_time": 0.0,
                    "end_time": 20.0,
                    "score": 91,
                    "hook_sentence": "A complete thought",
                }]

            with patch("shorts_generator.local.downloader.download_youtube_local", return_value=str(source)), \
                 patch("shorts_generator.local.transcriber.transcribe_local", return_value=transcript), \
                 patch("shorts_generator.local.segment.compute_boundaries", return_value=[]), \
                 patch("shorts_generator.pipeline.get_highlights", return_value=highlights), \
                 patch("shorts_generator.local.clipper.crop_highlights_local", side_effect=fake_crop), \
                 patch("subtitles.subtitle_burn_stage"), \
                 patch("shorts_generator.local.validate.validate_clip"), \
                 patch("thumbnail.thumbnail_stage", return_value=None):
                result = generate_shorts(
                    "https://youtu.be/abcdefghijk",
                    mode="local",
                    output_dir=str(root),
                    progress_callback=lambda stage, _message: stages.append(stage),
                )

        self.assertEqual(
            stages,
            ["downloading", "transcribing", "segmenting", "ranking", "cropping", "captioning"],
        )
        self.assertEqual(result["shorts"][0]["title"], "Complete thought")

    def test_source_caption_mode_preserves_source_subtitles_without_burning_a_second_track(self):
        transcript = {"duration": 20, "segments": [{"start": 0.0, "end": 20.0, "text": "A complete thought.", "words": []}]}
        highlights = {"highlights": [{"title": "Complete thought", "start_time": 0.0, "end_time": 20.0, "score": 91}], "effective_boundaries": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            raw_clip = root / "clips" / "short_01.mp4"
            source.write_bytes(b"source")
            with patch("shorts_generator.local.downloader.download_youtube_local", return_value=str(source)), \
                 patch("shorts_generator.local.transcriber.transcribe_local", return_value=transcript), \
                 patch("shorts_generator.local.segment.compute_boundaries", return_value=[]), \
                 patch("shorts_generator.pipeline.get_highlights", return_value=highlights), \
                 patch("shorts_generator.local.clipper.crop_highlights_local", return_value=[{**highlights["highlights"][0], "clip_url": str(raw_clip)}]), \
                 patch("subtitles.subtitle_burn_stage") as burn, \
                 patch("shorts_generator.local.validate.validate_clip") as validate, \
                 patch("thumbnail.thumbnail_stage", return_value=None):
                result = generate_shorts("https://youtu.be/abcdefghijk", mode="local", output_dir=str(root), caption_mode="source")
        burn.assert_not_called()
        validate.assert_called_once_with(str(raw_clip), aspect_ratio="9:16")
        self.assertEqual(result["caption_mode"], "source")
        self.assertEqual(result["shorts"][0]["clip_url"], str(raw_clip))


if __name__ == "__main__":
    unittest.main()
