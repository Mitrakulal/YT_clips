import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shorts_generator.local.clipper import crop_highlights_local
from subtitles import subtitle_burn_stage


def run(cmd):
    subprocess.run(cmd, check=True)


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = root / "fixture.mp4"
        run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ])
        words = []
        for i, word in enumerate("This complete context starts with a hook and ends after the full point is made".split()):
            start = 1.0 + i * 0.35
            words.append({"start": start, "end": start + 0.25, "word": word})
        highlights = [{"title": "Complete context", "start_time": 0.5, "end_time": 15.0, "score": 99}]
        shorts = crop_highlights_local(str(source), highlights, aspect_ratio="9:16", out_dir=str(root), words=words, boundaries=[])
        clip = Path(shorts[0]["clip_url"])
        final = root / "short_01_captioned.mp4"
        subtitle_burn_stage(str(clip), words, 0.5, 15.0, str(final), hook_text="Complete context")
        probe = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_type,r_frame_rate:format=duration",
            "-of", "json", str(final),
        ], text=True)
        print(json.dumps({"output": str(final), "probe": json.loads(probe)}, indent=2))
        assert final.exists() and final.stat().st_size > 0
        data = json.loads(probe)
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        audio = next(s for s in data["streams"] if s["codec_type"] == "audio")
        assert [video["width"], video["height"]] == [1080, 1920]
        assert video["r_frame_rate"] == "30/1"
        assert audio["codec_type"] == "audio"


if __name__ == "__main__":
    main()
