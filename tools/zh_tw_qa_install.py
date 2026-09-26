"""Bounded zh-TW QA extension of the existing COD4 installer (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import zlib

from cod4_cn_patch import COD4CNPatch


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def byte_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def safe_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (not relative or '\\' in relative or ':' in relative or pure.is_absolute()
            or any(part in ('', '.', '..') for part in relative.split('/'))):
        raise ValueError(f'unsafe manifest path: {relative!r}')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'manifest path escapes root: {relative}')
    current = path
    while current != root:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError(f'symlink/reparse path is unsupported: {relative}')
        current = current.parent
    return path


def verify_package(root: Path) -> dict:
    manifest = read_json(root / 'manifests/package.json')
    if (manifest.get('schema'), manifest.get('locale'), manifest.get('scope'), manifest.get('status')) != (
            1, 'zh-TW', 'single-player', 'experimental-local-QA'):
        raise ValueError('not a supported single-player zh-TW QA package')
    files = manifest.get('files', {})
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    expected = set(files) | {'manifests/package.json', 'SHA256SUMS'}
    if actual != expected:
        raise ValueError('QA package file inventory changed')
    for relative, record in files.items():
        path = safe_path(root, relative)
        if path.stat().st_size != record['size'] or digest(path) != record['sha256']:
            raise ValueError(f'QA package hash mismatch: {relative}')
    sums = ''.join(f"{digest(safe_path(root, name))}  {name}\n"
                   for name in sorted(set(files) | {'manifests/package.json'}))
    if (root / 'SHA256SUMS').read_text(encoding='utf-8') != sums:
        raise ValueError('QA package checksum index changed')
    return manifest


def patched_ff(source: Path, profile: dict, payloads: list[dict], package: Path) -> bytes:
    compressed = source.read_bytes()
    if byte_digest(compressed) != profile['sha256'] or compressed[:12].hex() != profile['header_hex']:
        raise ValueError(f'unsupported mission source: {source.name}')
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed[12:]) + decoder.flush()
    if not decoder.eof or decoder.unused_data or len(raw) != profile['decompressed_size']:
        raise ValueError(f'invalid fastfile compression: {source.name}')
    if byte_digest(raw) != profile['decompressed_sha256']:
        raise ValueError(f'decompressed source drifted: {source.name}')
    previous = 0
    replacements = []
    for item in sorted(payloads, key=lambda x: x['offset']):
        offset, size = item['offset'], item['size']
        if not (previous <= offset < offset + size <= len(raw)):
            raise ValueError(f'overlapping/out-of-bounds payload: {item["file"]}')
        if byte_digest(raw[offset:offset + size]) != item['preimage_sha256']:
            raise ValueError(f'payload preimage drifted: {item["file"]}')
        payload = safe_path(package, 'zone/chinese/' + item['file']).read_bytes()
        if len(payload) != size or byte_digest(payload) != item['sha256']:
            raise ValueError(f'payload output drifted: {item["file"]}')
        replacements.append((offset, payload))
        previous = offset + size
    output = bytearray(raw)
    for offset, payload in replacements:
        output[offset:offset + len(payload)] = payload
    if len(output) != len(raw):
        raise ValueError('mission replacement changed byte length')
    return compressed[:12] + zlib.compress(output)


class COD4ZHTWQAPatch(COD4CNPatch):
    """Use the existing backup/rollback primitives with a bounded QA journal."""

    def __init__(self, game_dir: Path, qa_assets: Path | None = None):
        super().__init__(game_dir, qa_assets)
        self.qa_assets = qa_assets.resolve() if qa_assets else None
        self.journal_path = self.bak_dir / 'qa-install.json'

    def _verify_baseline(self, profile: dict):
        files = profile['files']
        for relative, record in files.items():
            path = safe_path(self.game_dir, relative)
            if not path.is_file() or path.stat().st_size != record['size'] or digest(path) != record['sha256']:
                raise ValueError(f'遊戲來源與支援版本不同：{relative}')
        expected_iwds = {name for name in files if name.startswith('main/')}
        actual_iwds = {p.relative_to(self.game_dir).as_posix() for p in (self.game_dir / 'main').glob('*.iwd')}
        if actual_iwds != expected_iwds:
            raise ValueError('main/ 的 IWD 清單與乾淨 Steam 版本不同')
        expected_ff = {name for name in files if name.startswith('zone/english/')}
        actual_ff = {p.relative_to(self.game_dir).as_posix() for p in (self.game_dir / 'zone/english').glob('*.ff')
                     if not p.name.startswith('mp_') and not p.stem.endswith('_mp')}
        if actual_ff != expected_ff:
            raise ValueError('單機 fastfile 清單與支援版本不同')

    def plan(self) -> dict:
        if self.qa_assets is None:
            raise ValueError('install requires --qa-assets')
        manifest = verify_package(self.qa_assets)
        profile = read_json(self.qa_assets / 'manifests/source-profile.json')
        if manifest['game_profile'] != profile['id']:
            raise ValueError('package profile identity mismatch')
        if self.bak_dir.exists():
            raise ValueError('備份已存在；請先用 uninstall 還原')
        if (self.game_dir / 'zone/chinese').exists():
            raise ValueError('已存在 zone/chinese；此 QA 安裝僅支援乾淨英文版本')
        self._verify_baseline(profile)
        actions = []
        seen = set()

        def add_write(target, source, origin, expected, patches=None):
            safe_path(self.game_dir, target)
            if target in seen:
                raise ValueError(f'duplicate install destination: {target}')
            seen.add(target)
            target_path = self.game_dir / target
            before = profile['files'].get(target)
            if target_path.exists() and before is None:
                raise ValueError(f'非預期的既有目的檔案：{target}')
            action = {'kind': 'write', 'target': target, 'source': source, 'origin': origin,
                      'sha256': expected, 'before_sha256': before['sha256'] if before else None}
            if patches is not None:
                action['payloads'] = patches
            actions.append(action)

        english_iwds = sorted(name for name in profile['files'] if name.startswith('main/localized_english_iw'))
        for name in english_iwds:
            add_write(name.replace('localized_english_', 'localized_chinese_'), name, 'game', profile['files'][name]['sha256'])
        font_source = 'main/localized_chinese_iw15.iwd'
        add_write(font_source, font_source, 'package', manifest['files'][font_source]['sha256'])
        base_indices = sorted(int(Path(name).stem[3:]) for name in profile['files'] if name.startswith('main/iw_'))
        if base_indices != list(range(len(base_indices))):
            raise ValueError('base IWD sequence is not contiguous')
        add_write(f'main/iw_{len(base_indices):02d}.iwd', font_source, 'package', manifest['files'][font_source]['sha256'])
        grouped = {}
        for payload in manifest['payloads']:
            if payload['ff_name'].startswith('mp_') or payload['ff_name'].endswith('_mp.ff'):
                raise ValueError('multiplayer payload in single-player package')
            pin = profile['mission_spans'].get(payload['file'])
            if not pin or any(payload[k] != pin[k] for k in ('ff_name', 'offset', 'size', 'preimage_sha256')):
                raise ValueError('mission payload does not match supported source profile')
            grouped.setdefault(payload['ff_name'], []).append(payload)
        if set(profile['mission_spans']) != {p['file'] for p in manifest['payloads']}:
            raise ValueError('package mission inventory is incomplete')
        mirrors = []
        for relative in sorted(name for name in profile['files'] if name.startswith('zone/english/')):
            name = Path(relative).name
            target = 'zone/chinese/' + name
            if name == 'code_post_gfx.ff':
                checksum = manifest['files'][relative]['sha256']
                add_write(target, relative, 'package', checksum)
                mirrors.append((relative, target, checksum))
            elif name in grouped:
                output = patched_ff(self.game_dir / relative, profile['files'][relative], grouped[name], self.qa_assets)
                checksum = byte_digest(output)
                del output
                add_write(target, relative, 'patched-game', checksum, grouped[name])
                mirrors.append((relative, target, checksum))
            else:
                add_write(target, relative, 'game', profile['files'][relative]['sha256'])
        for target, source, checksum in mirrors:
            add_write(target, source, 'generated-game', checksum)
        for relative in sorted(name for name in profile['files'] if name.startswith('main/localized_')
                               and not name.startswith('main/localized_chinese_')):
            disabled = relative + '.disabled'
            if safe_path(self.game_dir, disabled).exists() or disabled in seen:
                raise ValueError(f'停用目的檔案已存在：{disabled}')
            seen.add(disabled)
            actions.append({'kind': 'rename', 'source': relative, 'target': disabled,
                            'sha256': profile['files'][relative]['sha256'], 'before_sha256': None})
        add_write('localization.txt', 'localization.txt', 'package', manifest['files']['localization.txt']['sha256'])
        return {'schema': 1, 'locale': 'zh-TW', 'status': 'planned', 'game_profile': profile['id'],
                'package_manifest_sha256': digest(self.qa_assets / 'manifests/package.json'),
                'profile': profile, 'actions': actions, 'attempted': [], 'created_dirs': []}

    def _record_dirs(self, parent: Path, journal: dict):
        missing = []
        while not parent.exists() and parent != self.game_dir:
            missing.append(parent)
            parent = parent.parent
        for path in reversed(missing):
            path.mkdir()
            journal['created_dirs'].append(path.relative_to(self.game_dir).as_posix())
            write_json(self.journal_path, journal)

    def _apply(self, action: dict, profile: dict, index: int):
        target = safe_path(self.game_dir, action['target'])
        if action['kind'] == 'rename':
            source = safe_path(self.game_dir, action['source'])
            if digest(source) != action['sha256'] or target.exists():
                raise ValueError('rename source/destination drifted after preflight')
            self._backup(source)
            self._mark_rename(source, target)
            source.rename(target)
            return
        if action['before_sha256']:
            if not target.exists() or digest(target) != action['before_sha256']:
                raise ValueError('install destination drifted after preflight')
            self._backup(target)
        else:
            if target.exists():
                raise ValueError('install destination appeared after preflight')
            self._mark_delete(target)
            self._record_generated_file(target)
        temporary = self.bak_dir / '.qa-stage' / str(index)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        origin = action['origin']
        source_root = self.qa_assets if origin == 'package' else self.game_dir
        source = safe_path(source_root, action['source'])
        if origin == 'patched-game':
            data = patched_ff(source, profile['files'][action['source']], action['payloads'], self.qa_assets)
            temporary.write_bytes(data)
        else:
            expected_source = action['sha256']
            if digest(source) != expected_source:
                raise ValueError('install source drifted after preflight')
            shutil.copyfile(source, temporary)
        if digest(temporary) != action['sha256']:
            raise ValueError('staged output hash differs from dry-run plan')
        os.replace(temporary, target)
        if digest(target) != action['sha256']:
            raise ValueError('installed output hash mismatch')

    def _clean_backups(self):
        expected = self.game_dir / '.cod4cn_bak'
        if self.bak_dir != expected or not self.bak_dir.resolve().is_relative_to(self.game_dir) or self.bak_dir.is_symlink():
            raise ValueError('unsafe backup cleanup path')
        shutil.rmtree(self.bak_dir)

    def _verify_restored(self, journal: dict):
        self._verify_baseline(journal['profile'])
        for action in journal['actions']:
            if action['before_sha256'] is None and safe_path(self.game_dir, action['target']).exists():
                raise ValueError(f'generated file remains after rollback: {action["target"]}')
        for relative in reversed(journal['created_dirs']):
            path = safe_path(self.game_dir, relative)
            if path.exists():
                path.rmdir()  # Only directories created by this transaction, and only if empty.

    def install_qa(self, dry_run=False, plan_out: Path | None = None) -> bool:
        try:
            journal = self.plan()
            if plan_out:
                if plan_out.resolve().is_relative_to(self.game_dir) or plan_out.resolve().is_relative_to(self.qa_assets):
                    raise ValueError('plan output must be outside the game and QA package directories')
                write_json(plan_out, journal)
            print(f"驗證通過：{journal['game_profile']}；{len(journal['actions'])} 個單機安裝步驟")
            if dry_run:
                print('dry-run 完成；遊戲檔案未變更。')
                return True
            self.bak_dir.mkdir(exist_ok=False)
            journal['status'] = 'installing'
            write_json(self.journal_path, journal)
            try:
                for index, action in enumerate(journal['actions']):
                    self._record_dirs(safe_path(self.game_dir, action['target']).parent, journal)
                    journal['attempted'].append(index)
                    write_json(self.journal_path, journal)
                    self._apply(action, journal['profile'], index)
                journal['status'] = 'installed'
                write_json(self.journal_path, journal)
                print('繁體中文（台灣）單機 QA 已安裝。請依 README-QA.md 驗收；備份已保留。')
                return True
            except Exception:
                # The persisted journal also covers failure between backup and write.
                # Reuse uninstall's full preflight before any rollback mutation.
                if not self.uninstall():
                    print('自動回復未完成；保留 .cod4cn_bak，請勿刪除。')
                raise
        except Exception as exc:
            print(f'QA 安裝停止：{exc}')
            return False

    def uninstall(self) -> bool:
        try:
            journal = read_json(self.journal_path)
            if journal.get('schema') != 1 or journal.get('locale') != 'zh-TW':
                raise ValueError('unsupported QA transaction journal')
            log = []
            # Preflight every backup and active path before touching any file.
            for index in journal['attempted']:
                action = journal['actions'][index]
                target = safe_path(self.game_dir, action['target'])
                if action['kind'] == 'rename':
                    source = safe_path(self.game_dir, action['source'])
                    backup = safe_path(self.bak_dir, action['source'])
                    if not backup.exists():
                        if source.exists() and digest(source) == action['sha256'] and not target.exists():
                            continue  # Interrupted before backup/mutation.
                        raise ValueError('rename backup missing')
                    if digest(backup) != action['sha256']:
                        raise ValueError('rename backup changed')
                    if target.exists() and digest(target) != action['sha256']:
                        raise ValueError('disabled IWD changed after installation')
                    if source.exists() and digest(source) != action['sha256']:
                        raise ValueError('localized source reappeared with different content')
                    log.extend([('restore', backup, source), ('rename', target, source)])
                elif action['before_sha256']:
                    backup = safe_path(self.bak_dir, action['target'])
                    if not backup.exists():
                        if target.exists() and digest(target) == action['before_sha256']:
                            continue
                        raise ValueError('original backup missing')
                    if digest(backup) != action['before_sha256']:
                        raise ValueError('original backup changed')
                    if target.exists() and digest(target) not in (action['sha256'], action['before_sha256']):
                        raise ValueError(f'安裝後檔案已被修改，保留備份並停止：{action["target"]}')
                    log.append(('restore', backup, target))
                else:
                    if target.exists() and digest(target) != action['sha256']:
                        raise ValueError(f'新增檔案已被修改，保留備份並停止：{action["target"]}')
                    log.append(('delete', target))
            self._rollback_log = log
            self._rollback()
            self._verify_restored(journal)
            self._clean_backups()
            print('QA 已完整還原；原始檔案 SHA-256 全數一致。')
            return True
        except Exception as exc:
            print(f'QA 還原停止，備份保留：{exc}')
            return False

    def status(self) -> bool:
        if self.journal_path.is_file():
            state = read_json(self.journal_path)
            print(f"zh-TW 單機 QA：{state.get('status')}；備份：{self.bak_dir}")
            return True
        print('未安裝 zh-TW 單機 QA。')
        return False
