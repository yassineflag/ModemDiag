import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modemdiag.core.appearance import load_theme, save_theme


class AppearanceTests(unittest.TestCase):
    def test_missing_or_invalid_preferences_use_dark(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "appearance.json"
            self.assertEqual(load_theme(path), "dark")
            for content in ('broken', '[]', '{"theme": "unknown"}'):
                path.write_text(content)
                self.assertEqual(load_theme(path), "dark")

    def test_saving_preferences_keeps_other_user_files_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = root / "user_commands.yaml"
            commands.write_text("my saved commands")
            path = root / "appearance.json"
            save_theme(path, "light")
            self.assertEqual(json.loads(path.read_text()), {"theme": "light"})
            self.assertEqual(commands.read_text(), "my saved commands")

    def test_failed_save_preserves_the_previous_preference(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "appearance.json"
            save_theme(path, "dark")
            with patch("modemdiag.core.appearance.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    save_theme(path, "light")
            self.assertEqual(load_theme(path), "dark")
            self.assertEqual(list(Path(temporary).iterdir()), [path])
