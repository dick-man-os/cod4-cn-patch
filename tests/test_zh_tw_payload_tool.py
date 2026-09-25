import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "zh_tw_payload_tool.py"
spec = importlib.util.spec_from_file_location("zh_tw_payload_tool", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FakeOpenCC:
    MAPPING = {
        "游戏设置": "遊戲設定",
        "战争": "戰爭",
        "读取游戏": "載入遊戲",
    }
    def convert(self, text):
        return self.MAPPING.get(text, text)


class PayloadToolTests(unittest.TestCase):
    def test_scan_finds_gbk_cjk_only(self):
        data = (
            b"ASCII_KEY\x00"
            + "游戏设置".encode("gbk")
            + b"\x00\xff\xff"
            + "战争".encode("gbk")
            + b"\x00"
        )
        spans = mod.scan_gbk_spans(data)
        self.assertEqual([s.text for s in spans], ["游戏设置", "战争"])

    def test_s2twp_candidate_keeps_length_for_common_terms(self):
        glossary = mod.load_glossary(None)
        original = "游戏设置"
        converted = mod.to_zh_tw(original, glossary, FakeOpenCC())
        self.assertEqual(converted, "遊戲設定")
        self.assertEqual(len(original.encode("gbk")), len(converted.encode("gbk")))

    def test_manifest_marks_length_mismatch(self):
        raw = "游戏".encode("gbk")
        span = mod.Span(10, 14, "游戏", raw)
        entry = mod.make_entry("x.bin", span, "載入遊戲")
        self.assertEqual(entry["status"], "length_mismatch")
        self.assertFalse(entry["exact_length"])

    def test_build_fails_closed_on_source_drift(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            source = td / "src"
            out = td / "out"
            source.mkdir()
            p = source / "x.bin"
            original = "游戏".encode("gbk")
            p.write_bytes(b"\x00" + original + b"\x00")

            manifest = {
                "schema": 1,
                "entries": [{
                    "id": "x.bin:1:5",
                    "file": "x.bin",
                    "start": 1,
                    "end": 5,
                    "original": "游戏",
                    "original_len": 4,
                    "original_hex": original.hex(),
                    "zh_tw": "遊戲",
                    "approved": True,
                    "gbk_encodable": True,
                    "candidate_len": 4,
                    "candidate_hex": "遊戲".encode("gbk").hex(),
                    "exact_length": True,
                    "status": "candidate_safe",
                }],
            }
            mf = td / "manifest.json"
            mf.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            from types import SimpleNamespace
            args = SimpleNamespace(manifest=mf, source=source, output=out, clean=False)

            self.assertEqual(mod.command_build(args), 0)
            self.assertEqual((out / "x.bin").read_bytes(), b"\x00" + "遊戲".encode("gbk") + b"\x00")

            p.write_bytes(b"\x00" + "設定".encode("gbk") + b"\x00")
            with self.assertRaises(RuntimeError):
                mod.command_build(args)


if __name__ == "__main__":
    unittest.main()
