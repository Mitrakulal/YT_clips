import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(cmd):
    subprocess.run(cmd, check=True)


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        os.environ["LOCAL_OUTPUT_DIR"] = str(root / "cache")
        os.environ["LOCAL_WHISPER_MODEL"] = "tiny.en"
        os.environ["LOCAL_WHISPER_DEVICE"] = "cpu"
        from shorts_generator.local.transcriber import transcribe_local
        from shorts_generator.highlights import get_highlights
        from shorts_generator.local.clipper import crop_highlights_local
        from subtitles import subtitle_burn_stage

        wav = root / "jfk.wav"
        run(["curl", "-L", "--fail", "-o", str(wav), "https://github.com/ggerganov/whisper.cpp/raw/master/samples/jfk.wav"])
        source = root / "speech_fixture.mp4"
        run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-i", str(wav), "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ])
        transcript = transcribe_local(str(source), language="en")
        assert transcript["segments"], "ASR returned no segments"
        words = [w for segment in transcript["segments"] for w in segment.get("words", [])]
        assert words, "ASR returned no word timestamps"

        def fake_llm(prompt):
            if prompt.startswith("Analyze this video transcript sample"):
                return '{"content_type":"speech","density":"high"}'
            first_id = "candidate_001"
            return json.dumps({"highlights": [{"candidate_id": first_id, "title": "Complete speech", "score": 95}]})

        result = get_highlights(transcript, num_clips=1, llm_fn=fake_llm, boundaries=[])
        selected = result["highlights"][0]
        assert selected["start_time"] >= 0
        assert selected["end_time"] > selected["start_time"]
        assert selected["end_time"] - selected["start_time"] <= 120
        shorts = crop_highlights_local(
            str(source), [selected], aspect_ratio="9:16", out_dir=str(root),
            words=words, boundaries=result.get("effective_boundaries", []),
        )
        clip = Path(shorts[0]["clip_url"])
        final = root / "short_01_captioned.mp4"
        subtitle_burn_stage(str(clip), words, selected["start_time"], selected["end_time"], str(final), hook_text=selected["title"])
        probe = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_type,r_frame_rate:format=duration",
            "-of", "json", str(final),
        ], text=True))
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
        assert [video["width"], video["height"]] == [1080, 1920]
        assert video["r_frame_rate"] == "30/1"
        assert audio["codec_type"] == "audio"
        print(json.dumps({"segments": len(transcript["segments"]), "words": len(words), "selected": selected, "probe": probe}, indent=2))


if __name__ == "__main__":
    main()
