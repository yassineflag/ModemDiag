import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox
from modemdiag.ui import automation_panel as ap

APP = QApplication.instance() or QApplication([])


class AutomationPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.macros = self.root / "macros"
        self.diagnostics = self.root / "diagnostics"
        self.macros.mkdir()
        self.diagnostics.mkdir()
        for name, value in (("MACROS_DIR", str(self.macros)), ("DIAG_DIR", str(self.diagnostics))):
            patcher = patch.object(ap, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(ap, "get_data_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bundled = self.diagnostics / "suite.yaml"
        self.bundled.write_text("name: Default\nsteps: []\n")
        self.panel = ap.AutomationPanel()
        self.addCleanup(self.panel.deleteLater)
        self.panel._current_path = str(self.bundled)
        self.panel._load({"name": "Private local suite", "description": "Local configuration", "steps": []})

    def test_save_keeps_bundled_diagnostic_unchanged(self):
        original = self.bundled.read_bytes()
        self.panel._save()
        self.assertEqual(self.bundled.read_bytes(), original)
        saved = self.macros / "suite.yaml"
        self.assertEqual(self.panel._current_path, str(saved))
        self.assertEqual(yaml.safe_load(saved.read_text())["name"], "Private local suite")

    def test_source_example_saves_under_a_custom_name(self):
        example = self.macros / "example_lte_check.yaml"
        example.write_text("name: Example\nsteps: []\n")
        self.panel._current_path = str(example)
        self.panel._save()
        self.assertEqual(example.read_text(), "name: Example\nsteps: []\n")
        self.assertEqual(self.panel._current_path, str(self.macros / "example_lte_check_custom.yaml"))
        # Repeated saves update the same local file.
        self.panel._name_edit.setText("Updated local suite")
        self.panel._save()
        self.assertEqual(len(list(self.macros.glob("*_custom*.yaml"))), 1)

    def test_declining_existing_copy_overwrite_keeps_both_files(self):
        saved = self.macros / "suite.yaml"
        saved.write_text("name: Existing local suite\nsteps: []\n")
        with patch.object(ap.QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.panel._save()
        self.assertEqual(self.panel._current_path, str(self.bundled))
        self.assertEqual(yaml.safe_load(saved.read_text())["name"], "Existing local suite")

    def test_bundled_diagnostic_cannot_be_deleted(self):
        self.panel._select_by_path(str(self.bundled))
        with patch.object(ap.QMessageBox, "information") as information:
            self.panel._delete()
        information.assert_called_once()
        self.assertTrue(self.bundled.exists())

    def test_vendor_scope_sends_only_shared_and_selected_vendor_steps(self):
        self.panel._load({"steps": [
            {"command": "AT"},
            {"command": "AT+QNWINFO", "manufacturer": "Quectel"},
            {"command": "AT+GTCCINFO?", "manufacturer": "Fibocom"},
        ]})
        sent = []
        self.panel.run_suite_requested.connect(sent.append)
        self.panel._on_run()
        self.assertEqual([s["command"] for s in sent[-1]], ["AT"])
        self.panel._run_vendor.setCurrentText("Fibocom")
        self.panel._on_run()
        self.assertEqual([s["command"] for s in sent[-1]], ["AT", "AT+GTCCINFO?"])
        self.assertEqual(self.panel._run_rows, [0, 2])
        self.panel._run_vendor.setCurrentText("All steps")
        self.panel._on_run()
        self.assertEqual(len(sent[-1]), 3)

    def test_saving_preserves_suite_and_step_metadata(self):
        self.panel._load({"name": "Vendor suite", "manufacturer": "Fibocom", "icon": "test",
                          "steps": [{"command": "AT", "manufacturer": "Fibocom",
                                     "notes": "Keep this local field"}]})
        self.panel._save()
        saved = yaml.safe_load(Path(self.panel._current_path).read_text())
        self.assertEqual(saved["manufacturer"], "Fibocom")
        self.assertEqual(saved["icon"], "test")
        self.assertEqual(saved["steps"][0]["manufacturer"], "Fibocom")
        self.assertEqual(saved["steps"][0]["notes"], "Keep this local field")

    def test_suite_vendor_is_inherited_by_steps(self):
        self.panel._load({"manufacturer": "Fibocom", "steps": [{"command": "AT"}]})
        self.assertEqual(self.panel._run_vendor.currentText(), "Fibocom")
        sent = []
        self.panel.run_suite_requested.connect(sent.append)
        self.panel._on_run()
        self.assertEqual([s["command"] for s in sent[-1]], ["AT"])

    def test_advanced_match_completion_and_payload_fields_survive_edit_and_save(self):
        self.panel._load({'name': 'MQTT', 'steps': [{
            'command': 'AT+SEND={length}', 'expect_regex': '^SEND OK$',
            'prompt': '>', 'payload': 'é', 'payload_encoding': 'utf-8',
            'payload_ending': 'none', 'wait_for': '^DONE$', 'fail_on': '^FAIL$', 'final_ok': 'SEND OK'}]})
        self.panel._on_row_selected(0)
        self.assertEqual(self.panel._p_match.currentText(), 'Regex')
        self.assertEqual(self.panel._p_payload.toPlainText(), 'é')
        self.panel._p_payload.setPlainText('hello')
        self.panel._save()
        saved = yaml.safe_load(Path(self.panel._current_path).read_text())['steps'][0]
        self.assertEqual(saved['payload'], 'hello')
        self.assertEqual(saved['expect_regex'], '^SEND OK$')
        self.assertEqual(saved['wait_for'], '^DONE$')
        self.assertEqual(saved['fail_on'], '^FAIL$')
        self.assertEqual(saved['final_ok'], 'SEND OK')

    def test_unchecked_steps_remain_editable_and_can_be_enabled(self):
        self.panel._load({"name": "Selectable steps", "steps": [
            {"command": "AT"}, {"command": "AT+PAYLOAD", "enabled": False},
            {"command": "AT+CSQ"},
        ]})
        sent = []
        self.panel.run_suite_requested.connect(sent.append)
        self.panel._on_run()
        self.assertEqual([s["command"] for s in sent[-1]], ["AT", "AT+CSQ"])
        self.assertEqual(self.panel._run_rows, [0, 2])
        self.assertEqual(self.panel._steps[1]["command"], "AT+PAYLOAD")
        self.panel._save()
        saved = yaml.safe_load(Path(self.panel._current_path).read_text())
        self.assertFalse(saved["steps"][1]["enabled"])
        self.panel._table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        self.panel._on_run()
        self.assertEqual([s["command"] for s in sent[-1]], ["AT", "AT+PAYLOAD", "AT+CSQ"])

    def test_bundled_suites_keep_compatibility_forms_and_corrected_commands(self):
        root = Path(__file__).resolve().parents[1]
        nr = yaml.safe_load((root / "resources/diagnostics/5g_nr.yaml").read_text())
        self.assertTrue({"AT+GTNRINFO", "AT#NR5GINFO", "AT+GTCCINFO?", "AT+GTCAINFO?"}
                        <= {step["command"] for step in nr["steps"]})
        gnss = yaml.safe_load((root / "resources/diagnostics/gnss.yaml").read_text())
        commands = {step["command"]: step for step in gnss["steps"]}
        self.assertTrue({"AT+GTGPSPOWER=1", "AT+GTGPS?", "AT+CGPS=1", "AT+CGPSINFO",
                         "AT+CGPSPWR=1", "AT+CGPSINF=0"} <= commands.keys())
        self.assertEqual(commands["AT+CGPSINF=0"]["manufacturer"], "SIMCom SIM808")
        for kind in ("tcp", "udp"):
            path = root / f"resources/diagnostics/fibocom_ma510_{kind}.yaml"
            commands = {step["command"]: step for step in yaml.safe_load(path.read_text())["steps"]}
            self.assertIn('AT+GTSET="IPRFMT",5', commands)
            for command in ("AT+MIPSEND=1,12", "AT+MIPREAD=1,12"):
                self.assertIn(command, commands)
                self.assertFalse(commands[command]["enabled"])


if __name__ == "__main__":
    unittest.main()
