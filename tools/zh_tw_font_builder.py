#!/usr/bin/env python3
"""Append missing GBK glyphs to the IW3 font atlases, deterministically.

Font cmap lookup uses Unicode. The game-facing glyph letter uses packed GBK.
This does not call OpenAssetTools' TTF font compiler.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import hashlib
import json
import shutil
import struct
from pathlib import Path
import zipfile

FONT_SHA256 = "dce08bd4fd91aa8aa76ed8fea4b694c2dfb8550f67871e326843212ddbeb88b4"
BASELINE_IWD_SHA256 = "c59fba82283ca16cdcb478328675fce850a71112bcb05eef7a270d533878708e"
CORE_FONTS = ("bigfont", "boldfont", "extrabigfont", "normalfont", "objectivefont", "smallfont", "consolefont")
IWI_HEADER_SIZE = 28
ATLAS = {
    "gamefonts_pc_normal": {"old_size": (1024, 512), "new_size": (1024, 1024),
                            "start": (4, 516), "pitch": (16, 20), "columns": 62,
                            "bitmap": (13, 15)},
    "gamefonts_pc_extrabig": {"old_size": (1024, 1024), "new_size": (1024, 2048),
                              "start": (4, 1028), "pitch": (24, 28), "columns": 42,
                              "bitmap": (19, 23)},
    "gamefonts_pc_small": {"old_size": (1024, 1024), "new_size": (1024, 1024),
                           "start": (4, 672), "pitch": (20, 24), "columns": 51,
                           "bitmap": (14, 18)},
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pinned_font(path: Path, expected_sha256: str = FONT_SHA256) -> bytes:
    data = path.read_bytes()
    actual = sha256(data)
    if actual != expected_sha256:
        raise ValueError(f"font SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    return data


def unicode_cmap_groups(font_data: bytes) -> list[tuple[int, int, int]]:
    """Read a Unicode format-12 cmap from an SFNT/OTF font without guessing glyphs."""
    if len(font_data) < 12 or font_data[:4] not in (b"OTTO", b"\x00\x01\x00\x00"):
        raise ValueError("font is not a supported OTF/TTF SFNT")
    table_count = struct.unpack_from(">H", font_data, 4)[0]
    cmap = None
    for i in range(table_count):
        tag, _, offset, length = struct.unpack_from(">4sIII", font_data, 12 + 16 * i)
        if offset + length > len(font_data):
            raise ValueError("SFNT table extends beyond file")
        if tag == b"cmap":
            cmap = (offset, length)
    if cmap is None:
        raise ValueError("font has no cmap table")
    base, length = cmap
    records = []
    for i in range(struct.unpack_from(">H", font_data, base + 2)[0]):
        platform, encoding, relative = struct.unpack_from(">HHI", font_data, base + 4 + 8 * i)
        at = base + relative
        if not (base <= at <= base + length - 16):
            raise ValueError("cmap subtable offset is invalid")
        fmt = struct.unpack_from(">H", font_data, at)[0]
        if fmt == 12 and (platform == 0 or (platform, encoding) == (3, 10)):
            records.append((platform != 3, at))
    if not records:
        raise ValueError("font has no Unicode format-12 cmap")
    records.sort()
    at = records[0][1]
    subtable_length, count = struct.unpack_from(">II", font_data, at + 4)[0], struct.unpack_from(">I", font_data, at + 12)[0]
    if at + subtable_length > base + length or 16 + 12 * count > subtable_length:
        raise ValueError("cmap format-12 length is invalid")
    groups = [struct.unpack_from(">III", font_data, at + 16 + 12 * i) for i in range(count)]
    if any(a > b or (i and a <= groups[i - 1][1]) for i, (a, b, _) in enumerate(groups)):
        raise ValueError("cmap groups overlap or are unsorted")
    return groups


def cmap_glyph_id(groups: list[tuple[int, int, int]], codepoint: int) -> int:
    starts = [g[0] for g in groups]
    i = bisect_right(starts, codepoint) - 1
    if i < 0:
        return 0
    start, end, first_gid = groups[i]
    return first_gid + codepoint - start if codepoint <= end else 0


def required_mapping(path: Path, groups: list[tuple[int, int, int]]) -> list[dict]:
    chars = sorted(set(path.read_text(encoding="utf-8-sig").strip()))
    mapping = []
    for ch in chars:
        raw = ch.encode("gbk", errors="strict")
        if len(raw) != 2:
            raise ValueError(f"required glyph is not two-byte GBK: {ch!r}")
        if raw.decode("gbk", errors="strict") != ch:
            raise ValueError(f"GBK round-trip changed required glyph: {ch!r}")
        mapping.append({
            "char": ch,
            "unicode": f"U+{ord(ch):04X}",
            "gbk_code": f"0x{int.from_bytes(raw, 'big'):04X}",
            "glyph_id": cmap_glyph_id(groups, ord(ch)),
        })
    missing = [item["char"] for item in mapping if item["glyph_id"] == 0]
    if missing:
        raise ValueError(f"font cmap is missing {len(missing)} required glyphs: {''.join(missing)}")
    if len({item["gbk_code"] for item in mapping}) != len(mapping):
        raise ValueError("two required characters map to one GBK code")
    return sorted(mapping, key=lambda item: int(item["gbk_code"], 16))


def load_baseline_fonts(root: Path) -> dict[str, dict]:
    fonts = {}
    for name in CORE_FONTS:
        path = root / "fonts" / f"{name}.json"
        obj = json.loads(path.read_text(encoding="utf-8"))
        if obj.get("_type") != "font" or obj.get("_game") != "iw3":
            raise ValueError(f"unexpected baseline font schema: {path}")
        for key in ("pixelHeight", "material", "glowMaterial"):
            if key not in obj:
                raise ValueError(f"baseline font lacks {key}: {path}")
        fonts[name] = obj
    return fonts


def read_baseline_iwi(iwd: Path, image_name: str, size: tuple[int, int]) -> bytes:
    """Read and validate a pinned original IW3 BC3 image from its IWD."""
    with zipfile.ZipFile(iwd) as archive:
        data = archive.read(f"images/{image_name}.iwi")
    if len(data) < IWI_HEADER_SIZE or data[:4] != b"IWi\x06":
        raise ValueError(f"unexpected IWI header: {image_name}")
    fmt, flags, width, height, depth, m0, m1, m2, m3 = struct.unpack_from("<BBHHH4I", data, 4)
    if (fmt, flags, width, height, depth) != (13, 0, *size, 1):
        raise ValueError(f"unexpected IWI properties: {image_name}")
    if m0 != len(data) or m1 != len(data) - width * height or not (IWI_HEADER_SIZE < m3 < m2 < m1 < m0):
        raise ValueError(f"invalid IWI mip offsets: {image_name}")
    return data


def decode_bc3_alpha(iwi: bytes) -> tuple[bytes, int, int]:
    """Decode only the highest-resolution BC3 alpha; old visible RGB is white."""
    fmt, _, width, height, _, _, top_start, _, _ = struct.unpack_from("<BBHHH4I", iwi, 4)
    if fmt != 13 or width % 4 or height % 4 or top_start + width * height != len(iwi):
        raise ValueError("BC3 top mip layout is invalid")
    out = bytearray(width * height)
    block_count_x = width // 4
    for block_y in range(height // 4):
        for block_x in range(block_count_x):
            pos = top_start + 16 * (block_y * block_count_x + block_x)
            a0, a1 = iwi[pos], iwi[pos + 1]
            if a0 > a1:
                palette = (a0, a1, *((((7 - i) * a0 + i * a1) // 7) for i in range(1, 7)))
            else:
                palette = (a0, a1, *((((5 - i) * a0 + i * a1) // 5) for i in range(1, 5)), 0, 255)
            indices = int.from_bytes(iwi[pos + 2:pos + 8], "little")
            for py in range(4):
                row = (block_y * 4 + py) * width + block_x * 4
                for px in range(4):
                    out[row + px] = palette[(indices >> (3 * (py * 4 + px))) & 7]
    return bytes(out), width, height


def render_em_cell(font, char: str, target: tuple[int, int]):
    """Render a full CJK em cell, keeping punctuation at its natural position."""
    from PIL import Image, ImageDraw

    source = Image.new("L", (64, 68), 0)
    ImageDraw.Draw(source).text((0, 58), char, font=font, fill=255, anchor="ls")
    resized = source.resize(target, Image.Resampling.LANCZOS)
    if not resized.getbbox():
        raise ValueError(f"source font rasterized an empty glyph: {char!r}")
    return resized


def normalize_code(letter: object) -> int:
    if isinstance(letter, str) and len(letter) == 1:
        return ord(letter)
    if isinstance(letter, int) and not isinstance(letter, bool):
        return letter
    raise ValueError(f"invalid Font_s letter value: {letter!r}")


def modal_cjk_metrics(obj: dict) -> tuple[int, int, int, int, int]:
    mode, _ = Counter((g["x0"], g["y0"], g["dx"], g["pixelWidth"], g["pixelHeight"])
                      for g in obj["glyphs"] if normalize_code(g["letter"]) > 0xFF).most_common(1)[0]
    return mode


def pack_missing(atlas, missing: list[dict], config: dict, font_path: Path) -> dict[int, tuple[int, int]]:
    """Place only absent codes in an explicit, stable grid."""
    from PIL import ImageFont

    font = ImageFont.truetype(str(font_path), size=64)
    width, height = config["new_size"]
    pitch_x, pitch_y = config["pitch"]
    bitmap_w, bitmap_h = config["bitmap"]
    start_x, start_y = config["start"]
    columns = config["columns"]
    positions = {}
    for i, item in enumerate(missing):
        x = start_x + (i % columns) * pitch_x
        y = start_y + (i // columns) * pitch_y
        if x + bitmap_w + 4 > width or y + bitmap_h + 4 > height:
            raise ValueError(f"fixed atlas grid overflows at {item['char']!r}")
        atlas.paste(render_em_cell(font, item["char"], (bitmap_w, bitmap_h)), (x, y))
        positions[int(item["gbk_code"], 16)] = (x, y)
    return positions


def write_iwi_rgba(alpha: bytes, width: int, height: int) -> bytes:
    if len(alpha) != width * height or max(width, height) > 65535:
        raise ValueError("invalid atlas dimensions")
    # IW3 IWI v6, IMG_FORMAT_BITMAP_RGBA, NOMIPMAPS, 2D depth 1.
    # The original DXT5 font images have white RGB under their alpha. A8 alone
    # can sample as black RGB with the original font materials.
    rgba = bytearray(4 * len(alpha))
    for i, a in enumerate(alpha):
        rgba[4 * i:4 * i + 4] = bytes((255, 255, 255, a))
    header = b"IWi\x06" + struct.pack("<BBHHH4I", 1, 2, width, height, 1,
                                         IWI_HEADER_SIZE + len(rgba), 0, 0, 0)
    if len(header) != IWI_HEADER_SIZE:
        raise AssertionError("IWI header size drift")
    return header + rgba


def dump_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def build(font_path: Path, font_data: bytes, mapping: list[dict], baseline_root: Path,
          baseline_iwd: Path, license_file: Path, notice_file: Path, output: Path) -> dict:
    from PIL import __version__ as pillow_version
    from PIL import Image, features

    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}")
    iwd_sha = sha256(baseline_iwd.read_bytes())
    if iwd_sha != BASELINE_IWD_SHA256:
        raise ValueError(f"baseline IWD SHA-256 mismatch: expected {BASELINE_IWD_SHA256}, got {iwd_sha}")
    baseline = load_baseline_fonts(baseline_root)
    required_codes = {int(item["gbk_code"], 16) for item in mapping}
    old_codes_by_name = {}
    for name, obj in baseline.items():
        old_codes = [normalize_code(g["letter"]) for g in obj["glyphs"]]
        if len(old_codes) != len(set(old_codes)) or not set(range(0x20, 0x80)).issubset(old_codes):
            raise ValueError(f"baseline font has duplicate or missing ASCII glyphs: {name}")
        old_codes_by_name[name] = set(old_codes)
    if len({frozenset(codes) for codes in old_codes_by_name.values()}) != 1:
        raise ValueError("the baseline font assets do not have an identical code set")
    old_codes = next(iter(old_codes_by_name.values()))
    missing = [item for item in mapping if int(item["gbk_code"], 16) not in old_codes]
    if len(mapping) != 1288 or len(missing) != 498:
        raise ValueError(f"unexpected glyph checkpoint: required={len(mapping)} missing={len(missing)}")
    groups: dict[str, list[str]] = {}
    for name, obj in baseline.items():
        image_name = obj["material"].removeprefix("fonts/")
        if image_name not in ATLAS:
            raise ValueError(f"unexpected material for {name}: {obj['material']}")
        groups.setdefault(image_name, []).append(name)
    output.mkdir(parents=True, exist_ok=True)
    produced = {}
    atlas_layout = {}
    for image_name, names in sorted(groups.items()):
        config = ATLAS[image_name]
        source = read_baseline_iwi(baseline_iwd, image_name, config["old_size"])
        source_alpha, old_w, old_h = decode_bc3_alpha(source)
        width, height = config["new_size"]
        atlas = Image.new("L", (width, height), 0)
        atlas.paste(Image.frombytes("L", (old_w, old_h), source_alpha), (0, 0))
        if image_name == "gamefonts_pc_small" and atlas.crop((0, config["start"][1], old_w, old_h)).getbbox():
            raise ValueError("the small atlas append region is not empty")
        positions = pack_missing(atlas, missing, config, font_path)
        iwi_path = output / "images" / f"{image_name}.iwi"
        iwi_path.parent.mkdir(parents=True, exist_ok=True)
        iwi = write_iwi_rgba(atlas.tobytes(), width, height)
        iwi_path.write_bytes(iwi)
        produced[iwi_path.relative_to(output).as_posix()] = sha256(iwi)
        atlas_layout[image_name] = {"source_size": [old_w, old_h], "output_size": [width, height],
                                    "append_start": list(config["start"]), "pitch": list(config["pitch"]),
                                    "columns": config["columns"]}
        for name in names:
            original = baseline[name]
            x0, y0, dx, bitmap_w, bitmap_h = modal_cjk_metrics(original)
            if bitmap_w > config["bitmap"][0] or bitmap_h > config["bitmap"][1]:
                raise ValueError(f"baseline glyph metrics exceed atlas cell: {name}")
            glyphs = []
            for old in original["glyphs"]:
                glyph = old.copy()
                if old_h != height:
                    glyph["t0"] *= old_h / height
                    glyph["t1"] *= old_h / height
                glyphs.append(glyph)
            for item in missing:
                code = int(item["gbk_code"], 16)
                x, y = positions[code]
                if not atlas.crop((x, y, x + bitmap_w, y + bitmap_h)).getbbox():
                    raise ValueError(f"new glyph is blank at intended metrics: {name} {item['char']!r}")
                glyphs.append({
                    "letter": code, "x0": x0, "y0": y0, "dx": dx,
                    "pixelWidth": bitmap_w, "pixelHeight": bitmap_h,
                    "s0": (x + 0.5) / width, "t0": (y + 0.5) / height,
                    "s1": (x + bitmap_w + 0.5) / width,
                    "t1": (y + bitmap_h + 0.5) / height,
                })
            glyphs.sort(key=lambda glyph: normalize_code(glyph["letter"]))
            result = {
                "$schema": "http://openassettools.dev/schema/font.v1.json",
                "_type": "font", "_version": 1, "_game": "iw3",
                "pixelHeight": original["pixelHeight"],
                "material": original["material"],
                "glowMaterial": original["glowMaterial"],
                "glyphs": glyphs,
            }
            path = output / "fonts" / f"{name}.json"
            dump_json(path, result)
            produced[path.relative_to(output).as_posix()] = sha256(path.read_bytes())
    license_path = output / "licenses" / "Noto-CJK-OFL.txt"
    license_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(license_file, license_path)
    produced[license_path.relative_to(output).as_posix()] = sha256(license_path.read_bytes())
    notice_path = output / "licenses" / "Noto-CJK-NOTICE.txt"
    shutil.copyfile(notice_file, notice_path)
    produced[notice_path.relative_to(output).as_posix()] = sha256(notice_path.read_bytes())
    manifest = {
        "schema": 1,
        "font_name": "Noto Sans CJK TC Regular",
        "font_version": "2.004",
        "font_sha256": sha256(font_data),
        "font_source": "https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf",
        "baseline_iwd_sha256": iwd_sha,
        "pillow_version": pillow_version,
        "freetype_version": features.version_module("freetype2"),
        "texture": {"format": "IWI v6 RGBA white+alpha", "atlas_layout": atlas_layout},
        "required_count": len(mapping),
        "original_glyph_count_per_font": len(old_codes),
        "added_glyph_count_per_font": len(missing),
        "output_glyph_count_per_font": len(old_codes) + len(missing),
        "glyph_mapping": mapping,
        "outputs_sha256": produced,
    }
    dump_json(output / "font-build-manifest.json", manifest)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "build"))
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--required", type=Path, required=True)
    parser.add_argument("--expected-font-sha256", default=FONT_SHA256)
    parser.add_argument("--baseline-fonts", type=Path)
    parser.add_argument("--baseline-iwd", type=Path)
    parser.add_argument("--license-file", type=Path)
    parser.add_argument("--notice-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    data = load_pinned_font(args.font, args.expected_font_sha256)
    mapping = required_mapping(args.required, unicode_cmap_groups(data))
    print(f"font SHA-256: {sha256(data)}")
    print(f"required: {len(mapping)} covered: {len(mapping)} missing: 0")
    if args.command == "build":
        if not all((args.baseline_fonts, args.baseline_iwd, args.license_file, args.notice_file, args.output)):
            parser.error("build requires --baseline-fonts, --baseline-iwd, --license-file, --notice-file and --output")
        result = build(args.font, data, mapping, args.baseline_fonts, args.baseline_iwd,
                       args.license_file, args.notice_file, args.output)
        print(f"produced files: {len(result['outputs_sha256'])}")
        print(f"output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
