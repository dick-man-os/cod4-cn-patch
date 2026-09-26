#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import struct
import zipfile
import zlib
from pathlib import Path

def ff_decompress(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"IWff":
        raise RuntimeError(f"not a COD4 IWff: {path}")
    return zlib.decompress(data[12:])

def iwi_header(data: bytes) -> dict:
    # COD4 IWI v6 has a compact little-endian header. We report raw values
    # conservatively instead of assuming undocumented fields.
    out = {"size": len(data), "magic": data[:3].decode("ascii", "replace")}
    if len(data) >= 16:
        out.update({
            "version": data[3],
            "format": data[4],
            "flags": data[5],
            "raw_header_hex": data[:32].hex(),
        })
    return out

def printable_ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b <= 126 else "." for b in data)

def probe_ff(path: Path) -> list[dict]:
    data = ff_decompress(path)
    hits = []
    needle = b"fonts/"
    pos = 0
    while True:
        idx = data.find(needle, pos)
        if idx < 0:
            break
        end = data.find(b"\x00", idx)
        if end < 0:
            end = min(len(data), idx + 128)
        name = data[idx:end].decode("ascii", "replace")
        lo = max(0, idx - 96)
        hi = min(len(data), end + 96)
        before = data[lo:idx]
        # expose aligned little-endian uint32 candidates immediately before string
        ints = []
        for off in range(max(lo, idx - 64), idx - 3, 4):
            ints.append({"offset": off, "u32": struct.unpack_from("<I", data, off)[0]})
        hits.append({
            "offset": idx,
            "name": name,
            "window_start": lo,
            "window_hex": data[lo:hi].hex(),
            "window_ascii": printable_ascii(data[lo:hi]),
            "u32_before": ints[-16:],
        })
        pos = end + 1
    return hits

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iwd", type=Path, required=True)
    ap.add_argument("--ff", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    members = []
    with zipfile.ZipFile(args.iwd) as zf:
        for info in zf.infolist():
            row = {
                "name": info.filename,
                "size": info.file_size,
                "compressed_size": info.compress_size,
                "compression": info.compress_type,
            }
            if info.filename.lower().endswith(".iwi"):
                blob = zf.read(info)
                row["iwi"] = iwi_header(blob)
            members.append(row)

    payload = {
        "iwd": str(args.iwd),
        "members": members,
        "ff": str(args.ff),
        "font_string_hits": probe_ff(args.ff),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"IWD members: {len(members)}")
    print(f"IWI members: {sum(1 for x in members if 'iwi' in x)}")
    print(f"font string hits: {len(payload['font_string_hits'])}")
    for h in payload["font_string_hits"][:30]:
        print(f"0x{h['offset']:X} {h['name']}")
    print(f"report: {args.out}")

if __name__ == "__main__":
    main()
