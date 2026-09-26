#!/usr/bin/env python3
"""Verify every required GBK glyph has real alpha in its IW3 IWI atlas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from zh_tw_font_builder import CORE_FONTS, normalize_code


def read_rgba_iwi(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if len(data) < 28 or data[:4] != b"IWi\x06":
        raise ValueError(f"unsupported IWI signature: {path}")
    fmt, flags, width, height, depth, m0, m1, m2, m3 = struct.unpack_from("<BBHHH4I", data, 4)
    if (fmt, flags, depth, m0, m1, m2, m3) != (1, 2, 1, len(data), 0, 0, 0):
        raise ValueError(f"unexpected IWI v6 RGBA header: {path}")
    if width <= 0 or height <= 0 or len(data) != 28 + width * height * 4:
        raise ValueError(f"IWI RGBA payload has wrong size: {path}")
    rgba = data[28:]
    if any(rgba[i:i + 3] != b"\xff\xff\xff" for i in range(0, len(rgba), 4) if rgba[i + 3]):
        raise ValueError(f"visible IWI pixels are not white: {path}")
    return width, height, rgba[3::4]


def inspect(fonts: Path, images: Path, required: Path, expected_required: int = 1288) -> dict:
    chars = sorted(set(required.read_text(encoding="utf-8-sig").strip()))
    codes = {int.from_bytes(ch.encode("gbk"), "big"): ch for ch in chars}
    if len(chars) != expected_required or len(codes) != expected_required:
        raise ValueError(f"required character checkpoint changed: {len(chars)}")
    report = {"required": len(codes), "fonts": {}}
    cache = {}
    for name in CORE_FONTS:
        obj = json.loads((fonts / "fonts" / f"{name}.json").read_text(encoding="utf-8"))
        image_name = obj["material"].removeprefix("fonts/")
        if image_name not in cache:
            cache[image_name] = read_rgba_iwi(images / "images" / f"{image_name}.iwi")
        width, height, alpha = cache[image_name]
        glyphs = {normalize_code(g["letter"]): g for g in obj["glyphs"]}
        missing = []
        blank = []
        invalid = []
        for code, ch in codes.items():
            glyph = glyphs.get(code)
            if glyph is None:
                missing.append(ch)
                continue
            gw, gh = glyph["pixelWidth"], glyph["pixelHeight"]
            x = round(glyph["s0"] * width - 0.5)
            y = round(glyph["t0"] * height - 0.5)
            if (gw <= 0 or gh <= 0 or x < 0 or y < 0 or x + gw > width or y + gh > height
                    or abs((glyph["s1"] - glyph["s0"]) * width - gw) > 0.001
                    or abs((glyph["t1"] - glyph["t0"]) * height - gh) > 0.001):
                invalid.append(ch)
                continue
            if not any(alpha[row * width + x + col] for row in range(y, y + gh) for col in range(gw)):
                blank.append(ch)
        report["fonts"][name] = {
            "glyph_count": len(glyphs), "present": len(codes) - len(missing),
            "drawable": len(codes) - len(missing) - len(blank) - len(invalid),
            "missing": "".join(missing), "blank": "".join(blank), "invalid": "".join(invalid),
            "image": image_name, "image_size": [width, height],
        }
    report["pass"] = all(x["drawable"] == len(codes) for x in report["fonts"].values())
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonts", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--required", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--expected-required", type=int, default=1288)
    args = parser.parse_args(argv)
    report = inspect(args.fonts, args.images, args.required, args.expected_required)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, result in report["fonts"].items():
        print(f"{name}: drawable={result['drawable']}/{report['required']} "
              f"missing={len(result['missing'])} blank={len(result['blank'])} invalid={len(result['invalid'])}")
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
