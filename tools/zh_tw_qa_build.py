#!/usr/bin/env python3
"""Build a reproducible LOCAL single-player QA package. Never publishes it."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from zh_tw_font_builder import load_pinned_font, unicode_cmap_groups, required_mapping, build as build_fonts
from zh_tw_font_link import (oat_executable, run_oat, digest, ORIGINAL_FF_SHA256, main as link_fonts)
from zh_tw_text_build import build_text, dump_json, parse_str
from tools.zh_tw_qa_install import verify_package

REQUIRED = 1518
ADDED = 596
GLYPHS = 2338


def fresh(path: Path):
    if path.exists() and any(path.iterdir()):
        raise ValueError(f'output must be absent or empty: {path}')
    path.mkdir(parents=True, exist_ok=True)


def assemble(repo: Path, text: Path, font: Path, link: Path, output: Path) -> dict:
    fresh(output)
    text_manifest = json.loads((text / 'text-build-manifest.json').read_text(encoding='utf-8'))
    font_manifest = json.loads((font / 'font-build-manifest.json').read_text(encoding='utf-8'))
    link_result = json.loads((link / 'link-result.json').read_text(encoding='utf-8'))
    if not link_result['pass'] or not link_result['localize_roundtrip']['all_values_equal']:
        raise ValueError('integrated LocalizeEntry/font roundtrip did not pass')
    if link_result['required'] != REQUIRED or text_manifest['required_glyphs'] != REQUIRED:
        raise ValueError('integrated glyph checkpoint changed')
    copies = {
        'cod4_cn_patch.py': repo / 'cod4_cn_patch.py',
        'tools/zh_tw_qa_install.py': repo / 'tools/zh_tw_qa_install.py',
        'localization.txt': text / 'localization.txt',
        'locales/zh-TW/localization.tw': repo / 'locales/zh-TW/localization.tw',
        'main/localized_chinese_iw15.iwd': link / 'qa-package/localized_chinese_iw15.iwd',
        'zone/english/code_post_gfx.ff': link / 'code_post_gfx.ff',
        'manifests/source-profile.json': repo / 'tools/zh_tw_game_profile.json',
        'manifests/text-build.json': text / 'text-build-manifest.json',
        'manifests/font-build.json': font / 'font-build-manifest.json',
        'manifests/code_post_gfx.zh-TW.str': text / 'code_post_gfx.zh-TW.str',
        'manifests/required_zh_tw_glyphs.txt': text / 'required_zh_tw_glyphs.txt',
        'licenses/Noto-CJK-OFL.txt': repo / 'licenses/Noto-CJK-OFL.txt',
        'licenses/Noto-CJK-NOTICE.txt': repo / 'licenses/Noto-CJK-NOTICE.txt',
        'NOTICE': repo / 'NOTICE', 'LICENSE': repo / 'LICENSE',
        'README-QA.md': repo / 'docs/zh-tw-qa.md',
    }
    for item in text_manifest['payloads']:
        relative = 'zone/chinese/' + item['file']
        copies[relative] = text / relative
    for relative, source in sorted(copies.items()):
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    profile = json.loads((output / 'manifests/source-profile.json').read_text(encoding='utf-8'))
    payloads = []
    for item in text_manifest['payloads']:
        pin = profile['mission_spans'][item['file']]
        if any(pin[key] != item[key] for key in ('ff_name', 'offset', 'size')):
            raise ValueError('payload range differs from supported Steam source')
        payloads.append(dict(item, preimage_sha256=pin['preimage_sha256']))
    report = {'schema': 1, 'scope': 'single-player', 'status': 'experimental-local-QA',
              'required': REQUIRED, 'font_glyph_counts': link_result['font_glyph_counts'],
              'roundtrip_pixels': link_result['roundtrip_pixels'],
              'roundtrip_images': link_result['roundtrip_images'],
              'localize_roundtrip': link_result['localize_roundtrip'],
              'source_zone_entries': link_result['original_zone_entries'],
              'oat_linker_sha256': link_result['oat_linker_sha256'],
              'oat_unlinker_sha256': link_result['oat_unlinker_sha256'],
              'font_sha256': font_manifest['font_sha256'],
              'game_profile': profile['id'], 'runtime_validation': 'pending-user-test'}
    dump_json(output / 'manifests/build-report.json', report)
    dump_json(output / 'manifests/ui-values.json',
              {e.key: e.text for e in parse_str((text / 'code_post_gfx.zh-TW.str').read_bytes())})
    (output / 'LOCALIZATION-DATA-NOTICE.txt').write_text(
        'This package contains automatic zh-TW QA derivatives of the 2009 Chinese translation data.\n'
        'Original data copyright remains with the authors listed in NOTICE; no MIT claim is made for it.\n'
        'Noto glyph source is under SIL OFL 1.1; see licenses/. Runtime and human translation QA are pending.\n',
        encoding='utf-8')
    files = {path.relative_to(output).as_posix(): {'size': path.stat().st_size, 'sha256': digest(path)}
             for path in sorted(output.rglob('*')) if path.is_file()}
    manifest = {'schema': 1, 'locale': 'zh-TW', 'scope': 'single-player',
                'status': 'experimental-local-QA', 'game_profile': profile['id'],
                'files': files, 'payloads': payloads,
                'runtime_validation': 'pending-user-test'}
    dump_json(output / 'manifests/package.json', manifest)
    names = sorted(set(files) | {'manifests/package.json'})
    (output / 'SHA256SUMS').write_text(''.join(f'{digest(output / name)}  {name}\n' for name in names), encoding='utf-8')
    verify_package(output)
    return manifest


def make_zip(root: Path, target: Path):
    if target.exists():
        raise ValueError(f'ZIP already exists: {target}')
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--oat-bin', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    repo, work, output = args.repo.resolve(), args.work.resolve(), args.output.resolve()
    if output.is_relative_to(work) or work.is_relative_to(output):
        raise ValueError('work and package output must be separate directories')
    fresh(work)
    if output.exists() and any(output.iterdir()):
        raise ValueError('package output must be absent or empty')
    original_ff = repo / 'patches/zone/code_post_gfx.ff'
    if digest(original_ff) != ORIGINAL_FF_SHA256:
        raise ValueError('original font/UI fastfile changed')
    source_dump = work / 'source'
    source_dump.mkdir()
    run_oat(oat_executable(args.oat_bin.resolve(), 'Unlinker'),
            ['--include-assets', 'font,localize', '--output-folder', str(source_dump), str(original_ff)],
            work / 'unlink-source.log')
    text_dir, font_dir, link_dir = work / 'text', work / 'font', work / 'link'
    text_report = build_text(repo, source_dump / 'english/localizedstrings/code_post_gfx.str', text_dir)
    if text_report['required_glyphs'] != REQUIRED:
        raise ValueError('review the updated integrated glyph checkpoint before building')
    font_data = load_pinned_font(args.font)
    mapping = required_mapping(text_dir / 'required_zh_tw_glyphs.txt', unicode_cmap_groups(font_data))
    build_fonts(args.font, font_data, mapping, source_dump, repo / 'patches/main/localized_chinese_iw15.iwd',
                repo / 'licenses/Noto-CJK-OFL.txt', repo / 'licenses/Noto-CJK-NOTICE.txt', font_dir,
                expected_required=REQUIRED, expected_added=ADDED)
    code = link_fonts(['--oat-bin', str(args.oat_bin.resolve()), '--original-ff', str(original_ff),
                       '--baseline-iwd', str(repo / 'patches/main/localized_chinese_iw15.iwd'),
                       '--font-build', str(font_dir), '--required', str(text_dir / 'required_zh_tw_glyphs.txt'),
                       '--localize-str', str(text_dir / 'code_post_gfx.zh-TW.str'),
                       '--expected-required', str(REQUIRED), '--expected-glyphs-per-font', str(GLYPHS),
                       '--repo-notice', str(repo / 'NOTICE'), '--output', str(link_dir)])
    if code != 0:
        raise ValueError('font/text link roundtrip failed')
    assemble(repo, text_dir, font_dir, link_dir, output)
    zip_path = Path(str(output) + '.zip')
    make_zip(output, zip_path)
    print(f'LOCAL QA package: {output}')
    print(f'ZIP SHA-256: {digest(zip_path)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
