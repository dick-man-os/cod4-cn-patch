#!/usr/bin/env python3
"""Deterministic GBK text assets for the single-player zh-TW QA candidate."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from zh_tw_payload_tool import (get_opencc, load_glossary, scan_gbk_spans,
                                to_zh_tw, requires_multibyte_gbk_glyph)

HAZARDS = ("計程車兵", "最最佳化", "重重新整理")
TOKEN = re.compile(r'\[\{[^\r\n]*?\}\]|&&\d+|\^[0-9A-Za-z]|'
                   r'%(?:\d+\$)?[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|ll|[hlLjzt])?[diuoxXfFeEgGaAcspn%]|'
                   r'\\.|[\x00-\x1f\x7f"\\]')
QUOTED = rb'"((?:[^"\\\r\n]|\\[rntf"\\])*)"'
VALUE_LINE = re.compile(rb'(?m)^(LANG_ENGLISH[ \t]+)' + QUOTED + rb'([ \t]*\r?$)')
REFERENCE = re.compile(rb'REFERENCE[ \t]+(?:([A-Za-z0-9_]+)|' + QUOTED + rb')[ \t]*')
PAYLOAD_NAME = re.compile(r'([A-Za-z0-9_]+)\.ff\.dump\.([0-9A-Fa-f]+)\.bin')
PROBES = '遊戲戰國開關槍體讀載選「」《》'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def unescape(raw: bytes) -> bytes:
    table = {ord(k): v for k, v in [('r', 13), ('n', 10), ('t', 9), ('f', 12), ('"', 34), ('\\', 92)]}
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == 92:
            i += 1
            if i == len(raw) or raw[i] not in table:
                raise ValueError('unknown or truncated OAT string escape')
            out.append(table[raw[i]])
        else:
            out.append(raw[i])
        i += 1
    return bytes(out)


def escape(raw: bytes) -> bytes:
    # OAT parses bytes, so GBK trail byte 0x5C must be escaped too.
    table = {13: b'\\r', 10: b'\\n', 9: b'\\t', 12: b'\\f', 34: b'\\"', 92: b'\\\\'}
    return b''.join(table.get(value, bytes([value])) for value in raw)


@dataclass(frozen=True)
class Entry:
    key: str
    text: str
    start: int
    end: int


def parse_str(data: bytes) -> list[Entry]:
    """Parse the bounded OAT dump grammar while retaining exact byte spans."""
    entries = []
    keys = set()
    pending = None
    offset = 0
    ended = False
    headers = []
    for line in data.splitlines(keepends=True):
        raw = line.rstrip(b'\r\n')
        stripped = raw.strip()
        if not stripped or stripped.startswith(b'//'):
            offset += len(line)
            continue
        if ended:
            raise ValueError('unexpected content after ENDMARKER')
        ref = REFERENCE.fullmatch(raw)
        value = VALUE_LINE.fullmatch(raw)
        if ref:
            if pending is not None:
                raise ValueError('reference without a language value')
            key = (ref[1] or unescape(ref[2])).decode('ascii')
            if key in keys:
                raise ValueError(f'duplicate reference: {key}')
            keys.add(key)
            pending = key
        elif value:
            if pending is None:
                raise ValueError('language value without a reference')
            text = unescape(value[2]).decode('gbk', errors='strict')
            if '\x00' in text:
                raise ValueError(f'NUL in LocalizeEntry {pending}')
            entries.append(Entry(pending, text, offset + value.start(2), offset + value.end(2)))
            pending = None
        elif stripped == b'ENDMARKER':
            if pending is not None:
                raise ValueError('last reference has no value')
            ended = True
        elif re.fullmatch(rb'(VERSION|CONFIG|FILENOTES)[ \t]+' + QUOTED + rb'[ \t]*', raw):
            if entries or pending:
                raise ValueError('header appears after entries')
            headers.append(raw.split()[0])
        else:
            raise ValueError(f'unsupported STR syntax at byte {offset}: {raw[:80]!r}')
        offset += len(line)
    if not ended or headers != [b'VERSION', b'CONFIG', b'FILENOTES'] or not entries:
        raise ValueError('incomplete STR document')
    return entries


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text)


def check_hazards(text: str) -> None:
    for bad in HAZARDS:
        if bad in text:
            raise ValueError(f'semantic conversion hazard: {bad}')


def convert_ui(text: str, glossary: dict[str, str], cc) -> str:
    # Glossary keys must be normalized too: s2tw turns 读取 into 讀取 first.
    normalized = {cc.convert(key): value for key, value in glossary.items()}
    pattern = re.compile('|'.join(re.escape(key) for key in sorted(normalized, key=lambda x: (-len(x), x))) or r'(?!)')
    pieces = []
    cursor = 0
    for match in TOKEN.finditer(text):
        part = cc.convert(text[cursor:match.start()])
        pieces.extend((pattern.sub(lambda m: normalized[m[0]], part), match[0]))
        cursor = match.end()
    part = cc.convert(text[cursor:])
    pieces.append(pattern.sub(lambda m: normalized[m[0]], part))
    converted = ''.join(pieces)
    if tokens(text) != tokens(converted):
        raise ValueError('formatting/control token sequence changed')
    check_hazards(converted)
    converted.encode('gbk', errors='strict')
    return converted


def convert_str(data: bytes, glossary: dict[str, str], cc) -> tuple[bytes, dict, set[str]]:
    entries = parse_str(data)
    out = bytearray()
    cursor = 0
    changed = 0
    chars = set()
    for entry in entries:
        value = convert_ui(entry.text, glossary, cc)
        out.extend(data[cursor:entry.start])
        out.extend(escape(value.encode('gbk')))
        cursor = entry.end
        changed += value != entry.text
        chars.update(ch for ch in value if requires_multibyte_gbk_glyph(ch))
    out.extend(data[cursor:])
    output = bytes(out)
    parsed = parse_str(output)
    if [e.key for e in parsed] != [e.key for e in entries]:
        raise ValueError('LocalizeEntry keys or ordering changed')
    for before, after in zip(entries, parsed):
        if tokens(before.text) != tokens(after.text):
            raise ValueError(f'control tokens changed for {before.key}')
    return output, {'keys': len(entries), 'changed_values': changed,
                    'source_sha256': sha(data), 'output_sha256': sha(output),
                    'key_order_sha256': sha('\n'.join(e.key for e in entries).encode()),
                    'formatting_tokens_preserved': True}, chars


def apply_spans(data: bytes, entries: list[dict]) -> bytes:
    """Validate every span against original bytes before changing anything."""
    previous = 0
    replacements = []
    for item in sorted(entries, key=lambda x: (x['start'], x['end'])):
        start, end = item['start'], item['end']
        if not isinstance(start, int) or not isinstance(end, int) or not (previous <= start < end <= len(data)):
            raise ValueError('overlapping, ambiguous or out-of-bounds text spans')
        expected = bytes.fromhex(item['original_hex'])
        if data[start:end] != expected:
            raise ValueError('source bytes drifted')
        replacement = item['zh_tw'].encode('gbk', errors='strict')
        if len(replacement) != end - start:
            raise ValueError('replacement changes fixed-offset byte length')
        if tokens(expected.decode('gbk')) != tokens(item['zh_tw']):
            raise ValueError('payload formatting/control token sequence changed')
        check_hazards(item['zh_tw'])
        replacements.append((start, end, replacement))
        previous = end
    out = bytearray(data)
    for start, end, replacement in replacements:
        out[start:end] = replacement
    if len(out) != len(data):
        raise ValueError('payload length changed')
    return bytes(out)


def build_text(repo: Path, source_str: Path, output: Path) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError('text output must be new or empty')
    pins = json.loads((repo / 'tools/zh_tw_source_manifest.json').read_text(encoding='utf-8'))
    source = repo / 'patches/zone/chinese'
    paths = {p.name: p for p in source.glob('*.bin')}
    if set(paths) != set(pins['payloads']):
        raise ValueError('payload source inventory drifted')
    for name, path in paths.items():
        if sha(path.read_bytes()) != pins['payloads'][name]['sha256']:
            raise ValueError(f'payload source drifted: {name}')
    glossary = load_glossary(repo / 'tools/zh_tw_glossary.json')
    cc = get_opencc('s2tw')
    manifest = {'schema': 1, 'locale': 'zh-TW', 'scope': 'single-player',
                'translation_status': 'automatic QA candidate; not human-approved',
                'opencc_config': 's2tw', 'entries': [], 'payloads': [], 'excluded': []}
    chars = set(PROBES)
    offset_ranges = {}
    planned = []
    for name in sorted(paths):
        data = paths[name].read_bytes()
        entries = []
        for span in scan_gbk_spans(data):
            value = to_zh_tw(span.text, glossary, cc)
            item = {'file': name, 'start': span.start, 'end': span.end,
                    'original_hex': span.raw.hex(), 'original': span.text,
                    'zh_tw': value, 'approved': False}
            entries.append(item)
            chars.update(ch for ch in value if requires_multibyte_gbk_glyph(ch))
        converted = apply_spans(data, entries)
        manifest['entries'].extend(entries)
        match = PAYLOAD_NAME.fullmatch(name)
        if not match:
            if name != 'aftermath.ff.dump.bin' or entries:
                raise ValueError(f'ambiguous payload filename: {name}')
            manifest['excluded'].append({'file': name, 'reason': 'legacy English-only data has no offset; not installed'})
            continue
        ff_name, offset = match[1] + '.ff', int(match[2], 16)
        if ff_name.endswith('_mp.ff') or ff_name.startswith('mp_'):
            manifest['excluded'].append({'file': name, 'reason': 'multiplayer is outside this QA build'})
            continue
        offset_ranges.setdefault(ff_name, []).append((offset, offset + len(data), name))
        record = {'file': name, 'ff_name': ff_name, 'offset': offset, 'size': len(data),
                  'source_sha256': sha(data), 'sha256': sha(converted), 'text_spans': len(entries)}
        manifest['payloads'].append(record)
        planned.append((output / 'zone/chinese' / name, converted))
    for name, ranges in offset_ranges.items():
        ordered = sorted(ranges)
        if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])):
            raise ValueError(f'overlapping mission payload ranges: {name}')
    if len(manifest['entries']) != 2487:
        raise ValueError('detected text entry checkpoint drifted')
    str_data = source_str.read_bytes()
    converted, report, ui_chars = convert_str(str_data, glossary, cc)
    chars.update(ui_chars)
    manifest['localize'] = report
    localization = (repo / 'locales/zh-TW/localization.tw').read_text(encoding='utf-8')
    baseline_loc = (repo / 'patches/localization.cn').read_bytes().decode('gbk')
    keys = lambda s: re.findall(r'^WIN_[A-Z0-9_]+$', s, flags=re.MULTILINE)
    if keys(localization.replace('\r\n', '\n')) != keys(baseline_loc.replace('\r\n', '\n')):
        raise ValueError('localization.tw keys differ from original localization.cn')
    chars.update(ch for ch in localization if requires_multibyte_gbk_glyph(ch))
    planned.extend([(output / 'code_post_gfx.zh-TW.str', converted),
                    (output / 'localization.txt', localization.encode('gbk')),
                    (output / 'required_zh_tw_glyphs.txt', (''.join(sorted(chars)) + '\n').encode('utf-8'))])
    for path, data in planned:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest['required_glyphs'] = len(chars)
    manifest['detected_entries'] = len(manifest['entries'])
    manifest['installed_payload_count'] = len(manifest['payloads'])
    manifest['installed_text_spans'] = sum(p['text_spans'] for p in manifest['payloads'])
    dump_json(output / 'text-build-manifest.json', manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-str', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_text(args.repo, args.source_str, args.output)
    print(f"STR keys: {result['localize']['keys']}; changed values: {result['localize']['changed_values']}")
    print(f"Payload entries: {result['detected_entries']}; SP payloads: {result['installed_payload_count']}")
    print(f"Required glyphs including UI: {result['required_glyphs']}")


if __name__ == '__main__':
    main()
