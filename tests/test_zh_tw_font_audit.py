import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "zh_tw_font_audit.py"
spec = importlib.util.spec_from_file_location("zh_tw_font_audit", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FontAuditTests(unittest.TestCase):
    def test_gbk_two_byte_code_is_big_endian(self):
        raw = "遊".encode("gbk")
        self.assertEqual(len(raw), 2)
        self.assertEqual(mod.glyph_code_for_char("遊"), (raw[0] << 8) | raw[1])

    def test_font_coverage_detects_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            font_dir = root / "fonts"
            font_dir.mkdir()
            chars = ["遊", "戲"]
            glyphs = [{
                "letter": mod.glyph_code_for_char("遊"),
                "pixelWidth": 12, "pixelHeight": 12,
                "s0": 0.1, "s1": 0.2, "t0": 0.1, "t1": 0.2,
            }]
            data = {
                "_type": "font",
                "_game": "iw3",
                "font": {
                    "pixelHeight": 16,
                    "glyphs": glyphs,
                },
            }
            (font_dir / "normalfont.json").write_text(
                json.dumps(data), encoding="utf-8"
            )
            result = mod.audit_fonts(root, chars)
            self.assertEqual(result["font_count"], 1)
            self.assertEqual(result["fonts"][0]["two_byte_glyph_count"], 1)
            self.assertEqual(result["fonts"][0]["missing_count"], 1)
            self.assertEqual(result["fonts"][0]["covered_count"], 1)
            self.assertEqual(result["fonts"][0]["missing_chars"], "戲")

    def test_glyph_record_without_pixels_does_not_count_as_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            font_dir = root / "fonts"
            font_dir.mkdir()
            data = {
                "_type": "font", "_game": "iw3",
                "glyphs": [{
                    "letter": mod.glyph_code_for_char("遊"),
                    "pixelWidth": 0, "pixelHeight": 12,
                    "s0": 0.1, "s1": 0.1, "t0": 0.1, "t1": 0.2,
                }],
            }
            (font_dir / "normalfont.json").write_text(json.dumps(data), encoding="utf-8")
            font = mod.audit_fonts(root, ["遊"])["fonts"][0]
            self.assertEqual(font["covered_count"], 0)
            self.assertEqual(font["missing_chars"], "遊")
            self.assertEqual(font["unusable_count"], 1)


if __name__ == "__main__":
    unittest.main()

