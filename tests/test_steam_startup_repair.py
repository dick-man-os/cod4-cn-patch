import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "steam_startup_repair.py"
spec = importlib.util.spec_from_file_location("steam_startup_repair", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
import sys
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

FIXTURE = r'''"InstallScript"
{
    "Run Process"
    {
        "DirectX"
        {
            "process 1" "%INSTALLDIR%\\DirectX\\DXSETUP.exe"
            "command 1" "/silent"
        }
        "PunkBuster Anti-Cheat"
        {
            "process 1" "%INSTALLDIR%\\PB\\pbsvc.exe"
            "command 1" "-i --no-prompts"
        }
    }
}
'''


class SteamStartupRepairTests(unittest.TestCase):
    def test_detects_only_punkbuster_named_block(self):
        blocks = mod.find_punkbuster_blocks(FIXTURE)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].label, "PunkBuster Anti-Cheat")

    def test_disable_and_restore_are_reversible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = root / "installscript.vdf"
            script.write_text(FIXTURE, encoding="utf-8")

            self.assertEqual(mod.disable_punkbuster(root), 0)
            edited = script.read_text(encoding="utf-8-sig")
            self.assertNotIn("PunkBuster Anti-Cheat", edited)
            self.assertIn("DirectX", edited)
            self.assertTrue((root / mod.BACKUP_DIR / "installscript.vdf").exists())

            self.assertEqual(mod.restore(root), 0)
            restored = script.read_text(encoding="utf-8-sig")
            self.assertIn("PunkBuster Anti-Cheat", restored)
            self.assertIn("DirectX", restored)

    def test_ambiguous_multiple_blocks_fail_closed(self):
        text = FIXTURE.replace(
            '        "PunkBuster Anti-Cheat"',
            '        "PunkBuster Anti-Cheat"',
        )
        second = r'''
"PunkBuster Legacy"
{
    "process 1" "PB\\pbsetup.exe"
}
'''
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "installscript.vdf").write_text(text + second, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                mod.disable_punkbuster(root)


if __name__ == "__main__":
    unittest.main()
