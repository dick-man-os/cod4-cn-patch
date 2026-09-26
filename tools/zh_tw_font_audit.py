#!/usr/bin/env python3
"""
Audit COD4/IW3 dumped Font_s glyph tables against required zh-TW characters.

IW3's Asian text path combines two encoded bytes as:
    (first_byte << 8) + second_byte
and Font_s glyph.letter is uint16_t.  Therefore a GBK-encoded CJK character
maps directly to the big-endian 16-bit value of its two bytes.

Input font JSON is expected from OpenAssetTools Unlinker.
"""

from __future__ import annotations

import argparse
import json
import struct
import zipfile
from pathlib import Path

CORE_FONT_NAMES = {
    "fonts/bigfont",
    "fonts/smallfont",
    "fonts/boldfont",
    "fonts/normalfont",
    "fonts/extrabigfont",
    "fonts/objectivefont",
}


def glyph_code_for_char(ch: str) -> int:
    raw = ch.encode("gbk", errors="strict")
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2:
        return (raw[0] << 8) | raw[1]
    raise ValueError(f"unexpected GBK width for {ch!r}: {len(raw)}")


def load_required_chars(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").strip()
    chars = sorted(set(text))
    for ch in chars:
        glyph_code_for_char(ch)
    return chars


def find_font_objects(root: Path) -> list[tuple[Path, dict]]:
    found: list[tuple[Path, dict]] = []
    for path in root.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("_type") != "font" or data.get("_game") != "iw3":
            continue

        candidate = data.get("font", data)
        if isinstance(candidate, dict) and isinstance(candidate.get("glyphs"), list):
            found.append((path, candidate))
            continue

        # Fail-soft for schema variants: find one nested dict with glyphs.
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, dict):
                if isinstance(obj.get("glyphs"), list):
                    found.append((path, obj))
                    break
                stack.extend(obj.values())
            elif isinstance(obj, list):
                stack.extend(obj)
    return found


def font_name_from_path(path: Path) -> str:
    p = path.as_posix()
    marker = "/fonts/"
    idx = p.lower().rfind(marker)
    if idx >= 0:
        name = p[idx + 1 :]
        if name.endswith(".json"):
            name = name[:-5]
        return name.lower()
    return path.stem.lower()


def audit_fonts(font_root: Path, required_chars: list[str]) -> dict:
    required = {glyph_code_for_char(ch): ch for ch in required_chars}
    fonts = []

    for path, obj in find_font_objects(font_root):
        numeric_glyphs = [
            g for g in obj.get("glyphs", [])
            if isinstance(g, dict) and isinstance(g.get("letter"), int)
        ]
        letters = {int(g["letter"]) for g in numeric_glyphs}
        drawable = {
            int(g["letter"]) for g in numeric_glyphs
            if g.get("pixelWidth", 0) > 0
            and g.get("pixelHeight", 0) > 0
            and g.get("s1", 0) > g.get("s0", 0)
            and g.get("t1", 0) > g.get("t0", 0)
        }
        missing_codes = sorted(set(required) - drawable)
        unusable_codes = sorted(set(required).intersection(letters - drawable))
        missing_chars = "".join(required[x] for x in missing_codes)
        name = font_name_from_path(path)
        fonts.append({
            "name": name,
            "path": path.as_posix(),
            "pixel_height": obj.get("pixelHeight"),
            "glyph_count_declared": len(obj.get("glyphs", [])),
            "glyph_count_unique": len(letters),
            "required_count": len(required),
            "covered_count": len(required) - len(missing_codes),
            "missing_count": len(missing_codes),
            "missing_chars": missing_chars,
            "missing_codes": [f"0x{x:04X}" for x in missing_codes],
            "unusable_count": len(unusable_codes),
            "core_font": name in CORE_FONT_NAMES,
        })

    fonts.sort(key=lambda x: (not x["core_font"], x["name"]))
    core = [f for f in fonts if f["core_font"]]
    return {
        "required_chars": len(required_chars),
        "required_glyph_codes": len(required),
        "font_count": len(fonts),
        "core_font_count": len(core),
        "core_all_complete": bool(core) and all(f["missing_count"] == 0 for f in core),
        "fonts": fonts,
    }


