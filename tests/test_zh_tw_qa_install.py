import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib
import cod4_cn_patch

from tools.zh_tw_qa_install import (COD4ZHTWQAPatch, byte_digest, digest,
                                    patched_ff, safe_path, verify_package, write_json)


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def fixture(root):
    game, package = root / 'game', root / 'package'
    raw = b'0123456789abcdef'
    header = b'IWffu100' + (5).to_bytes(4, 'little')
    ff = header + zlib.compress(raw)
    files = {'iw3sp.exe': b'fake exe', 'localization.txt': b'english\n',
             'main/iw_00.iwd': b'base', 'main/localized_english_iw00.iwd': b'english',
             'main/localized_german_iw00.iwd': b'german',
             'zone/english/code_post_gfx.ff': ff, 'zone/english/mission.ff': ff,
             'zone/english/ui.ff': ff}
    records = {}
    for name, data in files.items():
        put(game, name, data)
        records[name] = {'size': len(data), 'sha256': byte_digest(data)}
        if name.endswith('.ff'):
            records[name].update(header_hex=header.hex(), decompressed_size=len(raw),
                                 decompressed_sha256=byte_digest(raw))
    # Files outside the SP inventory must survive installation and uninstall.
    put(game, 'zone/english/mp_test.ff', b'MP')
    put(game, 'profiles/player/config.cfg', b'personal settings')
    name = 'mission.ff.dump.4.bin'
    span = {'ff_name': 'mission.ff', 'offset': 4, 'size': 4, 'preimage_sha256': byte_digest(raw[4:8])}
    profile = {'id': 'fixture-profile', 'files': records, 'mission_spans': {name: span}}
    write_json(package / 'manifests/source-profile.json', profile)
    put(package, 'localization.txt', b'chinese\n')
    put(package, 'zone/english/code_post_gfx.ff', header + zlib.compress(b'new font/UI'))
    put(package, 'main/localized_chinese_iw15.iwd', b'new atlas')
    put(package, 'zone/chinese/' + name, b'ABCD')
    manifest = {'schema': 1, 'locale': 'zh-TW', 'scope': 'single-player',
                'status': 'experimental-local-QA', 'game_profile': profile['id'],
                'payloads': [dict(span, file=name, sha256=byte_digest(b'ABCD'))]}
    seal(package, manifest)
    return game, package, profile


