import tempfile
import tempfile
import unittest
from pathlib import Path

from shorts_generator.local.validate import expected_dimensions
from subtitles import build_ass


class AspectRatioContractTests(unittest.TestCase):
    def test_expected_dimensions_match_supported_ui_options(self):
        self.assertEqual(expected_dimensions("9:16"), (1080, 1920))
        self.assertEqual(expected_dimensions("1:1"), (1080, 1080))

    def test_square_ass_canvas_uses_square_play_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "caption.ass"
            build_ass([], 0, 10, str(output), canvas_size=(1080, 1080))
            content = output.read_text(encoding="utf-8")
        self.assertIn("PlayResX: 1080", content)
        self.assertIn("PlayResY: 1080", content)
        self.assertNotIn("__CAPTION_MARGIN__", content)

    def test_vertical_ass_uses_hinglish_capable_font_and_safe_caption_offset(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "caption.ass"
            build_ass([], 0, 10, str(output), canvas_size=(1080, 1920))
            content = output.read_text(encoding="utf-8")
        self.assertIn("Style: Caption,Kohinoor Devanagari", content)
        self.assertIn(",40,40,420,1", content)


if __name__ == "__main__":
    unittest.main()
