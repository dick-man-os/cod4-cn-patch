import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import zh_tw_text_build as text


def document(values):
    result = b'VERSION "1"\r\nCONFIG "C:/test.cfg"\r\nFILENOTES ""\r\n// preserved\r\n'
    for key, value in values:
        result += b'REFERENCE ' + key.encode() + b'\r\nLANG_ENGLISH "' + text.escape(value.encode('gbk')) + b'"\r\n\r\n'
    return result + b'ENDMARKER\r\n'


class TextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cc = text.get_opencc('s2tw')

    def test_str_keys_syntax_tokens_and_byte_escapes(self):
        value = '读取游戏 ^1&&1 %02d [{+attack}]\n"测试"\\n'
        original = document([('MENU_LOAD', value), ('MENU_BACK', '返回')])
        converted, report, _ = text.convert_str(original, {'读取': '載入'}, self.cc)
        entries = text.parse_str(converted)
        self.assertEqual([e.key for e in entries], ['MENU_LOAD', 'MENU_BACK'])
        self.assertIn('載入遊戲', entries[0].text)
        self.assertEqual(text.tokens(value), text.tokens(entries[0].text))
        self.assertEqual(report['keys'], 2)
        # Replace each value with a marker: every other byte must remain identical.
        def structure(data):
            for entry in reversed(text.parse_str(data)):
                data = data[:entry.start] + b'VALUE' + data[entry.end:]
            return data
        self.assertEqual(structure(original), structure(converted))
        self.assertEqual(converted, text.convert_str(original, {'读取': '載入'}, self.cc)[0])

    def test_gbk_trail_backslash_is_escaped_as_a_byte(self):
        char = next(chr(i) for i in range(0x4e00, 0x9fff)
                    if self.encodable(chr(i)) and chr(i).encode('gbk')[-1] == 92)
        data = document([('KEY', char)])
        self.assertIn(char.encode('gbk')[:1] + b'\\\\', data)
        self.assertEqual(text.parse_str(data)[0].text, char)

    @staticmethod
    def encodable(char):
        try:
            char.encode('gbk')
            return True
        except UnicodeEncodeError:
            return False

    def test_ui_can_expand_and_has_no_length_constraint(self):
        self.assertEqual(text.convert_ui('视频设置', {'视频': '影像', '设置': '設定選項'}, self.cc), '影像設定選項')
        self.assertEqual(text.convert_ui('游戏', {}, self.cc), '遊戲')

    def test_str_rejects_duplicates_unknown_grammar_and_missing_end(self):
        samples = [document([('KEY', '游戏'), ('KEY', '设置')]),
                   document([('KEY', '游戏')]).replace(b'LANG_ENGLISH', b'LANG_OTHER'),
                   document([('KEY', '游戏')]).replace(b'ENDMARKER', b''),
                   document([('KEY', '游戏')]).replace(b'ENDMARKER', b'REFERENCE OTHER\nENDMARKER')]
        for data in samples:
            with self.subTest(data=data), self.assertRaises(ValueError):
                text.parse_str(data)

    def test_payload_spans_fail_closed(self):
        data = b'ID\0' + '游戏'.encode('gbk') + b'\0END'
        item = {'start': 3, 'end': 7, 'original_hex': data[3:7].hex(), 'zh_tw': '遊戲'}
        output = text.apply_spans(data, [item])
        self.assertEqual(len(data), len(output))
        self.assertEqual(output[:3] + output[7:], data[:3] + data[7:])
        self.assertEqual(output[3:7].decode('gbk'), '遊戲')
        for entries in ([dict(item, original_hex='00000000')], [dict(item, zh_tw='載入遊戲')],
                        [item, item], [dict(item, end=999)], [dict(item, zh_tw='🙂🙂')]):
            with self.subTest(entries=entries), self.assertRaises((ValueError, UnicodeEncodeError)):
                text.apply_spans(data, entries)

    def test_tokens_and_semantic_hazards_fail_closed(self):
        data = '游戏 %s'.encode('gbk')
        with self.assertRaisesRegex(ValueError, 'token'):
            text.apply_spans(data, [{'start': 0, 'end': len(data), 'original_hex': data.hex(), 'zh_tw': '遊戲 %d'}])
        with self.assertRaisesRegex(ValueError, 'hazard'):
            text.convert_ui('士兵', {'士兵': '計程車兵'}, self.cc)


if __name__ == '__main__':
    unittest.main()
