import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from modemdiag.core import config_transfer as ct


class ConfigurationTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "macros").mkdir()
        (self.source / "user_commands.yaml").write_text(
            'added: [{name: Signal, command: AT+CSQ, function: Network}]\nremoved: []\nvendors: [Custom]\n')
        (self.source / "quick_send.yaml").write_text('- {label: Signal, command: AT+CSQ}\n')
        (self.source / "appearance.json").write_text('{"theme":"light"}')
        (self.source / "ai-settings.json").write_text(
            '{"provider":"compatible","model":"example","base_url":"http://localhost:1234/v1","api_key":"secret"}')
        (self.source / "macros/check.yaml").write_text('name: Check\nsteps: [{command: AT}]\n')
        self.archive = self.root / "settings.zip"

    def _archive_with(self, files, version=1):
        with ZipFile(self.archive, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"format": "modemdiag-configuration", "version": version}))
            for name, raw in files.items():
                archive.writestr(name, raw)
        return self.archive

    def test_round_trip_between_pcs_keeps_other_files_and_creates_restorable_backup(self):
        ct.export_configuration(self.source, self.archive)
        files = ct.read_archive(self.archive)
        target = self.root / "other-pc"
        (target / "macros").mkdir(parents=True)
        (target / "appearance.json").write_text('{"theme":"dark"}')
        (target / "macros/local.yaml").write_text('name: Local\nsteps: []\n')
        backup = ct.import_configuration(target, files)
        for name, raw in files.items():
            self.assertEqual((target / name).read_bytes(), raw)
        self.assertTrue((target / "macros/local.yaml").exists())
        old = ct.read_archive(backup)
        self.assertEqual(json.loads(old["appearance.json"])["theme"], "dark")
        self.assertNotIn("user_commands.yaml", old)
        ct.import_configuration(target, old)
        self.assertEqual(json.loads((target / "appearance.json").read_text())["theme"], "dark")

    def test_export_excludes_logs_credentials_chats_and_release_policy(self):
        for name in ("release-policy-cache.json", "chat.json", "session.log", "config.yaml"):
            (self.source / name).write_text("must not be exported")
        (self.source / "logs").mkdir()
        (self.source / "logs/session.log").write_text("private traffic")
        ct.export_configuration(self.source, self.archive)
        files = ct.read_archive(self.archive)
        self.assertEqual(set(files), ct.SETTINGS | {"macros/check.yaml"})
        self.assertNotIn("secret", files["ai-settings.json"].decode())

    def test_rejects_paths_and_unrelated_files(self):
        for name in ("../outside.yaml", "/tmp/outside.yaml", "macros/../outside.yaml",
                     "macros/a\\b.yaml", "macros/C:bad.yaml", "macros/CON.yaml",
                     "release-policy-cache.json", "logs/session.log"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                ct.read_archive(self._archive_with({name: "name: Test\nsteps: []"}))

    def test_rejects_bad_shapes_recursive_yaml_and_future_versions(self):
        for name, raw in (("user_commands.yaml", "added: [oops]"),
                          ("user_commands.yaml", "order: {Network: null}"),
                          ("quick_send.yaml", "not a list"),
                          ("appearance.json", '{"theme":"invalid"}'),
                          ("macros/bad.yaml", 'name: Bad\nsteps: [{command: AT, timeout_ms: never}]'),
                          ("macros/bad.yaml", 'name: Bad\nsteps: [{command: AT, description: []}]'),
                          ("macros/bad.yaml", 'name: Bad\nrecursive: &x [*x]'),
                          ("ai-settings.json", '{"api_key":"secret"}')):
            with self.subTest(name=name, raw=raw), self.assertRaises(ValueError):
                ct.read_archive(self._archive_with({name: raw}))
        with self.assertRaises(ValueError):
            ct.read_archive(self._archive_with({}, version=2))

    def test_case_collisions_are_not_portable(self):
        with self.assertRaises(ValueError):
            ct.read_archive(self._archive_with({"macros/a.yaml": "name: A", "macros/A.yaml": "name: B"}))

    def test_limits_are_checked_before_import(self):
        archive = self._archive_with({"appearance.json": '{"theme":"light"}'})
        with patch.object(ct, "MAX_FILE", 1), self.assertRaises(ValueError):
            ct.read_archive(archive)

    def test_symlinks_are_not_exported_or_replaced(self):
        outside = self.root / "outside.yaml"
        outside.write_text("name: Outside\nsteps: []")
        link = self.source / "macros/link.yaml"
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            ct.export_configuration(self.source, self.archive)
        with self.assertRaises(ValueError):
            ct.import_configuration(self.source, {"macros/link.yaml": b"name: Changed\nsteps: []"})
        self.assertEqual(outside.read_text(), "name: Outside\nsteps: []")

    def test_write_failure_rolls_back_completed_files_and_keeps_backup(self):
        ct.export_configuration(self.source, self.archive)
        files = {"appearance.json": b'{"theme":"dark"}', "quick_send.yaml": b"[]"}
        before = {name: (self.source / name).read_bytes() for name in files}
        replace = ct.os.replace
        def fail_on_second(source, destination):
            if Path(destination) == self.source / "quick_send.yaml":
                raise OSError("disk full")
            return replace(source, destination)
        with patch.object(ct.os, "replace", side_effect=fail_on_second), self.assertRaises(OSError):
            ct.import_configuration(self.source, files)
        self.assertEqual({name: (self.source / name).read_bytes() for name in files}, before)
        self.assertEqual(len(list((self.source / "backups").glob("*.zip"))), 1)

    def test_failed_export_keeps_existing_archive(self):
        self.archive.write_bytes(b"existing backup")
        with patch.object(ct.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            ct.export_configuration(self.source, self.archive)
        self.assertEqual(self.archive.read_bytes(), b"existing backup")

    def test_bundled_examples_can_all_be_transferred(self):
        import shutil
        bundled = Path(__file__).resolve().parents[1] / "resources"
        for directory in ("macros", "diagnostics"):
            for file in (bundled / directory).glob("*.yaml"):
                shutil.copy2(file, self.source / "macros" / file.name)
        ct.export_configuration(self.source, self.archive)
        self.assertGreater(len(ct.read_archive(self.archive)), 10)
