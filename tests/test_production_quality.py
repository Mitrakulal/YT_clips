import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shorts_generator.coherence import build_coherent_candidates
from shorts_generator.highlights import get_highlights
from shorts_generator.local.clipper import crop_highlights_local
from shorts_generator.local.segment import split_window_at_boundaries
import stage


class ProductionQualityTests(unittest.TestCase):
    def test_short_leading_and_trailing_sections_merge(self):
        transcript = {
            "duration": 40,
            "segments": [
                {"start": 0, "end": 2, "text": "A short lead."},
                {"start": 2.2, "end": 12, "text": "This is the complete first idea with context."},
                {"start": 12.2, "end": 25, "text": "This is the second complete idea with a payoff."},
                {"start": 25.2, "end": 27, "text": "Tiny end."},
            ],
        }
        candidates = build_coherent_candidates(transcript, boundaries=[2.1, 25.1], min_seconds=6, max_seconds=120)
        self.assertEqual(len(candidates), 1)
        self.assertIn("A short lead.", candidates[0]["text"])
        self.assertIn("Tiny end.", candidates[0]["text"])

    def test_split_helper_merges_short_leading_piece(self):
        pieces = split_window_at_boundaries(0, 20, [2, 10], 4)
        self.assertEqual(pieces, [(0, 10), (10, 20)])

    def test_ranker_can_only_select_existing_candidate_span(self):
        transcript = {
            "duration": 60,
            "segments": [
                {"start": 0, "end": 15, "text": "The first complete idea explains the problem."},
                {"start": 15.2, "end": 30, "text": "The second idea has the strongest surprising payoff."},
                {"start": 30.2, "end": 45, "text": "The third idea gives a practical example."},
                {"start": 45.2, "end": 60, "text": "The conclusion explains what to do next."},
            ],
        }
        calls = []

        def fake_llm(prompt):
            calls.append(prompt)
            if prompt.startswith("Analyze this video transcript sample"):
                return '{"content_type":"tutorial","density":"high"}'
            return json.dumps({"highlights": [{
                "candidate_id": "candidate_002",
                "title": "Strong payoff",
                "score": 98,
                "hook_sentence": "The second idea has the strongest surprising payoff.",
                "virality_reason": "Clear payoff",
                "start_time": 0,
                "end_time": 999,
            }]})

        result = get_highlights(transcript, num_clips=1, llm_fn=fake_llm, boundaries=[15.1, 30.1, 45.1])
        selected = result["highlights"][0]
        candidate = next(c for c in result["candidates"] if c["candidate_id"] == "candidate_002")
        self.assertEqual(selected["start_time"], candidate["start_time"])
        self.assertEqual(selected["end_time"], candidate["end_time"])
        self.assertNotEqual(selected["end_time"], 999)
        self.assertGreaterEqual(len(calls), 2)

    def test_ranker_batches_candidates_without_moving_safe_spans(self):
        transcript = {
            "duration": 246,
            "segments": [
                {"start": i * 13, "end": i * 13 + 12, "text": f"Complete thought {i}."}
                for i in range(18)
            ],
        }
        prompts = []

        def fake_llm(prompt):
            prompts.append(prompt)
            if prompt.startswith("Analyze this video transcript sample"):
                return '{"content_type":"tutorial","density":"high"}'
            candidate_id = re.findall(r"\[(candidate_\d+)\]", prompt)[0]
            score = 100 - int(candidate_id.rsplit("_", 1)[1])
            return json.dumps({"highlights": [{
                "candidate_id": candidate_id,
                "title": candidate_id,
                "score": score,
                "hook_sentence": "hook",
                "virality_reason": "reason",
            }]})

        boundaries = [i * 13 - 0.5 for i in range(1, 18)]
        result = get_highlights(transcript, num_clips=2, llm_fn=fake_llm, boundaries=boundaries)
        ranking_prompts = [prompt for prompt in prompts if "pre-segmented transcript" in prompt]
        self.assertEqual(len(ranking_prompts), 3)
        self.assertEqual(len(result["highlights"]), 2)
        candidate_map = {candidate["candidate_id"]: candidate for candidate in result["candidates"]}
        for selected in result["highlights"]:
            candidate = candidate_map[selected["candidate_id"]]
            self.assertEqual(selected["start_time"], candidate["start_time"])
            self.assertEqual(selected["end_time"], candidate["end_time"])

    def test_comedy_keeps_internal_pause_boundaries_inside_one_candidate(self):
        transcript = {
            "duration": 45,
            "segments": [
                {"start": 0, "end": 12, "text": "The setup explains the joke."},
                {"start": 15, "end": 30, "text": "The punchline lands with the reaction."},
                {"start": 33, "end": 45, "text": "The beat closes with a final tag."},
            ],
        }

        def fake_llm(prompt):
            if prompt.startswith("Analyze this video transcript sample"):
                return '{"content_type":"comedy","density":"medium"}'
            return '{"highlights":[{"candidate_id":"candidate_001","title":"Complete joke","score":95}]}'

        result = get_highlights(transcript, num_clips=1, llm_fn=fake_llm, boundaries=[12.5, 30.5])
        self.assertEqual(result["effective_boundaries"], [])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIn("punchline", result["candidates"][0]["text"])

    def test_renderer_never_splits_a_ranked_complete_candidate(self):
        highlight = {
            "title": "A complete joke",
            "start_time": 10.0,
            "end_time": 46.0,
            "score": 98,
        }
        with tempfile.TemporaryDirectory() as td:
            with patch("shorts_generator.local.clipper.crop_clip_local") as crop:
                results = crop_highlights_local(
                    "source.mp4", [highlight], out_dir=td, boundaries=[20.0, 33.0]
                )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["start_time"], 10.0)
        self.assertEqual(results[0]["end_time"], 46.0)
        self.assertTrue(results[0]["clip_url"].endswith("short_01.mp4"))
        crop.assert_called_once()
        self.assertEqual(crop.call_args.args[1:3], (10.0, 46.0))

    def test_renderer_keeps_only_a_bounded_silent_reaction_tail(self):
        highlight = {
            "title": "A complete joke",
            "start_time": 10.0,
            "end_time": 20.0,
            "score": 98,
            "reaction_tail_seconds": 0.5,
        }
        words = [
            {"start": 10.0, "end": 20.0, "word": "Punchline"},
            {"start": 21.0, "end": 22.0, "word": "Next"},
        ]
        with tempfile.TemporaryDirectory() as td:
            with patch("shorts_generator.local.clipper.crop_clip_local") as crop:
                results = crop_highlights_local("source.mp4", [highlight], out_dir=td, words=words)
        self.assertEqual(results[0]["start_time"], 10.0)
        self.assertEqual(results[0]["end_time"], 20.5)
        self.assertEqual(crop.call_args.args[1:3], (10.0, 20.5))

    def test_candidate_ranking_prompt_audits_for_unfinished_endings(self):
        prompts = []

        def fake_llm(prompt):
            prompts.append(prompt)
            if prompt.startswith("Analyze this video transcript sample"):
                return '{"content_type":"interview","density":"medium"}'
            return '{"highlights":[{"candidate_id":"candidate_001","score":95}]}'

        transcript = {
            "duration": 30,
            "segments": [
                {"start": 0, "end": 14, "text": "This thought reaches a complete conclusion."},
                {"start": 15, "end": 30, "text": "A separate thought begins with its own ending."},
            ],
        }
        get_highlights(transcript, num_clips=1, llm_fn=fake_llm, boundaries=[14.5])
        self.assertTrue(any("opening audit" in prompt and "ending audit" in prompt for prompt in prompts))

    def test_stage_done_requires_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            state = stage.new_state({"job_id": "x"})
            stage.mark_stage(state, "crop", "done", artifact=str(Path(td) / "missing.json"))
            self.assertFalse(stage.stage_done(state, "crop"))
            artifact = Path(td) / "clips.json"
            artifact.write_text("{}", encoding="utf-8")
            stage.mark_stage(state, "crop", "done", artifact=str(artifact))
            self.assertTrue(stage.stage_done(state, "crop"))


if __name__ == "__main__":
    unittest.main()
