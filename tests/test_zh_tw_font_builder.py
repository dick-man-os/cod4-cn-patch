"""Independent checks of the generated IW3 font assets.

The integration checks run in CI with the four ZH_TW_* paths set. They read
the built JSON/IWI files and the original game resources; they do not call the
builder's rasterizer or atlas decoder.
"""

import hashlib
import json
import math
import os
import subprocess
import struct
import sys
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path


BUILT_FONTS = (
    "bigfont", "boldfont", "extrabigfont", "normalfont", "objectivefont", "smallfont",
    "consolefont",
)
EXPECTED_FONT_SHA256 = "dce08bd4fd91aa8aa76ed8fea4b694c2dfb8550f67871e326843212ddbeb88b4"
EXPECTED_IWD_SHA256 = "c59fba82283ca16cdcb478328675fce850a71112bcb05eef7a270d533878708e"


def glyph_code(letter):
    if isinstance(letter, str):
        if len(letter) != 1:
            raise ValueError(f"invalid one-character glyph letter: {letter!r}")
        return ord(letter)
    if isinstance(letter, int):
        return letter
    raise ValueError(f"invalid glyph letter type: {type(letter)}")


def read_iwi_rgba(path):
    data = path.read_bytes()
    if len(data) < 28 or data[:4] != b"IWi\x06":
        raise AssertionError(f"not IW3 IWI v6: {path}")
    fmt, flags, width, height, depth, mip0, mip1, mip2, mip3 = struct.unpack_from(
        "<BBHHH4I", data, 4
    )
    if (fmt, flags, depth) != (1, 2, 1):
        raise AssertionError(f"not a 2D RGBA atlas with no mips: {path}")
    if (mip0, mip1, mip2, mip3) != (len(data), 0, 0, 0):
        raise AssertionError(f"IWI mip offsets disagree with length: {path}")
    if len(data) != 28 + width * height * 4:
        raise AssertionError(f"IWI pixel payload length is wrong: {path}")
    rgba = memoryview(data)[28:]
    if any(channel != 255 for channel in rgba[0::4]):
        raise AssertionError(f"red channel is not white: {path}")
    if any(channel != 255 for channel in rgba[1::4]):
        raise AssertionError(f"green channel is not white: {path}")
    if any(channel != 255 for channel in rgba[2::4]):
        raise AssertionError(f"blue channel is not white: {path}")
    return width, height, rgba


