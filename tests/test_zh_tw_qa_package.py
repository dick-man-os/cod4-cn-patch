import json
import os
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from zh_tw_qa_install import verify_package
from zh_tw_text_build import apply_spans, parse_str, sha


@unittest.skipUnless(os.environ.get('ZH_TW_QA_PACKAGE'), 'integrated package path is supplied by CI')
class IntegratedPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = Path(os.environ['ZH_TW_QA_PACKAGE'])
        cls.repo = Path(__file__).resolve().parents[1]
        cls.manifest = verify_package(cls.package)

    def test_text_manifest_payloads_and_localize_values(self):
        report = json.loads((self.package / 'manifests/text-build.json').read_text(encoding='utf-8'))
        self.assertEqual(report['detected_entries'], 2487)
        self.assertEqual(report['installed_text_spans'], 2486)
        self.assertEqual(len(report['payloads']), 143)
        self.assertEqual(len(report['excluded']), 3)
        self.assertTrue(all(not e['approved'] for e in report['entries']))
        for payload in report['payloads']:
            source = (self.repo / 'patches/zone/chinese' / payload['file']).read_bytes()
            output = (self.package / 'zone/chinese' / payload['file']).read_bytes()
            self.assertEqual(len(source), len(output))
            self.assertEqual(sha(source), payload['source_sha256'])
            entries = [e for e in report['entries'] if e['file'] == payload['file']]
            self.assertEqual(apply_spans(source, entries), output)
        values = parse_str((self.package / 'manifests/code_post_gfx.zh-TW.str').read_bytes())
        self.assertEqual(len(values), 1797)
        self.assertEqual({e.key: e.text for e in values}, json.loads((self.package / 'manifests/ui-values.json').read_text(encoding='utf-8')))
        build = json.loads((self.package / 'manifests/build-report.json').read_text(encoding='utf-8'))
        self.assertTrue(build['localize_roundtrip']['all_values_equal'])
        self.assertEqual(build['localize_roundtrip']['keys'], 1797)
        self.assertTrue(build['roundtrip_pixels']['pass'])
        for font in build['roundtrip_pixels']['fonts'].values():
            self.assertEqual(font['drawable'], 1518)
            self.assertEqual(font['missing'] + font['blank'] + font['invalid'], '')

    def test_iwd_retains_unaffected_members_and_notices(self):
        with zipfile.ZipFile(self.repo / 'patches/main/localized_chinese_iw15.iwd') as old, \
                zipfile.ZipFile(self.package / 'main/localized_chinese_iw15.iwd') as new:
            self.assertEqual(old.namelist(), new.namelist())
            targets = {'images/gamefonts_pc_normal.iwi', 'images/gamefonts_pc_small.iwi', 'images/gamefonts_pc_extrabig.iwi'}
            for name in old.namelist():
                if name not in targets:
                    self.assertEqual(old.read(name), new.read(name), name)
        self.assertEqual((self.package / 'NOTICE').read_bytes(), (self.repo / 'NOTICE').read_bytes())
        for filename in ('Noto-CJK-OFL.txt', 'Noto-CJK-NOTICE.txt'):
            self.assertEqual((self.package / 'licenses' / filename).read_bytes(), (self.repo / 'licenses' / filename).read_bytes())


if __name__ == '__main__':
    unittest.main()
