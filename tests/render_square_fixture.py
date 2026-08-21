import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shorts_generator.local.clipper import crop_highlights_local
from shorts_generator.local.validate import validate_clip
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
            "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ])
        words = [{"start": 1 + idx * 0.3, "end": 1.2 + idx * 0.3, "word": word} for idx, word in enumerate("A complete square clip keeps all important context intact".split())]
        shorts = crop_highlights_local(
            str(source),
            [{"title": "Square context", "start_time": 0.5, "end_time": 10.0, "score": 99}],
            aspect_ratio="1:1", out_dir=str(root), words=words, boundaries=[],
        )
        final = root / "square_captioned.mp4"
        subtitle_burn_stage(str(shorts[0]["clip_url"]), words, 0.5, 10.0, str(final), hook_text="Square context", aspect_ratio="1:1")
        data = validate_clip(str(final), aspect_ratio="1:1")
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