def decode_dxt5_alpha(data):
    """Read the original IWI's top-mip BC3 alpha for preservation checks."""
    if len(data) < 28 or data[:4] != b"IWi\x06" or data[4] != 13:
        raise AssertionError("original font image is not IWI v6 DXT5")
    width, height = struct.unpack_from("<HH", data, 6)
    if width % 4 or height % 4:
        raise AssertionError("DXT5 dimensions are not multiples of four")
    block_count = width * height // 16
    blocks = memoryview(data)[-block_count * 16:]
    out = bytearray(width * height)
    for by in range(height // 4):
        for bx in range(width // 4):
            block = blocks[16 * (by * (width // 4) + bx):][:16]
            a0, a1 = block[0], block[1]
            if a0 > a1:
                palette = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
            else:
                palette = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
            indices = int.from_bytes(block[2:8], "little")
            for py in range(4):
                row = (by * 4 + py) * width + bx * 4
                for px in range(4):
                    out[row + px] = palette[(indices >> (3 * (py * 4 + px))) & 7]
    return width, height, bytes(out)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def is_drawable(glyph, width, height, rgba):
    if glyph["pixelWidth"] <= 0 or glyph["pixelHeight"] <= 0:
        return False
    s0, t0, s1, t1 = (glyph[key] for key in ("s0", "t0", "s1", "t1"))
    if not (0 <= s0 < s1 <= 1 and 0 <= t0 < t1 <= 1):
        return False
    left = max(0, int(math.floor(s0 * width)))
    top = max(0, int(math.floor(t0 * height)))
    right = min(width, int(math.ceil(s1 * width)))
    bottom = min(height, int(math.ceil(t1 * height)))
    for y in range(top, bottom):
        for x in range(left, right):
            if rgba[4 * (y * width + x) + 3]:
                return True
    return False


class MappingTests(unittest.TestCase):
    def test_known_unicode_to_gbk_codes(self):
        # 0xD3CE encodes simplified 游, not traditional 遊.
        self.assertEqual(int.from_bytes("遊".encode("gbk"), "big"), 0xDF5B)
        self.assertEqual(int.from_bytes("戲".encode("gbk"), "big"), 0x91F2)
        self.assertEqual(int.from_bytes("「".encode("gbk"), "big"), 0xA1B8)
        self.assertEqual("游".encode("gbk"), bytes.fromhex("d3ce"))

    def test_unpinned_font_bytes_are_rejected_before_cmap_use(self):
        script = Path(__file__).resolve().parents[1] / "tools" / "zh_tw_font_builder.py"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "font.otf").write_bytes(b"OTTO" + bytes(64))
            (root / "required.txt").write_text("遊", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), "check", "--font", str(root / "font.otf"),
                 "--required", str(root / "required.txt")],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("font SHA-256 mismatch", result.stderr)


@unittest.skipUnless(
    all(os.environ.get(key) for key in (
        "ZH_TW_FONT_BUILD_DIR", "ZH_TW_FONT_BASELINE_DIR", "ZH_TW_FONT_REQUIRED", "ZH_TW_BASELINE_IWD"
    )),
    "font build paths are supplied by CI",
)
class BuiltFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected_required = int(os.environ.get("ZH_TW_EXPECTED_REQUIRED", "1288"))
        cls.expected_added = int(os.environ.get("ZH_TW_EXPECTED_ADDED", "498"))
        cls.build = Path(os.environ["ZH_TW_FONT_BUILD_DIR"])
        cls.baseline = Path(os.environ["ZH_TW_FONT_BASELINE_DIR"])
        cls.original_iwd = Path(os.environ["ZH_TW_BASELINE_IWD"])
        cls.required = set(Path(os.environ["ZH_TW_FONT_REQUIRED"]).read_text(encoding="utf-8-sig").strip())
        cls.required_codes = {int.from_bytes(ch.encode("gbk"), "big") for ch in cls.required}
        cls.fonts = {name: read_json(cls.build / "fonts" / f"{name}.json") for name in BUILT_FONTS}
        cls.original_fonts = {
            name: read_json(cls.baseline / "fonts" / f"{name}.json") for name in BUILT_FONTS
        }
        cls.material_images = {}
        for font in cls.fonts.values():
            material = font["material"]
            cls.material_images[material] = cls.build / "images" / (material.split("/")[-1] + ".iwi")

    def test_cmap_manifest_and_licenses(self):
        self.assertEqual(len(self.required), self.expected_required)
        manifest = read_json(self.build / "font-build-manifest.json")
        self.assertEqual(manifest["font_sha256"], EXPECTED_FONT_SHA256)
        self.assertEqual(manifest["baseline_iwd_sha256"], EXPECTED_IWD_SHA256)
        self.assertEqual(manifest["required_count"], len(self.required))
        self.assertEqual(manifest["added_glyph_count_per_font"], self.expected_added)
        self.assertEqual(manifest["original_glyph_count_per_font"], 1742)
        self.assertEqual(manifest["output_glyph_count_per_font"], 1742 + self.expected_added)
        by_char = {item["char"]: item for item in manifest["glyph_mapping"]}
        for char in self.required:
            item = by_char[char]
            self.assertEqual(item["unicode"], f"U+{ord(char):04X}")
            self.assertEqual(item["gbk_code"], f"0x{int.from_bytes(char.encode('gbk'), 'big'):04X}")
            self.assertGreater(item["glyph_id"], 0)
        self.assertEqual(by_char["遊"]["gbk_code"], "0xDF5B")
        for name in ("Noto-CJK-OFL.txt", "Noto-CJK-NOTICE.txt"):
            output = self.build / "licenses" / name
            source = Path(__file__).resolve().parents[1] / "licenses" / name
            self.assertEqual(hashlib.sha256(output.read_bytes()).digest(), hashlib.sha256(source.read_bytes()).digest())

    def test_all_seven_fonts_cover_required_with_visible_pixels(self):
        self.assertEqual(len(self.material_images), 3)
        for name, font in self.fonts.items():
            with self.subTest(font=name):
                self.assertEqual((font["_type"], font["_game"]), ("font", "iw3"))
                glyphs = {glyph_code(g["letter"]): g for g in font["glyphs"]}
                self.assertEqual(len(glyphs), len(font["glyphs"]))
                self.assertTrue(set(range(0x20, 0x80)).issubset(glyphs))
                self.assertTrue(self.required_codes.issubset(glyphs))
                width, height, rgba = read_iwi_rgba(self.material_images[font["material"]])
                absent = [code for code in self.required_codes if not is_drawable(glyphs[code], width, height, rgba)]
                self.assertEqual(absent, [], f"undrawable GBK codes in {name}: {absent[:20]}")

    def test_added_glyphs_use_each_fonts_original_cjk_metrics(self):
        metrics = ("x0", "y0", "dx", "pixelWidth", "pixelHeight")
        mode_by_font = {}
        for name in BUILT_FONTS:
            original = self.original_fonts[name]
            old_codes = {glyph_code(g["letter"]) for g in original["glyphs"]}
            added = self.required_codes - old_codes
            self.assertEqual(len(added), self.expected_added, name)
            typical, _ = Counter(
                tuple(g[field] for field in metrics)
                for g in original["glyphs"] if glyph_code(g["letter"]) > 0xFF
            ).most_common(1)[0]
            mode_by_font[name] = typical
            new_glyphs = {glyph_code(g["letter"]): g for g in self.fonts[name]["glyphs"]}
            for code in added:
                self.assertEqual(tuple(new_glyphs[code][field] for field in metrics), typical,
                                 (name, f"0x{code:04X}"))
        self.assertNotEqual(mode_by_font["bigfont"], mode_by_font["boldfont"])
        self.assertNotEqual(mode_by_font["bigfont"], mode_by_font["objectivefont"])
        self.assertNotEqual(mode_by_font["normalfont"], mode_by_font["smallfont"])

    def test_original_glyph_metrics_and_atlas_pixels_are_preserved(self):
        layout = read_json(self.build / "font-build-manifest.json")["texture"]["atlas_layout"]
        with zipfile.ZipFile(self.original_iwd) as archive:
            original_images = {
                material: archive.read("images/" + material.split("/")[-1] + ".iwi")
                for material in self.material_images
            }
        dimensions = {}
        for material, old_iwi in original_images.items():
            with self.subTest(material=material):
                old_width, old_height, old_alpha = decode_dxt5_alpha(old_iwi)
                new_width, new_height, new_rgba = read_iwi_rgba(self.material_images[material])
                self.assertEqual(new_width, old_width)
                self.assertGreaterEqual(new_height, old_height)
                append_y = layout[material.split("/")[-1]]["append_start"][1]
                self.assertGreater(append_y, 0)
                old_bottom = max(
                    math.ceil(g["t1"] * old_height)
                    for font in self.original_fonts.values()
                    if font["material"] == material
                    for g in font["glyphs"]
                )
                self.assertLessEqual(old_bottom, append_y,
                                     f"new glyphs overlap original glyph rows in {material}")
                # The small atlas has unused space within its original height.
                # New glyphs may occupy that space, while the original occupied
                # rows and every original glyph rectangle stay untouched.
                for y in range(min(old_height, append_y)):
                    old_row = old_alpha[y * old_width:(y + 1) * old_width]
                    new_row = new_rgba[4 * y * new_width + 3:4 * (y + 1) * new_width:4]
                    self.assertEqual(old_row, new_row.tobytes(), f"atlas row {y} changed")
                dimensions[material] = (old_width, old_height, new_width, new_height)
        for name in BUILT_FONTS:
            new = self.fonts[name]
            old = self.original_fonts[name]
            with self.subTest(font=name):
                for field in ("pixelHeight", "material", "glowMaterial"):
                    self.assertEqual(new[field], old[field])
                old_glyphs = {glyph_code(g["letter"]): g for g in old["glyphs"]}
                new_glyphs = {glyph_code(g["letter"]): g for g in new["glyphs"]}
                self.assertTrue(old_glyphs.keys() <= new_glyphs.keys())
                old_width, old_height, new_width, new_height = dimensions[new["material"]]
                for code, source in old_glyphs.items():
                    result = new_glyphs[code]
                    for field in ("x0", "y0", "dx", "pixelWidth", "pixelHeight"):
                        self.assertEqual(result[field], source[field], (name, code, field))
                    for field, scale in (
                        ("s0", old_width / new_width), ("s1", old_width / new_width),
                        ("t0", old_height / new_height), ("t1", old_height / new_height),
                    ):
                        self.assertAlmostEqual(result[field], source[field] * scale, places=9,
                                               msg=f"{name} code={code} {field}")


if __name__ == "__main__":
    unittest.main()