def seal(package, manifest):
    manifest['files'] = {p.relative_to(package).as_posix(): {'size': p.stat().st_size, 'sha256': digest(p)}
                         for p in package.rglob('*') if p.is_file()
                         and p.relative_to(package).as_posix() not in ('manifests/package.json', 'SHA256SUMS')}
    write_json(package / 'manifests/package.json', manifest)
    names = sorted(set(manifest['files']) | {'manifests/package.json'})
    (package / 'SHA256SUMS').write_text(''.join(f'{digest(package / name)}  {name}\n' for name in names), encoding='utf-8')


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game, self.package, self.profile = fixture(Path(self.temp.name))
        self.before = snapshot(self.game)
        self.patcher = COD4ZHTWQAPatch(self.game, self.package)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def test_dry_run_and_complete_uninstall(self):
        self.assertTrue(self.patcher.install_qa(dry_run=True))
        self.assertEqual(snapshot(self.game), self.before)
        self.assertFalse(self.patcher.bak_dir.exists())
        self.assertTrue(self.patcher.install_qa())
        self.assertEqual(zlib.decompress((self.game / 'zone/english/mission.ff').read_bytes()[12:]), b'0123ABCD89abcdef')
        self.assertEqual((self.game / 'zone/english/mp_test.ff').read_bytes(), b'MP')
        self.assertTrue(COD4ZHTWQAPatch(self.game).uninstall())
        self.assertEqual(snapshot(self.game), self.before)
        self.assertFalse((self.game / 'zone/chinese').exists())

    def test_failure_after_each_action_restores_every_byte(self):
        count = len(self.patcher.plan()['actions'])
        original = COD4ZHTWQAPatch._apply
        for fail_index in range(count):
            def fail(patcher, action, profile, index):
                original(patcher, action, profile, index)
                if index == fail_index:
                    raise OSError('injected write failure')
            with self.subTest(action=fail_index), patch.object(COD4ZHTWQAPatch, '_apply', fail):
                self.assertFalse(COD4ZHTWQAPatch(self.game, self.package).install_qa())
                self.assertEqual(snapshot(self.game), self.before)

    def test_process_interruption_is_recoverable_from_journal(self):
        original = COD4ZHTWQAPatch._apply
        def interrupt(patcher, action, profile, index):
            original(patcher, action, profile, index)
            if action['kind'] == 'rename':
                raise KeyboardInterrupt()
        with patch.object(COD4ZHTWQAPatch, '_apply', interrupt), self.assertRaises(KeyboardInterrupt):
            self.patcher.install_qa()
        self.assertTrue(self.patcher.journal_path.exists())
        self.assertTrue(COD4ZHTWQAPatch(self.game).uninstall())
        self.assertEqual(snapshot(self.game), self.before)

    def test_source_drift_refused_before_mutation(self):
        put(self.game, 'iw3sp.exe', b'wrong version')
        before = snapshot(self.game)
        self.assertFalse(self.patcher.install_qa())
        self.assertEqual(snapshot(self.game), before)
        self.assertFalse(self.patcher.bak_dir.exists())

    def test_package_tampering_and_unlisted_files_refused(self):
        put(self.package, 'zone/chinese/mission.ff.dump.4.bin', b'EVIL')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            verify_package(self.package)
        self.assertFalse(self.patcher.install_qa())
        self.assertEqual(snapshot(self.game), self.before)
        put(self.package, 'extra.txt', b'new')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            verify_package(self.package)

    def test_changed_installed_file_or_backup_blocks_uninstall(self):
        self.assertTrue(self.patcher.install_qa())
        put(self.game, 'zone/english/mission.ff', b'changed by another tool')
        before = snapshot(self.game)
        self.assertFalse(COD4ZHTWQAPatch(self.game).uninstall())
        self.assertEqual(snapshot(self.game), before)

    def test_ambiguous_mission_ranges_and_preimage_are_rejected(self):
        manifest = verify_package(self.package)
        payload = manifest['payloads'][0]
        source = self.game / 'zone/english/mission.ff'
        record = self.profile['files']['zone/english/mission.ff']
        for payloads in ([payload, payload], [dict(payload, offset=99)],
                         [dict(payload, preimage_sha256='0' * 64)], [dict(payload, size=5)]):
            with self.subTest(payloads=payloads), self.assertRaises(ValueError):
                patched_ff(source, record, payloads, self.package)

    def test_safe_paths_and_existing_backup(self):
        for path in ('../other', '/absolute', 'C:/other', 'main\\outside', 'main//file', 'main/./file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(self.game, path)
        self.patcher.bak_dir.mkdir()
        with self.assertRaises(ValueError):
            self.patcher.plan()

    def test_incomplete_or_mp_manifest_is_rejected(self):
        manifest = verify_package(self.package)
        manifest['payloads'] = []
        seal(self.package, manifest)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.patcher.plan()

    def test_default_cli_still_selects_original_zh_cn_installer(self):
        with patch('sys.argv', ['cod4_cn_patch.py', 'install', '--game-dir', str(self.game)]), \
                patch.object(cod4_cn_patch.COD4CNPatch, 'verify_game_dir', return_value=True), \
                patch.object(cod4_cn_patch.COD4CNPatch, 'install', return_value=True) as install, \
                self.assertRaises(SystemExit) as outcome:
            cod4_cn_patch.main()
        self.assertEqual(outcome.exception.code, 0)
        install.assert_called_once()

    def test_uninstall_cli_detects_persisted_qa_journal(self):
        self.assertTrue(self.patcher.install_qa())
        with patch('sys.argv', ['cod4_cn_patch.py', 'uninstall', '--game-dir', str(self.game)]), \
                self.assertRaises(SystemExit) as outcome:
            cod4_cn_patch.main()
        self.assertEqual(outcome.exception.code, 0)
        self.assertEqual(snapshot(self.game), self.before)


if __name__ == '__main__':
    unittest.main()