def parse_iwi_header(data: bytes) -> dict:
    if len(data) < 10 or data[:3] != b"IWi":
        raise ValueError("not a recognized IWI image")
    version = data[3]
    fmt = data[4]
    flags = data[5]
    width, height = struct.unpack_from("<HH", data, 6)
    return {
        "version": version,
        "format": fmt,
        "flags": flags,
        "width": width,
        "height": height,
        "size": len(data),
    }


def inspect_iwd(path: Path) -> dict:
    result = {"path": str(path), "members": [], "font_images": []}
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            result["members"].append({
                "name": info.filename,
                "size": info.file_size,
                "compressed": info.compress_size,
            })
            lower = info.filename.lower()
            if "gamefont" in lower and lower.endswith(".iwi"):
                data = zf.read(info)
                meta = parse_iwi_header(data)
                meta["name"] = info.filename
                result["font_images"].append(meta)
    return result


def write_markdown(result: dict, iwd: dict | None, path: Path) -> None:
    lines = [
        "# COD4 zh-TW font coverage audit",
        "",
        f"- required zh-TW glyph codes: {result['required_glyph_codes']}",
        f"- dumped IW3 fonts: {result['font_count']}",
        f"- core gameplay/UI fonts found: {result['core_font_count']}",
        f"- all core fonts complete: {result['core_all_complete']}",
        "",
        "## Font coverage",
        "",
        "| Font | Glyphs | Covered | Missing | Unusable | Core |",
        "|---|---:|---:|---:|---:|:---:|",
    ]
    for font in result["fonts"]:
        lines.append(
            f"| {font['name']} | {font['glyph_count_unique']} | "
            f"{font['covered_count']} | {font['missing_count']} | "
            f"{font['unusable_count']} | {'yes' if font['core_font'] else 'no'} |"
        )
        if font["missing_count"]:
            lines.append("")
            lines.append(
                f"Missing from **{font['name']}**: "
                f"`{font['missing_chars'][:300]}`"
            )
            if len(font["missing_chars"]) > 300:
                lines.append(f"(and {len(font['missing_chars']) - 300} more)")
            lines.append("")

    if iwd is not None:
        lines += ["", "## IWD font textures", ""]
        for img in iwd["font_images"]:
            lines.append(
                f"- `{img['name']}`: IWI v{img['version']}, "
                f"{img['width']}×{img['height']}, format=0x{img['format']:02X}, "
                f"{img['size']} bytes"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Audit IW3 font glyph coverage for zh-TW")
    p.add_argument("--fonts", type=Path, required=True, help="OpenAssetTools dump root")
    p.add_argument("--required", type=Path, required=True)
    p.add_argument("--iwd", type=Path)
    p.add_argument("--json-out", type=Path, required=True)
    p.add_argument("--md-out", type=Path, required=True)
    args = p.parse_args(argv)

    required = load_required_chars(args.required)
    result = audit_fonts(args.fonts, required)
    iwd_result = inspect_iwd(args.iwd) if args.iwd else None
    if iwd_result is not None:
        result["iwd"] = iwd_result

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_markdown(result, iwd_result, args.md_out)

    print(f"required glyphs: {result['required_glyph_codes']}")
    print(f"fonts found: {result['font_count']}")
    print(f"core fonts found: {result['core_font_count']}")
    print(f"core fonts complete: {result['core_all_complete']}")
    for font in result["fonts"]:
        print(
            f"{font['name']}: glyphs={font['glyph_count_unique']} "
            f"covered={font['covered_count']} missing={font['missing_count']} "
            f"unusable={font['unusable_count']} core={font['core_font']}"
        )

    if result["core_font_count"] == 0:
        print("ERROR: no expected core IW3 font assets were dumped")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

