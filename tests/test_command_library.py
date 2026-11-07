"""Regression checks use temporary config files and never touch user settings."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

from modemdiag.ui import command_panel as cp

APP = QApplication.instance() or QApplication([])


class CommandLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user_path = self.root / "user_commands.yaml"
        self.fav_path = self.root / "quick_send.yaml"
        for name, value in (("USER_FILE", str(self.user_path)),
                            ("FAVORITES_FILE", str(self.fav_path))):
            patcher = patch.object(cp, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.panel = cp.CommandPanel()
        self.addCleanup(self.panel.deleteLater)

    def edit(self, original, function, **updates):
        result = {key: original.get(key, "") for key in ("name", "command", "description")}
        result.update(updates)
        dialog = Mock()
        dialog.exec.return_value = True
        dialog.get_result.return_value = result
        with patch.object(cp, "_CmdDialog", return_value=dialog):
            self.panel._edit_cmd(original, function)

    def test_rename_and_description_keep_command_and_parameter_schema(self):
        command = next(c for c in self.panel._commands_for_function("Network") if c.get("params"))
        self.edit(command, "Network", name="My command", description="My description")
        edited = next(c for c in self.panel._commands_for_function("Network") if c["name"] == "My command")
        for key in ("command", "params", "get", "caps"):
            self.assertEqual(edited.get(key), command.get(key))
        self.assertEqual(edited["description"], "My description")
        self.assertEqual(edited["_src"], "user")
        self.assertEqual(sum(c["command"] == command["command"]
                             for c in self.panel._commands_for_function("Network")), 1)
        self.edit(edited, "Network", name="Second name")
        self.assertEqual(len(self.panel._user["added"]), 1)
        reloaded = cp.CommandPanel()
        self.addCleanup(reloaded.deleteLater)
        saved = next(c for c in reloaded._commands_for_function("Network") if c["name"] == "Second name")
        self.assertEqual(saved["params"], command["params"])

    def test_plain_command_button_displays_edited_name_and_description(self):
        command = self.panel._commands_for_function("Identity")[0]
        self.edit(command, "Identity", name="Module identity", description="Read module identity")
        texts = [label.text() for label in self.panel._fn_to_page["Identity"].findChildren(QLabel)]
        self.assertIn("Module identity", texts)
        self.assertIn("Read module identity", texts)

    def test_runtime_undo_redo_and_new_edit_discards_redo(self):
        command = self.panel._commands_for_function("Identity")[0]
        self.edit(command, "Identity", name="Edited")
        self.assertTrue(self.panel.undo_action.isEnabled())
        self.panel.undo()
        self.assertEqual(self.panel._commands_for_function("Identity")[0]["name"], command["name"])
        self.assertTrue(self.panel.redo_action.isEnabled())
        self.panel.redo()
        self.assertIn("Edited", [c["name"] for c in self.panel._commands_for_function("Identity")])
        self.panel.undo()
        self.edit(command, "Identity", name="Different")
        self.assertFalse(self.panel.redo_action.isEnabled())

    def test_changing_command_then_undo_restores_original(self):
        command = self.panel._commands_for_function("Identity")[0]
        self.edit(command, "Identity", command="AT+CGMI", name="Changed")
        self.assertFalse(any(c["command"] == command["command"]
                             for c in self.panel._commands_for_function("Identity")))
        self.panel.undo()
        self.assertTrue(any(c["command"] == command["command"]
                            for c in self.panel._commands_for_function("Identity")))

    def test_vendor_edit_and_removal_are_scoped(self):
        common = {"name": "Shared", "command": "AT+SHARED", "_src": "vendor"}
        for name in ("Fibocom", "Quectel"):
            self.panel._vendor_cache[name] = ({"Network": [common]}, "#e3b341")
        self.panel._on_mfr("Fibocom")
        self.edit(common, "Network", name="Fibocom edit")
        self.assertIn("Fibocom edit", [c["name"] for c in self.panel._commands_for_function("Network")])
        self.panel._on_mfr("Quectel")
        names = [c["name"] for c in self.panel._commands_for_function("Network")]
        self.assertIn("Shared", names)
        self.assertNotIn("Fibocom edit", names)

    def test_add_vendor_persists_and_undo_removes_it(self):
        with patch.object(cp.QInputDialog, "getText", return_value=("My Vendor", True)):
            self.panel._add_vendor()
        self.assertEqual(self.panel._current_mfr, "My Vendor")
        dialog = Mock()
        dialog.exec.return_value = True
        dialog.get_result.return_value = {"name": "Mine", "command": "AT+MINE", "description": "Custom"}
        with patch.object(cp, "_CmdDialog", return_value=dialog):
            self.panel._add_user_cmd("Identity")
        self.assertEqual(self.panel._user["added"][0]["manufacturer"], "My Vendor")
        self.panel._on_mfr("Fibocom")
        self.assertNotIn("Mine", [c["name"] for c in self.panel._commands_for_function("Identity")])
        reloaded = cp.CommandPanel()
        self.addCleanup(reloaded.deleteLater)
        self.assertGreaterEqual(reloaded._mfr_combo.findText("My Vendor"), 0)
        self.panel.undo()
        self.panel.undo()
        self.assertEqual(self.panel._mfr_combo.findText("My Vendor"), -1)
        self.panel.redo()
        self.assertGreaterEqual(self.panel._mfr_combo.findText("My Vendor"), 0)

    def test_duplicate_vendor_rejected_case_insensitively(self):
        with patch.object(cp.QInputDialog, "getText", return_value=("  fibocom  ", True)), \
                patch.object(cp.QMessageBox, "warning") as warning:
            self.panel._add_vendor()
        warning.assert_called_once()
        self.assertNotIn("vendors", self.panel._user)

    def test_parameterized_favorite_keeps_actions_and_can_be_undone(self):
        command = next(c for c in self.panel._commands_for_function("Network") if c.get("params"))
        original = cp._load_favorites()
        self.panel._add_cmd_to_favorites(command)
        favorite = cp._load_favorites()[-1]
        self.assertEqual(favorite["params"], command["params"])
        buttons = self.panel._pages[0].findChildren(cp._CmdButton)
        sent = []
        self.panel.command_triggered.connect(sent.append)
        buttons[-1].clicked.emit()
        self.assertFalse(self.panel._param_bar.isHidden())
        self.assertFalse(sent)
        self.panel.undo()
        self.assertEqual(cp._load_favorites(), original)
        self.panel.redo()
        self.assertEqual(cp._load_favorites()[-1], favorite)

    def test_favorite_description_survives_edit(self):
        self.panel._commit_config([{"label": "One", "command": "AT", "description": "Original"}])
        dialog = Mock()
        dialog.exec.return_value = True
        dialog.get_result.return_value = {"name": "Two", "command": "AT", "description": "Changed"}
        with patch.object(cp, "_CmdDialog", return_value=dialog):
            self.panel._edit_favorite(0)
        self.assertEqual(cp._load_favorites()[0]["description"], "Changed")
        self.panel.undo()
        self.assertEqual(cp._load_favorites()[0]["description"], "Original")

    def test_reset_is_undoable_and_keeps_other_files(self):
        command = self.panel._commands_for_function("Identity")[0]
        self.edit(command, "Identity", name="Customized")
        before = deepcopy(self.panel._user)
        for name in ("macros/my_suite.yaml", "logs/session.log"):
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("private user data")
        with patch.object(cp.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.panel.reset_defaults()
        self.assertEqual(self.panel._user, {"added": [], "removed": []})
        self.assertEqual(cp._load_favorites(), cp._FAV_DEFAULTS)
        self.assertEqual((self.root / "macros/my_suite.yaml").read_text(), "private user data")
        self.assertEqual((self.root / "logs/session.log").read_text(), "private user data")
        self.panel.undo()
        self.assertEqual(self.panel._user, before)
        self.panel.redo()
        self.assertEqual(self.panel._user, {"added": [], "removed": []})

    def test_cancel_reset_keeps_config(self):
        original = deepcopy(self.panel._user)
        with patch.object(cp.QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.panel.reset_defaults()
        self.assertEqual(self.panel._user, original)
        self.assertFalse(self.user_path.exists())

    def test_custom_category_undo_redo_rebuilds_page_mapping(self):
        with patch.object(cp.QInputDialog, "getText", return_value=("My category", True)):
            self.panel._add_category()
        self.assertIn("My category", self.panel._fn_to_page)
        self.panel.undo()
        self.assertNotIn("My category", self.panel._fn_to_page)
        self.panel.redo()
        self.assertIn("My category", self.panel._fn_to_page)
        self.assertEqual(self.panel._fn_to_page["My category"], self.panel._pages[-1])

    def test_builtin_category_hiding_persists_and_is_undoable(self):
        with patch.object(cp.QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.panel._delete_category(1, "Identity")
        reloaded = cp.CommandPanel()
        self.addCleanup(reloaded.deleteLater)
        self.assertNotIn("Identity", [reloaded._cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                                      for i in range(reloaded._cat_list.count())])
        self.panel.undo()
        self.assertIn("Identity", [self.panel._cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                                   for i in range(self.panel._cat_list.count())])

    def test_search_uses_current_edits_and_removals(self):
        command = self.panel._commands_for_function("Identity")[0]
        self.edit(command, "Identity", name="Unique changed label")
        self.panel._search_box.setText(command["command"])
        self.panel._on_search(command["command"])
        results = self.panel._search_page.findChildren(cp._CmdButton)
        matching = [b for b in results if b._raw_command == command["command"]]
        self.assertEqual(len(matching), 1)
        self.assertIn("Unique changed label"[:19] + "…",
                      [l.text() for l in matching[0].findChildren(QLabel)])
        replacement = self.panel._commands_for_function("Identity")[-1]
        self.panel._hide_cmd(replacement, "Identity")
        self.panel._commit_config()
        results = self.panel._search_page.findChildren(cp._CmdButton)
        self.assertFalse(any(b._raw_command == command["command"] for b in results))

    def test_ordering_does_not_drop_commands_with_identical_templates(self):
        commands = [{"name": "One", "command": "AT"}, {"name": "Two", "command": "AT"}]
        self.panel._user["order"] = {"Identity": ["AT"]}
        self.assertEqual(self.panel._apply_order(commands, "Identity"), commands)

    def test_every_bundled_vendor_command_is_reachable(self):
        for name, filename in cp.MANUFACTURER_FILES.items():
            if not filename:
                continue
            self.panel._on_mfr(name)
            expected = sum(len(c["commands"]) for c in cp._load_yaml(filename)["categories"])
            actual = sum(len([c for c in self.panel._commands_for_function(fn)
                              if c.get("_src") == "vendor"]) for fn, _, _ in cp.FUNCTION_TABS[1:])
            self.assertEqual(actual, expected, name)

    def test_unknown_vendor_category_falls_back_to_other(self):
        self.panel._vendor_cache["Fibocom"] = ({"New YAML category": [{"name": "New", "command": "AT+NEW"}]}, "")
        self.panel._on_mfr("Fibocom")
        self.assertIn("AT+NEW", [c["command"] for c in self.panel._commands_for_function("Other")])

    def test_model_profiles_filter_scoped_entries_and_all_keeps_legacy_forms(self):
        self.panel._on_mfr('Fibocom')
        self.panel._profile_combo.setCurrentIndex(self.panel._profile_combo.findData('fm350'))
        commands = [c['command'] for c in self.panel._commands_for_function('Identity')]
        self.assertIn('AT+GTMCFWVER', commands)
        self.assertNotIn('AT+GTSDK', commands)
        self.panel._profile_combo.setCurrentIndex(0)
        self.assertIn('AT+GTSDK', [c['command'] for c in self.panel._commands_for_function('Identity')])

    def test_esp_profiles_hide_cellular_commands_and_esp8266_hides_ble(self):
        self.panel._on_mfr('Espressif ESP-AT')
        self.assertFalse(any(c.get('_src') == 'gpp' for c in self.panel._commands_for_function('Network')))
        self.panel._profile_combo.setCurrentIndex(self.panel._profile_combo.findData('esp8266'))
        commands = [c['command'] for c in self.panel._commands_for_function('Other')]
        self.assertIn('AT+CWLAP', commands)
        self.assertFalse(any('BLE' in c for c in commands))
        self.panel._profile_combo.setCurrentIndex(self.panel._profile_combo.findData('esp32'))
        self.assertIn('AT+BLEINIT?', [c['command'] for c in self.panel._commands_for_function('Other')])

    def test_repeated_yaml_categories_merge_without_dropping_commands(self):
        categories = cp._build_cat_map({'categories': [
            {'name': 'Network', 'commands': [{'command': 'AT+OLD'}]},
            {'name': 'Network', 'commands': [{'command': 'AT+NEW'}]}]})
        self.assertEqual([c['command'] for c in categories['Network']], ['AT+OLD', 'AT+NEW'])

    def test_old_hidden_replacement_recovers_parameter_metadata(self):
        command = next(c for c in self.panel._commands_for_function("Network") if c.get("params"))
        cp._save_user({"added": [{"name": "Legacy edit", "command": command["command"],
                                  "description": "Legacy description", "function": "Network", "source": "user"}],
                       "removed": [{"function": "Network", "command": command["command"]}]})
        reloaded = cp.CommandPanel()
        self.addCleanup(reloaded.deleteLater)
        replacement = next(c for c in reloaded._commands_for_function("Network") if c["name"] == "Legacy edit")
        self.assertEqual(replacement["params"], command["params"])
        self.assertEqual(replacement["description"], "Legacy description")

    def test_atomic_save_failure_leaves_existing_file_intact(self):
        cp._save_user({"added": [], "removed": []})
        original = self.user_path.read_bytes()
        with patch.object(cp.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                cp._save_user({"added": [{"command": "AT"}]})
        self.assertEqual(self.user_path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [self.user_path])


class ParameterBarTests(unittest.TestCase):
    def setUp(self):
        self.bar = cp._ParamBar()
        self.addCleanup(self.bar.deleteLater)

    def test_free_text_equals_is_preserved(self):
        self.bar.load("Example", 'AT+EXAMPLE="{text}",{mode}', [
            {"id": "text", "default": "token=a=b"},
            {"id": "mode", "type": "select", "options": ["1=Enable"], "default": "1=Enable"}])
        self.assertEqual(self.bar._assemble(), 'AT+EXAMPLE="token=a=b",1')

    def test_optional_trailing_fields_are_omitted_but_internal_blanks_remain(self):
        self.bar.load("Example", "AT+EXAMPLE={a},{b},{c},{d}", [
            {"id": "a", "default": "1"}, {"id": "b"}, {"id": "c", "default": "3"}, {"id": "d"}])
        self.assertEqual(self.bar._assemble(), "AT+EXAMPLE=1,,3")

    def test_missing_fields_prevent_sending_a_raw_template(self):
        sent = []
        self.bar.send_requested.connect(sent.append)
        self.bar.load("Broken", "AT+EXAMPLE={missing}", [{"id": "other"}])
        with patch.object(cp.QMessageBox, "warning") as warning:
            self.bar._on_send()
        warning.assert_called_once()
        self.assertFalse(sent)

    def test_fibocom_corrected_command_previews(self):
        commands = {c["name"]: c for category in cp._load_yaml("fibocom.yaml")["categories"]
                    for c in category["commands"]}
        expected = {"RAT": "AT+GTRAT=10", "AT+GTGPS": 'AT+GTGPS="RMC"',
                    "AT+GTGPSCERT": "AT+GTGPSCERT=0,1", "AT+GTCELLLOCK": "AT+GTCELLLOCK=0",
                    "AT+GTWWAN": "AT+GTWWAN=0,1", "AT+GTSETMBN": "AT+GTSETMBN=0"}
        for name, preview in expected.items():
            command = commands[name]
            self.bar.load(name, command["command"], command["params"])
            self.assertEqual(self.bar._assemble(), preview, name)

    def test_all_bundled_templates_have_parameter_definitions(self):
        from string import Formatter
        for path in Path(cp.COMMANDS_DIR).glob("*.yaml"):
            library = yaml.safe_load(path.read_text())
            for category in library["categories"]:
                for command in category["commands"]:
                    fields = {field for _, field, _, _ in Formatter().parse(command["command"])
                              if field is not None}
                    parameters = {p["id"] for p in command.get("params", [])}
                    self.assertTrue(fields <= parameters, (path.name, command["name"], fields - parameters))


if __name__ == "__main__":
    unittest.main()
