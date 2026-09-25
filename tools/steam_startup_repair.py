#!/usr/bin/env python3
"""
Safe Steam first-run repair for Call of Duty 4 (2007).

Purpose:
Steam's legacy installscript.vdf may hang while launching the obsolete
PunkBuster installer. For single-player users, this tool can disable only the
PunkBuster block in installscript.vdf while keeping an automatic backup.

Commands:
  status
  disable-punkbuster
  restore

The tool fails closed on ambiguous or malformed VDF input.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

BACKUP_DIR = ".cod4tw_startup_bak"
SCRIPT_NAME = "installscript.vdf"


@dataclass(frozen=True)
class Block:
    label_start: int
    brace_start: int
    brace_end: int
    label: str

    @property
    def end(self) -> int:
        return self.brace_end + 1


def _decode_vdf(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise RuntimeError("installscript.vdf uses an unsupported text encoding")


def _find_matching_brace(text: str, opening: int) -> int:
    if opening >= len(text) or text[opening] != "{":
        raise ValueError("opening index does not point at '{'")

    depth = 0
    in_quote = False
    escaped = False
    for i in range(opening, len(text)):
        ch = text[i]
        if in_quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = False
            continue

        if ch == '"':
            in_quote = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
            if depth < 0:
                break
    raise RuntimeError("malformed VDF: unmatched brace")


def find_punkbuster_blocks(text: str) -> list[Block]:
    """Find named VDF blocks whose label identifies PunkBuster.

    This deliberately does not remove arbitrary lines containing "PB".
    """
    blocks: list[Block] = []
    i = 0
    n = len(text)

    while i < n:
        if text[i] != '"':
            i += 1
            continue

        label_start = i
        i += 1
        chars: list[str] = []
        escaped = False
        while i < n:
            ch = text[i]
            if escaped:
                chars.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                break
            else:
                chars.append(ch)
            i += 1

        if i >= n:
            break

        label = "".join(chars)
        after = i + 1
        while after < n and text[after].isspace():
            after += 1

        if "punkbuster" in label.lower() and after < n and text[after] == "{":
            end = _find_matching_brace(text, after)
            blocks.append(Block(label_start, after, end, label))
            i = end + 1
        else:
            i += 1

    return blocks


def _game_dir(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else Path.cwd().resolve()


def _script_path(game_dir: Path) -> Path:
    return game_dir / SCRIPT_NAME


def _backup_path(game_dir: Path) -> Path:
    return game_dir / BACKUP_DIR / SCRIPT_NAME


def status(game_dir: Path) -> int:
    script = _script_path(game_dir)
    if not script.exists():
        print(f"ERROR: {script} not found")
        return 2

    text, encoding = _decode_vdf(script)
    blocks = find_punkbuster_blocks(text)
    backup = _backup_path(game_dir)

    print(f"game_dir: {game_dir}")
    print(f"installscript: {script}")
    print(f"encoding: {encoding}")
    print(f"punkbuster_blocks: {len(blocks)}")
    for block in blocks:
        body = text[block.label_start:block.end].lower()
        process_markers = [x for x in ("pbsetup", "pbsvc", "punkbuster") if x in body]
        print(f"  - {block.label!r}; markers={','.join(process_markers) or 'label-only'}")
    print(f"backup_exists: {backup.exists()}")

    return 0


def disable_punkbuster(game_dir: Path) -> int:
    script = _script_path(game_dir)
    if not script.exists():
        raise RuntimeError(f"{script} not found")

    text, encoding = _decode_vdf(script)
    blocks = find_punkbuster_blocks(text)

    if not blocks:
        print("No PunkBuster VDF block detected; nothing changed.")
        return 0
    if len(blocks) != 1:
        raise RuntimeError(
            f"Expected exactly one PunkBuster block, found {len(blocks)}; refusing to modify."
        )

    block = blocks[0]
    body = text[block.label_start:block.end].lower()
    if not any(marker in body for marker in ("punkbuster", "pbsetup", "pbsvc")):
        raise RuntimeError("PunkBuster block lacks expected markers; refusing to modify.")

    backup = _backup_path(game_dir)
    if backup.exists():
        raise RuntimeError(
            f"Backup already exists at {backup}. Restore first or inspect it manually."
        )
    backup.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(script, backup)

    start = block.label_start
    # Include preceding indentation but never cross the previous newline.
    line_start = text.rfind("\n", 0, start) + 1
    if text[line_start:start].strip() == "":
        start = line_start

    end = block.end
    # Remove one trailing newline to avoid accumulating blank lines.
    if end < len(text) and text[end] == "\r":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1

    new_text = text[:start] + text[end:]

    # Validate that only the target block disappeared and braces still parse
    # around any remaining PunkBuster labels.
    if find_punkbuster_blocks(new_text):
        raise RuntimeError("PunkBuster block still detected after edit; refusing to write.")

    write_encoding = "utf-8-sig" if encoding == "utf-8-sig" else encoding
    script.write_text(new_text, encoding=write_encoding, newline="")
    print("PunkBuster install block disabled.")
    print(f"Backup: {backup}")
    print("This is intended for single-player / non-PunkBuster use.")
    return 0


def restore(game_dir: Path) -> int:
    script = _script_path(game_dir)
    backup = _backup_path(game_dir)
    if not backup.exists():
        print(f"ERROR: backup not found: {backup}")
        return 2

    shutil.copy2(backup, script)
    shutil.rmtree(backup.parent)
    print("Original installscript.vdf restored.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="COD4 Steam PunkBuster first-run repair")
    p.add_argument("--game-dir", help="COD4 game root; defaults to current directory")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("disable-punkbuster")
    sub.add_parser("restore")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    game_dir = _game_dir(args.game_dir)
    try:
        if args.command == "status":
            return status(game_dir)
        if args.command == "disable-punkbuster":
            return disable_punkbuster(game_dir)
        if args.command == "restore":
            return restore(game_dir)
        raise RuntimeError("unknown command")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
