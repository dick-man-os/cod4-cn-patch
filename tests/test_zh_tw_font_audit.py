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
            glyphs = [{"letter": mod.glyph_code_for_char("遊")}]
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
            self.assertEqual(result["fonts"][0]["missing_count"], 1)
            self.assertEqual(result["fonts"][0]["missing_chars"], "戲")


if __name__ == "__main__":
    unittest.main()
