import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

from modemdiag.core.ai_diagnostics import MAX_ROUNDS, parse_plan
from modemdiag.ui.ai_panel import AIDiagPanel

APP = QApplication.instance() or QApplication([])


class FakeAIClient(QObject):
    completed = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.requests = []

    def request(self, config, messages):
        self.busy = True
        self.requests.append((config, messages))

    def answer(self, commands=(), reply="Diagnostic result"):
        self.busy = False
        self.completed.emit(parse_plan(json.dumps({"reply": reply, "commands": [
            {"command": command, "reason": "Inspect the next state"} for command in commands]})))

    def cancel(self):
        self.busy = False


def attach_fake_client(panel):
    panel._client.cancel()
    fake = FakeAIClient(panel)
    fake.completed.connect(panel._on_answer)
    fake.failed.connect(panel._on_error)
    panel._client = fake
    return fake


class AIPanelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "ai-settings.json"
        self.panel = AIDiagPanel(self.path)
        self.fake = attach_fake_client(self.panel)
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(lambda: self.panel.stop(quiet=True))
        self.panel._key.setText("sk-test-123456789")
        self.runs = []
        self.panel.commands_requested.connect(lambda commands, automatic: self.runs.append((commands, automatic)))

    def send(self, mode="review"):
        self.panel._mode.setCurrentIndex(self.panel._mode.findData(mode))
        self.panel._input.setPlainText("Why is the modem offline?")
        self.panel._send()

    def finish_commands(self, success=True):
        commands = self.runs[-1][0]
        self.panel.on_execution_finished([{"command": item["command"], "response": "OK" if success else "TIMEOUT", "success": success} for item in commands])
        APP.processEvents()

    def test_connection_test_sends_no_modem_context_and_saves_no_key(self):
        self.panel.set_context_provider(lambda *args: "PRIVATE MODEM CONTEXT")
        self.panel._test_connection()
        self.assertNotIn("PRIVATE MODEM CONTEXT", str(self.fake.requests))
        self.fake.answer(["AT+CSQ"])
        self.assertIn("verified", self.panel._connection_status.text())
        self.assertEqual(self.runs, [])
        self.assertNotIn(self.panel._key.text(), self.path.read_text())

    def test_chat_mode_never_executes_commands_even_if_the_model_proposes_them(self):
        self.send("chat")
        self.fake.answer(["AT+CSQ"])
        self.assertEqual(self.runs, [])
        self.assertEqual(self.panel._proposals, [])

    def test_review_mode_preselects_only_reads_and_feeds_selected_results_back(self):
        self.panel.set_connected(True)
        self.send()
        self.fake.answer(["AT+CSQ", "AT+CEREG=2", "AT&F"])
        self.assertEqual(self.runs, [])
        self.assertEqual(self.panel._plan.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertEqual(self.panel._plan.item(1, 0).checkState(), Qt.CheckState.Unchecked)
        self.assertFalse(self.panel._plan.item(2, 0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.panel._run_selected()
        self.assertEqual([item["command"] for item in self.runs[0][0]], ["AT+CSQ"])
        self.assertFalse(self.runs[0][1])
        self.finish_commands()
        self.assertIn("Observed command results", str(self.fake.requests[-1][1]))

    def test_auto_mode_runs_reads_and_preserves_configuration_for_later_review(self):
        self.panel.set_connected(True)
        self.send("auto")
        self.fake.answer(["AT+CSQ", "AT+CEREG=2"])
        self.assertTrue(self.runs[0][1])
        self.assertEqual([item["command"] for item in self.runs[0][0]], ["AT+CSQ"])
        self.finish_commands()
        self.fake.answer([])
        self.assertFalse(self.panel.is_controlling)
        self.assertEqual(self.panel._proposals[0]["command"], "AT+CEREG=2")
        self.assertEqual(self.panel._plan.item(0, 0).checkState(), Qt.CheckState.Unchecked)

    def test_auto_round_limit_stops_commands_and_requests_a_final_summary(self):
        self.panel.set_connected(True)
        self.send("auto")
        for _ in range(MAX_ROUNDS):
            self.fake.answer(["AT+CSQ"])
            self.finish_commands()
        self.assertEqual(len(self.runs), MAX_ROUNDS)
        self.assertFalse(self.panel.is_controlling)
        self.assertIn("mode: chat", self.fake.requests[-1][1][0]["content"])
        self.fake.answer(["AT+CSQ"])
        self.assertEqual(len(self.runs), MAX_ROUNDS)

    def test_failure_stops_auto_execution_and_stop_cancels_deferred_analysis(self):
        self.panel.set_connected(True)
        self.send("auto")
        self.fake.answer(["AT+CSQ"])
        commands = [{"command": "AT+CSQ", "response": "TIMEOUT", "success": False}]
        self.panel.on_execution_finished(commands)
        self.assertFalse(self.panel.is_controlling)
        self.panel.stop(quiet=True)
        APP.processEvents()
        self.assertEqual(len(self.fake.requests), 1)
        self.assertFalse(self.fake.busy)

    def test_stop_emits_only_for_its_own_active_command_run(self):
        stops = []
        self.panel.stop_requested.connect(lambda: stops.append(True))
        self.panel.set_connected(True)
        self.send("auto")
        self.panel.stop(quiet=True)
        self.assertEqual(stops, [])
        self.send("auto")
        self.fake.answer(["AT+CSQ"])
        self.panel.stop(quiet=True)
        self.assertEqual(stops, [True])

    def test_compatible_provider_never_reuses_openai_environment_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-private-environment"}):
            self.panel._provider.setCurrentIndex(self.panel._provider.findData("compatible"))
            self.panel._base.setText("http://localhost:11434/v1")
            self.panel._model.setText("local-model")
            config = self.panel._configuration()
        self.assertEqual(config.api_key, "")

    def test_connection_change_invalidates_old_proposals_before_execution(self):
        self.panel.set_connected(True)
        self.send()
        self.fake.answer(["AT+CSQ"])
        self.panel._model.setText("another-model")
        self.panel._run_selected()
        self.assertEqual(self.runs, [])
        self.assertIn("new diagnostic plan", self.panel._status.text())
