#!/usr/bin/env python3
"""Rebuild IW3 code_post_gfx.ff with the verified zh-TW Font_s assets.

OpenAssetTools v0.33.0 is used only as Unlinker/Linker. The input FF and IWD
are read-only; all extraction, staging, links, and QA output stay in --output.
The QA IWD is a deterministic copy with only three atlas payloads replaced.
It is not an installer or a release package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from zh_tw_font_builder import BASELINE_IWD_SHA256, CORE_FONTS, normalize_code
from zh_tw_font_pixels_audit import inspect as inspect_pixels, read_rgba_iwi


ATLAS_NAMES = ("gamefonts_pc_normal", "gamefonts_pc_small", "gamefonts_pc_extrabig")
MATERIAL_NAMES = tuple(
    f"gamefonts_pc_{size}{suffix}"
    for size in ("normal", "small", "extrabig")
    for suffix in ("", "_glow")
)
REQUIRED_COUNT = 1288
GLYPHS_PER_FONT = 2240
ORIGINAL_FF_SHA256 = "9671d5d7366acb8260a866a21abf558c07b848ca7933132a4015f247d08f6fef"
KNOWN_UNAVAILABLE_IMAGES = frozenset({
    "scrollbar_thumb", "3_cursor3", "button_highlight_end", "line_horizontal",
    "devfonts", "missing_fx", "gradient_fadein", "falloff_linear", "default",
    "statmon_warning_tris", "console", "warning@file", "warning@fps",
    "voice_on_dim_#0", "voice_on_#0", "scrollbar_arrow_left", "shadow",
    "popups_alpha", "scrollbar", "scrollbar_arrow_dwn_a", "scrollbar_arrow_up_a",
    "scrollbar_arrow_right", "slider2", "sliderbutt_1", "compass_fov",
})


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def oat_executable(folder: Path, name: str) -> Path:
    found = [path for path in folder.rglob("*")
             if path.is_file() and path.name in (name, f"{name}.exe")]
    ensure(len(found) == 1, f"expected exactly one {name} executable under {folder}; found {len(found)}")
    return found[0].resolve()


def run_oat(binary: Path, args: list[str], log_path: Path) -> str:
    command = [str(binary), "--no-color", *args]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", check=False)
    log = result.stdout
    log_path.write_text("Command: " + " ".join(command) + "\n" + log, encoding="utf-8")
    ensure(result.returncode == 0, f"{binary.name} exited {result.returncode}; see {log_path}")
    ensure(re.search(r"Finished with 0 warnings, 0 errors\s*$", log),
           f"{binary.name} reported warnings/errors; see {log_path}")
    return log


def verify_inputs(args: argparse.Namespace) -> dict:
    for label in ("original_ff", "baseline_iwd", "required"):
        ensure(getattr(args, label).is_file(), f"missing {label}: {getattr(args, label)}")
    ensure(args.font_build.is_dir(), f"missing font build: {args.font_build}")
    ensure(digest(args.original_ff) == ORIGINAL_FF_SHA256,
           "original FF does not match pinned SHA-256")
    manifest = load_json(args.font_build / "font-build-manifest.json")
    ensure(manifest.get("schema") == 1 and manifest.get("required_count") == args.expected_required,
           "font-build manifest schema or required count differs from checkpoint")
    ensure(manifest.get("output_glyph_count_per_font") == args.expected_glyphs_per_font,
           "font-build manifest glyph count differs from checkpoint")
    ensure(manifest.get("baseline_iwd_sha256") == BASELINE_IWD_SHA256,
           "font-build baseline IWD SHA does not match pinned baseline")
    ensure(digest(args.baseline_iwd) == BASELINE_IWD_SHA256,
           "baseline IWD does not match pinned SHA-256")
    expected_files = {f"fonts/{name}.json" for name in CORE_FONTS}
    expected_files.update(f"images/{name}.iwi" for name in ATLAS_NAMES)
    output_hashes = manifest.get("outputs_sha256", {})
    ensure(expected_files.issubset(output_hashes), "font-build manifest omits font or atlas hashes")
    for relative in sorted(expected_files):
        source = args.font_build / relative
        ensure(source.is_file(), f"font-build output missing: {source}")
        ensure(digest(source) == output_hashes[relative], f"font-build hash mismatch: {relative}")
    characters = sorted(set(args.required.read_text(encoding="utf-8-sig").strip()))
    ensure(len(characters) == args.expected_required, "required glyph count differs from checkpoint")
    codes = [int.from_bytes(char.encode("gbk"), "big") for char in characters]
    ensure(all(code > 0xFF for code in codes) and len(set(codes)) == args.expected_required,
           "required Unicode to GBK mapping is not one-to-one double-byte")
    manifest_mapping = {int(item["gbk_code"], 16): item["char"]
                        for item in manifest.get("glyph_mapping", [])}
    ensure(manifest_mapping == dict(zip(codes, characters)),
           "required glyph mapping differs from font-build manifest")
    pixels = inspect_pixels(args.font_build, args.font_build, args.required, args.expected_required)
    ensure(pixels["pass"] and all(item["drawable"] == args.expected_required
                                  for item in pixels["fonts"].values()),
           "font-build pixels are not fully drawable")
    return {"manifest": manifest, "prelink_pixels": pixels}


def stage_assets(source_dump: Path, font_build: Path, stage: Path, localize_str: Path | None = None) -> int:
    zone_source = source_dump / "zone_source" / "code_post_gfx.zone"
    localized = source_dump / "english" / "localizedstrings" / "code_post_gfx.str"
    ensure(zone_source.is_file() and localized.is_file(),
           "Unlinker did not provide full zone source and aggregate localize file")
    zone_text = zone_source.read_text(encoding="utf-8-sig")
    ensure(zone_text.startswith("// Call Of Duty 4") and ">game,IW3" in zone_text,
           "unexpected original zone source")
    ensure("localize,code_post_gfx" in zone_text,
           "original zone source does not request aggregate localized strings")
    for name in CORE_FONTS:
        ensure(f"font,fonts/{name}" in zone_text, f"original zone omits font {name}")
    for name in ATLAS_NAMES:
        ensure(f"image,{name}" in zone_text, f"original zone omits image {name}")
    entries = [line for line in zone_text.splitlines()
               if line and not line.lstrip().startswith("//") and not line.startswith(">")]
    ensure(len(entries) > 100, f"original zone source appears truncated: {len(entries)} entries")
    if localize_str is not None:
        from zh_tw_text_build import parse_str, tokens, check_hazards
        original_entries = parse_str(localized.read_bytes())
        converted_entries = parse_str(localize_str.read_bytes())
        ensure([e.key for e in original_entries] == [e.key for e in converted_entries],
               "localize override changes keys or their order")
        for original, converted in zip(original_entries, converted_entries):
            ensure(tokens(original.text) == tokens(converted.text),
                   f"localize override changes control tokens: {original.key}")
            check_hazards(converted.text)
    copies = [(zone_source, stage / "zone_source" / zone_source.name),
              (localize_str or localized, stage / "english" / "localizedstrings" / localized.name)]
    for name in CORE_FONTS:
        copies.append((font_build / "fonts" / f"{name}.json", stage / "fonts" / f"{name}.json"))
    for name in MATERIAL_NAMES:
        src = source_dump / "materials" / "fonts" / f"{name}.json"
        ensure(src.is_file(), f"original font material unavailable: {name}")
        material = load_json(src)
        expected_image = name.removesuffix("_glow")
        ensure(material.get("_game") == "iw3"
               and any(texture.get("image") == expected_image
                       for texture in material.get("textures", [])),
               f"material {name} does not reference expected image {expected_image}")
        copies.append((src, stage / "materials" / "fonts" / src.name))
    for name in ATLAS_NAMES:
        copies.append((font_build / "images" / f"{name}.iwi", stage / "images" / f"{name}.iwi"))
    for source, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return len(entries)


def verify_link_log(log: str) -> None:
    for name in CORE_FONTS:
        ensure(f'Loaded font "fonts/{name}" (src: disk)' in log,
               f"Linker did not use built font {name}")
    for name in MATERIAL_NAMES:
        ensure(f'Loaded material "fonts/{name}" (src: disk)' in log,
               f"Linker did not use staged material {name}")
    for name in ATLAS_NAMES:
        ensure(f'Loaded image "{name}" (src: disk)' in log,
               f"Linker did not use built atlas {name}")
    ensure('Created zone "code_post_gfx"' in log, "Linker did not create requested zone")


def verify_roundtrip(font_build: Path, dumped: Path, expected_glyphs: int = GLYPHS_PER_FONT) -> dict:
    counts = {}
    for name in CORE_FONTS:
        expected = load_json(font_build / "fonts" / f"{name}.json")
        path = dumped / "fonts" / f"{name}.json"
        ensure(path.is_file(), f"Unlinker did not dump rebuilt font: {name}")
        actual = load_json(path)
        for key in ("_type", "_version", "_game", "pixelHeight", "material", "glowMaterial"):
            ensure(expected.get(key) == actual.get(key), f"font {name} changed {key} in roundtrip")
        def indexed(obj: dict) -> dict:
            result = {}
            for glyph in obj["glyphs"]:
                code = normalize_code(glyph["letter"])
                ensure(code not in result, f"duplicate code 0x{code:04X} in {name}")
                result[code] = glyph
            return result
        before, after = indexed(expected), indexed(actual)
        ensure(len(before) == expected_glyphs and before.keys() == after.keys(),
               f"font {name} lost or gained glyph codes in roundtrip")
        for code, glyph in before.items():
            rebuilt = after[code]
            for field in ("x0", "y0", "dx", "pixelWidth", "pixelHeight"):
                ensure(glyph[field] == rebuilt[field],
                       f"font {name} code 0x{code:04X} changed {field}")
            for field in ("s0", "t0", "s1", "t1"):
                ensure(abs(glyph[field] - rebuilt[field]) <= 1e-7,
                       f"font {name} code 0x{code:04X} changed {field}")
        counts[name] = len(after)
    return counts


def verify_image_roundtrip(unlinker: Path, linked_ff: Path, stage: Path,
                           font_build: Path, output: Path) -> dict:
    """Check target atlases despite unavailable raw data for unrelated IW3 images."""
    command = [str(unlinker), "--no-color", "--include-assets", "image",
               "--image-format", "IWI", "--search-path", str(stage),
               "--output-folder", str(output), str(linked_ff)]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", check=False)
    log = result.stdout
    log_path = output.parent / "unlink-images.log"
    log_path.write_text("Command: " + " ".join(command) + "\n" + log, encoding="utf-8")
    ensure(result.returncode == 0 and re.search(r"Finished with \d+ warnings, \d+ errors", log),
           f"image Unlinker failed; see {log_path}")
    errors = re.findall(r"^ERROR: (.+)$", log, flags=re.MULTILINE)
    warnings = re.findall(r"^WARN: (.+)$", log, flags=re.MULTILINE)
    for error in errors:
        match = re.fullmatch(r'Could not find data for image "([^"]+)"', error.strip())
        ensure(match is not None and match.group(1) in KNOWN_UNAVAILABLE_IMAGES,
               f"unexpected image roundtrip error: {error}")
    ensure(all("$pixelcostcolorcode" in warning and "IWI does not support" in warning
               for warning in warnings), f"unexpected image roundtrip warnings: {warnings}")
    summary = re.search(r"Finished with (\d+) warnings, (\d+) errors", log)
    ensure(summary is not None and (int(summary.group(1)), int(summary.group(2)))
           == (len(warnings), len(errors)), "image Unlinker summary and diagnostics disagree")
    atlases = {}
    for name in ATLAS_NAMES:
        ensure(f'Dumped image "{name}"' in log, f"Unlinker did not dump image {name}")
        source = font_build / "images" / f"{name}.iwi"
        dumped = output / "images" / f"{name}.iwi"
        ensure(dumped.is_file(), f"Unlinker omitted target atlas: {name}")
        ensure(digest(source) == digest(dumped), f"Unlinker atlas bytes changed: {name}")
        sw, sh, _ = read_rgba_iwi(source)
        dw, dh, _ = read_rgba_iwi(dumped)
        ensure((sw, sh) == (dw, dh), f"Unlinker atlas dimensions changed: {name}")
        atlases[name] = {"sha256": digest(dumped), "size": [dw, dh]}
    return {"atlases": atlases, "unrelated_missing_images": len(errors),
            "unsupported_image_warnings": len(warnings)}


def repack_iwd(original: Path, font_build: Path, target: Path) -> dict:
    replacements = {f"images/{name}.iwi": font_build / "images" / f"{name}.iwi"
                    for name in ATLAS_NAMES}
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(original, "r") as source, zipfile.ZipFile(target, "w") as output:
        infos = source.infolist()
        names = [info.filename for info in infos]
        ensure(len(infos) == 35 and len(set(names)) == len(names),
               "baseline IWD member list differs from checkpoint")
        ensure(set(replacements).issubset(names), "baseline IWD omits font atlases")
        ensure(all(info.compress_type == zipfile.ZIP_STORED for info in infos),
               "baseline IWD has unexpected compressed members")
        for info in infos:
            data = replacements[info.filename].read_bytes() if info.filename in replacements else source.read(info)
            output.writestr(info, data)
    with zipfile.ZipFile(original, "r") as source, zipfile.ZipFile(target, "r") as rebuilt:
        ensure(source.namelist() == rebuilt.namelist(), "QA IWD member order changed")
        for name in source.namelist():
            desired = replacements[name].read_bytes() if name in replacements else source.read(name)
            ensure(rebuilt.read(name) == desired, f"QA IWD member differs: {name}")
    return {"member_count": len(names), "replaced": sorted(replacements),
            "sha256": digest(target), "path": str(target)}


def package_notices(font_build: Path, qa_dir: Path, repo_notice: Path | None) -> list[str]:
    licenses = qa_dir / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("Noto-CJK-OFL.txt", "Noto-CJK-NOTICE.txt"):
        source = font_build / "licenses" / name
        ensure(source.is_file(), f"font-build license missing: {source}")
        target = licenses / name
        shutil.copyfile(source, target)
        copied.append(str(target))
    if repo_notice is not None:
        ensure(repo_notice.is_file(), f"repository NOTICE missing: {repo_notice}")
        target = qa_dir / "NOTICE"
        shutil.copyfile(repo_notice, target)
        copied.append(str(target))
    (qa_dir / "LICENSES.txt").write_text(
        "QA artifact only. Noto Sans CJK TC glyph source is under SIL Open Font License 1.1; "
        "see licenses/. Existing COD4 localization content from patches/ retains its original "
        "copyright and license boundary as described by repository NOTICE. It is not MIT-licensed "
        "by this font builder. This package is not a tested installer or release.\n",
        encoding="utf-8")
    copied.append(str(qa_dir / "LICENSES.txt"))
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oat-bin", type=Path, required=True,
                        help="directory containing pinned OpenAssetTools v0.33.0 Linker and Unlinker")
    parser.add_argument("--original-ff", type=Path, required=True)
    parser.add_argument("--baseline-iwd", type=Path, required=True)
    parser.add_argument("--font-build", type=Path, required=True)
    parser.add_argument("--required", type=Path, required=True)
    parser.add_argument("--expected-required", type=int, default=REQUIRED_COUNT)
    parser.add_argument("--expected-glyphs-per-font", type=int, default=GLYPHS_PER_FONT)
    parser.add_argument("--localize-str", type=Path,
                        help="converted GBK OAT STR; keys/tokens and linked values are verified")
    parser.add_argument("--repo-notice", type=Path,
                        help="repository NOTICE to copy into QA package when available")
    parser.add_argument("--output", type=Path, required=True,
                        help="new or empty directory for scratch, linked FF, roundtrip, and QA IWD")
    args = parser.parse_args(argv)
    for key in ("oat_bin", "original_ff", "baseline_iwd", "font_build", "required", "output"):
        setattr(args, key, getattr(args, key).resolve())
    if args.localize_str is not None:
        args.localize_str = args.localize_str.resolve()
    if args.repo_notice is not None:
        args.repo_notice = args.repo_notice.resolve()
    else:
        candidate = Path(__file__).resolve().parent.parent / "NOTICE"
        args.repo_notice = candidate if candidate.is_file() else None
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be absent or empty; refusing to overwrite prior build")
    try:
        verified = verify_inputs(args)
        linker = oat_executable(args.oat_bin, "Linker")
        unlinker = oat_executable(args.oat_bin, "Unlinker")
        args.output.mkdir(parents=True, exist_ok=True)
        source_dump = args.output / "source-dump"
        source_dump.mkdir()
        run_oat(unlinker, ["--include-assets", "localize,material", "--output-folder",
                           str(source_dump), str(args.original_ff)],
                args.output / "unlink-source.log")
        stage = args.output / "stage"
        zone_entries = stage_assets(source_dump, args.font_build, stage, args.localize_str)
        linked_dir = args.output / "linked"
        linked_dir.mkdir()
        link_log = run_oat(linker, ["--load", str(args.original_ff), "--base-folder",
                                    str(args.output), "--asset-search-path", str(stage),
                                    "--source-search-path", str(stage / "zone_source"),
                                    "--output-folder", str(linked_dir), "code_post_gfx"],
                           args.output / "link.log")
        verify_link_log(link_log)
        linked_ff = linked_dir / "code_post_gfx.ff"
        ensure(linked_ff.is_file() and linked_ff.stat().st_size > 100_000,
               "Linker did not produce a plausible FF")
        final_ff = args.output / "code_post_gfx.ff"
        shutil.copyfile(linked_ff, final_ff)
        roundtrip = args.output / "roundtrip"
        roundtrip.mkdir()
        dump_log = run_oat(unlinker, ["--include-assets", "font,localize", "--output-folder",
                                       str(roundtrip), str(final_ff)],
                           args.output / "unlink-roundtrip.log")
        for name in CORE_FONTS:
            ensure(f'Dumped font "fonts/{name}"' in dump_log,
                   f"Unlinker did not report rebuilt font {name}")
        font_counts = verify_roundtrip(args.font_build, roundtrip, args.expected_glyphs_per_font)
        pixels = inspect_pixels(roundtrip, args.font_build, args.required, args.expected_required)
        ensure(pixels["pass"] and all(item["drawable"] == args.expected_required
                                      for item in pixels["fonts"].values()),
               "roundtrip glyphs do not point to drawable atlas pixels")
        localize_report = None
        if args.localize_str is not None:
            from zh_tw_text_build import parse_str
            desired = {e.key: e.text for e in parse_str(args.localize_str.read_bytes())}
            actual_path = roundtrip / "english/localizedstrings/code_post_gfx.str"
            actual = {e.key: e.text for e in parse_str(actual_path.read_bytes())}
            ensure(desired == actual, "linked LocalizeEntry keys or values changed in roundtrip")
            localize_report = {"keys": len(actual), "all_values_equal": True,
                               "source_str_sha256": digest(args.localize_str),
                               "roundtrip_str_sha256": digest(actual_path)}
        image_roundtrip = verify_image_roundtrip(
            unlinker, final_ff, stage, args.font_build, args.output / "roundtrip-images")
        iwd = repack_iwd(args.baseline_iwd, args.font_build,
                         args.output / "qa-package" / "localized_chinese_iw15.iwd")
        notices = package_notices(args.font_build, args.output / "qa-package", args.repo_notice)
        result = {
            "pass": True, "oat_version_expected": "v0.33.0",
            "oat_linker_sha256": digest(linker), "oat_unlinker_sha256": digest(unlinker),
            "original_ff_sha256": digest(args.original_ff),
            "baseline_iwd_sha256": digest(args.baseline_iwd),
            "font_build_manifest_sha256": digest(args.font_build / "font-build-manifest.json"),
            "original_zone_entries": zone_entries,
            "font_glyph_counts": font_counts,
            "required": args.expected_required,
            "localize_roundtrip": localize_report,
            "roundtrip_pixels": pixels,
            "roundtrip_images": image_roundtrip,
            "linked_ff": {"path": str(final_ff), "size": final_ff.stat().st_size,
                          "sha256": digest(final_ff)},
            "qa_iwd": iwd,
            "qa_notices": notices,
            "roundtrip_fonts": str(roundtrip / "fonts"),
        }
        report = args.output / "link-result.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(f"FF: {final_ff} ({result['linked_ff']['sha256']})")
        print(f"QA IWD: {iwd['path']} ({iwd['sha256']})")
        print(f"Roundtrip: {len(font_counts)} fonts, {args.expected_required}/{args.expected_required} drawable per font")
        print(f"Result: {report}")
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"font link failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
