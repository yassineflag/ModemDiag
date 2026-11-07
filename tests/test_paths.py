from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modemdiag.core import paths
from tests import ROOT


class PathLayoutTests(unittest.TestCase):
    def test_source_resources_live_under_resources(self):
        with patch.object(paths.sys, "frozen", False, create=True):
            self.assertEqual(paths.get_data_dir(), ROOT / "resources")
            self.assertEqual(paths.commands_dir(), ROOT / "resources/commands")
            self.assertTrue((paths.commands_dir() / "3gpp.yaml").is_file())
            self.assertTrue(paths.diagnostics_dir().is_dir())
            self.assertTrue((paths.assets_dir() / "icon.png").is_file())

    def test_source_settings_are_inside_the_ignored_local_folder(self):
        with patch.object(paths.sys, "frozen", False, create=True):
            self.assertEqual(paths.get_user_dir(), ROOT / "local-ignored/runtime")
            self.assertEqual(paths.user_commands_file(), ROOT / "local-ignored/runtime/user_commands.yaml")
            self.assertEqual(paths.favorites_file(), ROOT / "local-ignored/runtime/quick_send.yaml")

    def test_legacy_data_migrates_without_overwriting_current_settings(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(paths, "get_project_dir", return_value=Path(temporary)), \
                patch.object(paths.sys, "frozen", False, create=True):
            root = Path(temporary)
            current = root / "local-ignored/runtime"
            current.mkdir(parents=True)
            (current / "user_commands.yaml").write_text("current commands")
            (root / "user_commands.yaml").write_text("legacy commands to preserve")
            (root / "quick_send.yaml").write_text("personal favourites")
            (root / "macros").mkdir()
            (root / "macros/custom.yaml").write_text("personal workflow")
            (root / "logs").mkdir()
            (root / "logs/session.log").write_text("personal session")
            self.assertEqual(paths.get_user_dir(), current)
            self.assertEqual((current / "user_commands.yaml").read_text(), "current commands")
            self.assertEqual((root / "user_commands.yaml").read_text(), "legacy commands to preserve")
            self.assertEqual((current / "quick_send.yaml").read_text(), "personal favourites")
            self.assertEqual((current / "macros/custom.yaml").read_text(), "personal workflow")
            self.assertEqual((current / "logs/session.log").read_text(), "personal session")
            self.assertFalse((root / "quick_send.yaml").exists())
            self.assertFalse((root / "logs").exists())
            self.assertFalse((root / "macros").exists())

    def test_seeding_example_does_not_overwrite_a_user_macro(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(paths, "get_project_dir", return_value=Path(temporary)), \
                patch.object(paths.sys, "frozen", False, create=True):
            root = Path(temporary)
            bundled = root / "resources/macros"
            bundled.mkdir(parents=True)
            (bundled / "example.yaml").write_text("bundled example")
            macros = paths.macros_dir()
            self.assertEqual((macros / "example.yaml").read_text(), "bundled example")
            (macros / "example.yaml").write_text("user version")
            paths.macros_dir()
            self.assertEqual((macros / "example.yaml").read_text(), "user version")

    def test_packaged_resources_use_the_extracted_bundle(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(paths.sys, "frozen", True, create=True), \
                patch.object(paths.sys, "_MEIPASS", temporary, create=True):
            self.assertEqual(paths.commands_dir(), Path(temporary) / "commands")
            self.assertEqual(paths.diagnostics_dir(), Path(temporary) / "diagnostics")
            self.assertEqual(paths.assets_dir(), Path(temporary) / "assets")

    def test_packaged_settings_remain_in_the_os_user_directory(self):
        for platform, variable in (("linux", "XDG_DATA_HOME"), ("win32", "APPDATA")):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary, \
                    patch.object(paths.sys, "frozen", True, create=True), \
                    patch.object(paths.sys, "platform", platform), \
                    patch.dict(paths.os.environ, {variable: temporary}):
                self.assertEqual(paths.get_user_dir(), Path(temporary) / "ModemDiag")
                self.assertTrue(paths.get_user_dir().is_dir())
