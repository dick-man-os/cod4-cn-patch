#!/usr/bin/env python3
"""
COD4 zh-TW payload preparation tool.

Phase-1 goals:
- scan legacy GBK/CP936 .bin payloads without modifying originals
- isolate Chinese text spans conservatively
- optionally generate Taiwan Traditional Chinese candidates with OpenCC s2tw
- enforce GBK encodability and exact byte-length safety for fixed-offset payloads
- build only explicitly approved replacements
- generate required CJK glyph inventory for the later font phase

This tool intentionally fails closed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ALLOWED_RE = re.compile(
    r"^[\x09\x0a\x0d\x20-\x7e"
    r"\u3000-\u303f"
    r"\u3400-\u4dbf"
    r"\u4e00-\u9fff"
    r"\uf900-\ufaff"
    r"\uff01-\uff65]+$"
)

DEFAULT_GLOSSARY = {
    "使命召唤4": "決勝時刻4",
    "使命召唤": "決勝時刻",
    "现代战争": "現代戰爭",
    "设置": "設定",
    "游戏": "遊戲",
    "任务": "任務",
    "文件": "檔案",
    "读取": "載入",
    "保存": "儲存",
    "检查点": "檢查點",
    "视频": "影像",
    "选项": "選項",
    "声音": "音效",
    "字幕": "字幕",
    "硬盘": "硬碟",
    "刷新": "更新",
    "服务器": "伺服器",
    "显卡": "顯卡",
    "驱动": "驅動",
    "运行": "執行",
    "程序": "程式",
    "硬件": "硬體",
    "卸载": "移除",
    "错误": "錯誤",
    "退出": "離開",
}


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str
    raw: bytes

    @property
    def length(self) -> int:
        return self.end - self.start


def _looks_like_text(text: str) -> bool:
    if not text or not CJK_RE.search(text):
        return False
    return bool(ALLOWED_RE.fullmatch(text))


def scan_gbk_spans(data: bytes, min_len: int = 2) -> list[Span]:
    """Conservatively scan NUL/0xFF-delimited GBK text runs containing CJK.

    The legacy payloads commonly use NUL and 0xFF as boundaries/padding.
    We do not attempt to decode arbitrary binary blocks.
    """
    spans: list[Span] = []
    n = len(data)
    i = 0

    while i < n:
        while i < n and data[i] in (0x00, 0xFF):
            i += 1
        start = i
        while i < n and data[i] not in (0x00, 0xFF):
            i += 1
        end = i

        if end - start < min_len:
            continue

        raw = data[start:end]
        try:
            text = raw.decode("gbk", errors="strict")
        except UnicodeDecodeError:
            continue

        if _looks_like_text(text):
            spans.append(Span(start=start, end=end, text=text, raw=raw))

    return spans


def load_glossary(path: Path | None) -> dict[str, str]:
    glossary = dict(DEFAULT_GLOSSARY)
    if path is not None:
        user = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(user, dict):
            raise ValueError("glossary must be a JSON object")
        glossary.update({str(k): str(v) for k, v in user.items()})
    return dict(sorted(glossary.items(), key=lambda kv: len(kv[0]), reverse=True))


def apply_glossary(text: str, glossary: dict[str, str]) -> str:
    for src, dst in glossary.items():
        text = text.replace(src, dst)
    return text


def get_opencc(config: str = "s2tw"):
    try:
        from opencc import OpenCC  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "OpenCC is required for automatic conversion. "
            "Install with: pip install opencc-python-reimplemented"
        ) from exc
    return OpenCC(config)


def to_zh_tw(text: str, glossary: dict[str, str], opencc=None, opencc_config: str = "s2tw") -> str:
    if opencc is None:
        opencc = get_opencc(opencc_config)
    converted = opencc.convert(text)
    return apply_glossary(converted, glossary)


def encode_gbk(text: str) -> tuple[bytes | None, str | None]:
    try:
        return text.encode("gbk", errors="strict"), None
    except UnicodeEncodeError as exc:
        return None, str(exc)


def make_entry(rel: str, span: Span, candidate: str | None) -> dict:
    result = {
        "id": f"{rel}:{span.start}:{span.end}",
        "file": rel,
        "start": span.start,
        "end": span.end,
        "original": span.text,
        "original_len": span.length,
        "original_hex": span.raw.hex(),
        "zh_tw": candidate,
        "approved": False,
        "notes": "",
    }

    if candidate is None:
        result.update({
            "gbk_encodable": None,
            "candidate_len": None,
            "exact_length": None,
            "status": "needs_translation",
        })
        return result

    encoded, err = encode_gbk(candidate)
    if encoded is None:
        result.update({
            "gbk_encodable": False,
            "candidate_len": None,
            "exact_length": False,
            "status": "unencodable_gbk",
            "encoding_error": err,
        })
        return result

    exact = len(encoded) == span.length
    result.update({
        "gbk_encodable": True,
        "candidate_len": len(encoded),
        "candidate_hex": encoded.hex(),
        "exact_length": exact,
        "status": "candidate_safe" if exact else "length_mismatch",
    })
    return result


def iter_bin_files(source: Path) -> Iterable[Path]:
    yield from sorted(p for p in source.rglob("*.bin") if p.is_file())


def command_scan(args) -> int:
    source = args.source.resolve()
    glossary = load_glossary(args.glossary)
    opencc = get_opencc(args.opencc_config) if args.auto else None
    entries: list[dict] = []

    for path in iter_bin_files(source):
        rel = path.relative_to(source).as_posix()
        data = path.read_bytes()
        for span in scan_gbk_spans(data):
            candidate = to_zh_tw(span.text, glossary, opencc) if opencc else None
            entries.append(make_entry(rel, span, candidate))

    payload = {
        "schema": 1,
        "encoding": "gbk",
        "source": str(source),
        "auto_generated_with_opencc": bool(args.auto),
        "opencc_config": args.opencc_config if args.auto else None,
        "entries": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    safe = sum(1 for x in entries if x["status"] == "candidate_safe")
    mismatch = sum(1 for x in entries if x["status"] == "length_mismatch")
    unenc = sum(1 for x in entries if x["status"] == "unencodable_gbk")
    print(f"scanned entries: {len(entries)}")
    if args.auto:
        print(f"candidate_safe: {safe}")
        print(f"length_mismatch: {mismatch}")
        print(f"unencodable_gbk: {unenc}")
    print(f"manifest: {args.out}")
    return 0


def command_refresh(args) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    glossary = load_glossary(args.glossary)
    opencc = get_opencc(args.opencc_config)

    for entry in manifest["entries"]:
        candidate = to_zh_tw(entry["original"], glossary, opencc)
        updated = make_entry(
            entry["file"],
            Span(
                start=int(entry["start"]),
                end=int(entry["end"]),
                text=entry["original"],
                raw=bytes.fromhex(entry["original_hex"]),
            ),
            candidate,
        )
        updated["approved"] = bool(entry.get("approved", False))
        updated["notes"] = entry.get("notes", "")
        if entry.get("manual", False) and entry.get("zh_tw"):
            manual = str(entry["zh_tw"])
            updated = make_entry(
                entry["file"],
                Span(
                    start=int(entry["start"]),
                    end=int(entry["end"]),
                    text=entry["original"],
                    raw=bytes.fromhex(entry["original_hex"]),
                ),
                manual,
            )
            updated["approved"] = bool(entry.get("approved", False))
            updated["notes"] = entry.get("notes", "")
            updated["manual"] = True
        entry.clear()
        entry.update(updated)

    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"refreshed: {args.manifest}")
    return 0


def command_build(args) -> int:
    source_root = args.source.resolve()
    output_root = args.output.resolve()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    grouped: dict[str, list[dict]] = {}
    for entry in manifest["entries"]:
        if not entry.get("approved"):
            continue
        if not entry.get("gbk_encodable"):
            raise RuntimeError(f"{entry['id']}: approved entry is not GBK encodable")
        if not entry.get("exact_length"):
            raise RuntimeError(f"{entry['id']}: approved entry changes byte length")
        grouped.setdefault(entry["file"], []).append(entry)

    if output_root.exists() and args.clean:
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    changed = 0
    for rel, entries in grouped.items():
        src = source_root / rel
        if not src.exists():
            raise FileNotFoundError(src)
        data = bytearray(src.read_bytes())

        for entry in sorted(entries, key=lambda e: int(e["start"])):
            start, end = int(entry["start"]), int(entry["end"])
            current = bytes(data[start:end])
            expected = bytes.fromhex(entry["original_hex"])
            if current != expected:
                raise RuntimeError(f"{entry['id']}: source bytes drifted; refusing to patch")
            replacement = str(entry["zh_tw"]).encode("gbk")
            if len(replacement) != end - start:
                raise RuntimeError(f"{entry['id']}: replacement length changed after validation")
            data[start:end] = replacement

        dst = output_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        changed += 1

    print(f"built files: {changed}")
    print(f"output: {output_root}")
    return 0


def requires_multibyte_gbk_glyph(ch: str) -> bool:
    """Return True when COD4's Chinese path must resolve a two-byte GBK glyph.

    This intentionally includes CJK punctuation and symbols, not only Han
    ideographs.  ASCII/control characters are handled by the engine's built-in
    single-byte glyph range and are excluded here.
    """
    try:
        raw = ch.encode("gbk", errors="strict")
    except UnicodeEncodeError:
        return False
    return len(raw) == 2


def command_glyphs(args) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    chars: set[str] = set()

    for entry in manifest["entries"]:
        text = entry.get("zh_tw")
        if not text:
            continue
        if args.approved_only and not entry.get("approved"):
            continue
        chars.update(ch for ch in text if requires_multibyte_gbk_glyph(ch))

    if args.localization and args.localization.exists():
        text = args.localization.read_text(encoding="utf-8")
        chars.update(ch for ch in text if requires_multibyte_gbk_glyph(ch))

    ordered = "".join(sorted(chars))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(ordered + "\n", encoding="utf-8")
    print(f"glyphs: {len(ordered)}")
    print(f"output: {args.out}")
    return 0


def command_convert_localization(args) -> int:
    glossary = load_glossary(args.glossary)
    opencc = get_opencc(args.opencc_config)
    text = args.source.read_text(encoding=args.input_encoding)
    converted = to_zh_tw(text, glossary, opencc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(converted, encoding="utf-8", newline="")
    print(f"converted localization: {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="COD4 zh-TW payload preparation tool")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="scan GBK payloads into a review manifest")
    s.add_argument("source", type=Path, help="directory containing .bin payloads")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--glossary", type=Path)
    s.add_argument("--auto", action="store_true", help="generate OpenCC Taiwan Traditional candidates")
    s.add_argument("--opencc-config", default="s2tw", choices=("s2tw", "s2twp"))
    s.set_defaults(func=command_scan)

    r = sub.add_parser("refresh", help="refresh zh-TW candidates in an existing manifest")
    r.add_argument("manifest", type=Path)
    r.add_argument("--glossary", type=Path)
    r.add_argument("--opencc-config", default="s2tw", choices=("s2tw", "s2twp"))
    r.set_defaults(func=command_refresh)

    b = sub.add_parser("build", help="build approved exact-length payload replacements")
    b.add_argument("manifest", type=Path)
    b.add_argument("--source", type=Path, required=True)
    b.add_argument("--output", type=Path, required=True)
    b.add_argument("--clean", action="store_true")
    b.set_defaults(func=command_build)

    g = sub.add_parser("glyphs", help="generate required CJK glyph inventory")
    g.add_argument("manifest", type=Path)
    g.add_argument("--localization", type=Path)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--approved-only", action="store_true")
    g.set_defaults(func=command_glyphs)

    l = sub.add_parser("convert-localization", help="convert text localization file to zh-TW")
    l.add_argument("source", type=Path)
    l.add_argument("--out", type=Path, required=True)
    l.add_argument("--glossary", type=Path)
    l.add_argument("--input-encoding", default="utf-8")
    l.add_argument("--opencc-config", default="s2tw", choices=("s2tw", "s2twp"))
    l.set_defaults(func=command_convert_localization)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
