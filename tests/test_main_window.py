import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog, QTextBrowser, QPushButton
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl, QEventLoop, QTimer
from PyQt6.QtGui import QColor
from modemdiag.ui.theme import apply_theme, set_tooltip
from modemdiag.ui import command_panel as cp, automation_panel as ap, main_window as mw
from modemdiag.ui.port_selector import PortSelectorWidget
from modemdiag.core.network_status import overview_steps, cell_steps, NOT_REPORTED, NetworkSnapshot, CellInfo, Measurement, DataContext
from tests.release_policy_fixtures import policy_document, public_key, Ed25519PrivateKey

APP = QApplication.instance() or QApplication([])


class MainWindowTests(unittest.TestCase):
    def setUp(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for module, name, value in ((cp, "USER_FILE", str(root / "commands.yaml")),
                                     (cp, "FAVORITES_FILE", str(root / "favorites.yaml")),
                                     (ap, "MACROS_DIR", str(root)), (ap, "DIAG_DIR", str(root))):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (patch.object(PortSelectorWidget, "_start_detector"),
                        patch.object(mw.ReleaseGuard, "start"),
                        patch.object(mw, "get_user_dir", return_value=root),
                        patch.object(mw, "logs_dir", return_value=root)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = mw.MainWindow()
        self.addCleanup(self._delete_window)
        self.addCleanup(apply_theme, APP, "dark", self.window)
        self.addCleanup(self.window.close)
        self.policy_key = Ed25519PrivateKey.generate()
        self.window._release_guard.state.public_key_b64 = public_key(self.policy_key)

    def _delete_window(self):
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _connect_fake_modem(self):
        from unittest.mock import Mock
        self.window._serial._port = Mock(is_open=True)
        self.window._on_connection_changed(True)

    def _run_dashboard(self, responses, after_start=None):
        """Exercise serial chunking and the real runner without hardware or delays."""
        loop = QEventLoop()
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        watchdog.start(2000)

        def respond(command):
            response = responses.get(command, "ERROR") + "\r\n"
            QTimer.singleShot(0, lambda: self.window._on_data_received(response[:-1]))
            QTimer.singleShot(1, lambda: self.window._on_data_received(response[-1:]))
            return True, None

        def finished(*args):
            if self.window._run_source is None:
                # CR can complete a response before a separately queued LF.
                QTimer.singleShot(2, loop.quit)

        def quick(steps):
            return [{**step, "delay_ms": 0, "timeout_ms": 100} for step in steps]

        self.window._runner.suite_finished.connect(finished)
        try:
            with patch.object(mw, "overview_steps", return_value=quick(overview_steps())), \
                    patch.object(mw, "cell_steps", side_effect=lambda snapshot: quick(cell_steps(snapshot))), \
                    patch.object(self.window._serial, "send", side_effect=respond) as send:
                self.window._refresh_dashboard()
                if after_start:
                    after_start()
                loop.exec()
                self.assertTrue(watchdog.isActive(), "Dashboard run did not finish")
                self.assertFalse(self.window._runner.is_running)
                return [call.args[0] for call in send.call_args_list]
        finally:
            watchdog.stop()
            self.window._runner.suite_finished.disconnect(finished)

    def test_dashboard_is_the_first_tab_and_requires_a_connection(self):
        tabs = self.window._tabs
        self.assertEqual(tabs.currentWidget(), self.window._dashboard)
        self.assertIn("Dashboard", tabs.tabText(0))
        self.assertIn("AT Commands", tabs.tabText(1))
        self.assertIn("Automation", tabs.tabText(2))
        self.assertIn("AI Diag", tabs.tabText(3))
        self.assertIn("★", tabs.tabText(3))
        self.assertIn("Beta", tabs.tabText(3))
        self.assertFalse(self.window._dashboard._refresh.isEnabled())
        with patch.object(self.window._serial, "send") as send:
            self.window._refresh_dashboard()
        send.assert_not_called()

    def test_dashboard_refresh_handles_chunks_and_vendor_cells_without_editing_automation(self):
        self._connect_fake_modem()
        automation = self.window._auto_panel
        automation._load({"name": "Keep this suite", "steps": [{"command": "AT+KEEP"}]})
        before = automation._status_lbl.text()
        sent = self._run_dashboard({
            "ATI": "Quectel\r\nRM520N-GL\r\nOK",
            "AT+CSQ": "+CSQ: 23,99\r\nOK",
            "AT+CPIN?": "+CPIN: READY\r\nOK",
            "AT+COPS?": '+COPS: 0,0,"Example Mobile",7\r\nOK',
            "AT+CIREG?": "+CIREG: 0,1\r\nOK",
            'AT+QENG="servingcell"': '+QENG: "servingcell","NOCONN","LTE","FDD",001,01,1234567,123,1650,3,5,5,1234,-91,-10,-67,16,9,230,-\r\nOK',
            'AT+QENG="neighbourcell"': '+QENG: "neighbourcell intra","LTE",1650,124,-12,-99,-70,0,37,7,16,6,44\r\nOK',
        })
        dashboard = self.window._dashboard
        self.assertEqual(dashboard._operator.text(), "Example Mobile")
        self.assertEqual(dashboard._services["IMS"].text(), "Registered")
        self.assertEqual(dashboard._signals["RSSI"].text(), "-67 dBm")
        self.assertEqual(dashboard._cells.rowCount(), 2)
        self.assertIn('AT+QENG="neighbourcell"', sent)
        self.assertEqual(automation._table.item(0, 0).text(), "AT+KEEP")
        self.assertEqual(automation._status_lbl.text(), before)
        self.assertTrue(dashboard._refresh.isEnabled())
        self.assertIn("AT+CSQ", self.window._terminal._output.toPlainText())
        self._run_dashboard({})
        self.assertEqual(dashboard._signals["RSSI"].text(), NOT_REPORTED)
        self.assertEqual(dashboard._cells.rowCount(), 0)
        self.assertEqual(dashboard._services["IMS"].text(), NOT_REPORTED)

    def test_dashboard_blocks_manual_sends_and_hands_over_to_queued_automation(self):
        self._connect_fake_modem()
        def start_automation():
            self.window._send_command("AT+MANUAL")
            steps = [{"command": "AT+AUTOMATED", "delay_ms": 0}]
            self.window._start_suite(steps)
            steps[0]["command"] = "AT+EDITED_AFTER_START"
            self.assertIsNotNone(self.window._pending_suite)
        sent = self._run_dashboard({"ATI": "Quectel\r\nRM520N\r\nOK", "AT+AUTOMATED": "OK"}, start_automation)
        self.assertEqual(sent[-1], "AT+AUTOMATED")
        self.assertNotIn("AT+MANUAL", sent)
        self.assertNotIn('AT+QENG="servingcell"', sent)
        self.assertIsNone(self.window._pending_suite)
        self.assertTrue(self.window._dashboard._refresh.isEnabled())

    def test_stopping_queued_automation_does_not_interrupt_the_dashboard(self):
        self._connect_fake_modem()
        def cancel_automation():
            self.window._start_suite([{"command": "AT+AUTOMATED"}])
            self.window._auto_panel._on_stop()
            self.assertTrue(self.window._runner.is_running)
        sent = self._run_dashboard({}, cancel_automation)
        self.assertNotIn("AT+AUTOMATED", sent)

    def test_live_refresh_waits_for_manual_response_and_only_polls_a_visible_dashboard(self):
        self._connect_fake_modem()
        dashboard = self.window._dashboard
        with patch.object(self.window._serial, "send", return_value=(True, None)) as send:
            self.window._send_command("AT+CSQ")
            self.window._refresh_dashboard()
            self.assertEqual(send.call_count, 1)
            self.window._on_data_received("+CSQ: 23,99\r\nOK\r\n")
            self.assertFalse(self.window._manual_timer.isActive())
        self.window.show()
        APP.processEvents()
        with patch.object(self.window._runner, "run") as run:
            dashboard._live.setChecked(True)
            self.assertTrue(dashboard._timer.isActive())
            run.assert_called_once()
            self.window._run_source = None
            dashboard.set_busy(False)
            self.window._tabs.setCurrentWidget(self.window._cmd_panel)
            dashboard._poll()
            run.assert_called_once()
        self.window._on_disconnect()
        self.assertFalse(dashboard._timer.isActive())
        self.assertFalse(dashboard._live.isChecked())
        self.assertEqual(dashboard._signals["RSSI"].text(), NOT_REPORTED)

    def test_disconnect_and_revocation_stop_dashboard_queries(self):
        self._connect_fake_modem()
        with patch.object(self.window._serial, "send", return_value=(True, None)) as send:
            self.window._refresh_dashboard()
            self.assertTrue(self.window._runner.is_running)
            self.window._on_disconnect()
            self.assertFalse(self.window._runner.is_running)
            self.assertIsNone(self.window._run_source)
            self.assertFalse(self.window._dashboard._refresh.isEnabled())
        self._connect_fake_modem()
        with patch.object(self.window._serial, "send", return_value=(True, None)) as send:
            self.window._refresh_dashboard()
            self.window._release_guard.apply_policy(policy_document(self.policy_key, revoke_all=True))
            self.assertFalse(self.window._runner.is_running)
            self.assertFalse(self.window._dashboard._refresh.isEnabled())
            count = send.call_count
            self.window._refresh_dashboard()
            self.assertEqual(send.call_count, count)

    def test_dashboard_fits_default_window_and_switches_theme(self):
        self.window.resize(1280, 780)
        self.window.show()
        APP.processEvents()
        self.assertLessEqual(self.window.minimumSizeHint().width(), 1280)
        self.assertLessEqual(self.window.minimumSizeHint().height(), 780)
        self.window._theme_btn.click()
        self.assertIn("#0969da", self.window._dashboard._operator.styleSheet())

    def test_dashboard_terminal_toggle_and_other_tabs_restore_terminal(self):
        self.window.show()
        APP.processEvents()
        self.assertTrue(self.window._terminal.isHidden())
        self.window._dashboard._terminal_toggle.click()
        self.assertFalse(self.window._terminal.isHidden())
        self.window._dashboard._terminal_toggle.click()
        self.assertTrue(self.window._terminal.isHidden())
        self.window._tabs.setCurrentWidget(self.window._cmd_panel)
        self.assertFalse(self.window._terminal.isHidden())
        self.window._tabs.setCurrentWidget(self.window._dashboard)
        self.assertTrue(self.window._terminal.isHidden())

    def test_ai_read_run_uses_shared_runner_and_returns_results_for_analysis(self):
        from tests.test_ai_panel import attach_fake_client
        self._connect_fake_modem()
        panel = self.window._ai_panel
        fake = attach_fake_client(panel)
        panel._key.setText("sk-test-123456789")
        panel._mode.setCurrentIndex(panel._mode.findData("auto"))
        panel._input.setPlainText("Read the signal and explain it")
        panel._send()
        with patch.object(self.window._serial, "send", return_value=(True, None)) as send:
            fake.answer(["AT+CSQ"])
            self.assertEqual(self.window._run_source, "ai")
            send.assert_called_once_with("AT+CSQ")
            self.window._on_data_received("+CSQ: 23,99\r\nOK\r\n")
            loop = QEventLoop()
            QTimer.singleShot(400, loop.quit)
            loop.exec()
        self.assertFalse(self.window._runner.is_running)
        self.assertIsNone(self.window._run_source)
        self.assertEqual(len(fake.requests), 2)
        self.assertIn("+CSQ: 23,99", str(fake.requests[-1][1]))
        fake.answer([], "The modem reports RSSI -67 dBm.")
        self.assertFalse(panel.is_controlling)
        self.assertIn("-67 dBm", panel._chat.toPlainText())
        self.assertIn("AT+CSQ", self.window._terminal._output.toPlainText())

    def test_ai_chat_and_command_plan_fit_default_window_in_both_themes(self):
        from modemdiag.core.ai_diagnostics import parse_plan
        panel = self.window._ai_panel
        self.window._tabs.setCurrentWidget(panel)
        panel._show_plan(parse_plan('{"reply":"Read signal","commands":[{"command":"AT+CSQ","reason":"Inspect received signal power"}]}')["commands"])
        self.window.resize(1280, 780)
        self.window.show()
        APP.processEvents()
        self.assertLessEqual(self.window.minimumSizeHint().height(), 780)
        self.assertLessEqual(self.window.minimumSizeHint().width(), 1280)
        self.window._theme_btn.click()
        self.assertIn("#f6f8fa", panel._stop.styleSheet())

    def test_ai_commands_are_blocked_when_busy_disconnected_revoked_or_outside_auto_policy(self):
        panel = self.window._ai_panel
        proposals = [{"command": "AT+CSQ", "reason": "Signal"}]
        with patch.object(self.window._serial, "send", return_value=(True, None)) as send:
            self.window._run_ai_commands(proposals, True)
            send.assert_not_called()
            self._connect_fake_modem()
            self.window._run_ai_commands([{"command": "AT+CEREG=2", "access": "read"}], True)
            send.assert_not_called()
            self.window._runner._running = True
            self.window._run_ai_commands(proposals, True)
            send.assert_not_called()
            self.window._runner.stop()
            self.window._release_guard.apply_policy(policy_document(self.policy_key, revoke_all=True))
            self.window._run_ai_commands(proposals, True)
            send.assert_not_called()
        self.assertFalse(panel._run.isEnabled())

    def test_ai_stop_disconnect_and_other_panels_do_not_overlap_command_ownership(self):
        from tests.test_ai_panel import attach_fake_client
        self._connect_fake_modem()
        panel = self.window._ai_panel
        fake = attach_fake_client(panel)
        panel._key.setText("sk-test-123456789")
        panel._mode.setCurrentIndex(panel._mode.findData("auto"))
        panel._input.setPlainText("Inspect registration")
        panel._send()
        with patch.object(self.window._serial, "send", return_value=(True, None)) as send:
            fake.answer(["AT+CEREG?"])
            self.window._send_command("AT+MANUAL")
            self.window._start_suite([{"command": "AT+AUTOMATED"}])
            self.window._refresh_dashboard()
            self.assertEqual(send.call_count, 1)
            self.assertEqual(self.window._run_source, "ai")
            self.assertTrue(self.window._runner.is_running)
            self.window._on_disconnect()
        self.assertFalse(self.window._runner.is_running)
        self.assertFalse(panel.is_controlling)
        self.assertFalse(fake.busy)

    def test_cell_diagram_and_table_select_each_other_without_sending_commands(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        dashboard = self.window._dashboard
        snapshot = NetworkSnapshot(operator="Example", technology="LTE")
        snapshot.cells = [CellInfo("Serving", "LTE", "12345", pci="123", channel="1650", band="3"),
                          CellInfo("Neighbour", "LTE", "54321", pci="124", channel="1650", band="3")]
        dashboard.show_snapshot(snapshot)
        self.window.show()
        APP.processEvents()
        with patch.object(self.window._serial, "send") as send:
            diagram = dashboard._landscape
            QTest.keyClick(diagram, Qt.Key.Key_Right)
            self.assertEqual(diagram.selected, 1)
            self.assertEqual(dashboard._cells.currentRow(), 1)
            self.assertIn("PCI 124", dashboard._selection_title.text())
            node = next(rect for index, rect in diagram._nodes if index == 0)
            QTest.mouseClick(diagram, Qt.MouseButton.LeftButton, pos=node.center().toPoint())
            self.assertEqual(diagram.selected, 0)
            self.assertEqual(dashboard._cells.currentRow(), 0)
            dashboard._cells.setCurrentCell(1, 0)
            self.assertEqual(diagram.selected, 1)
        send.assert_not_called()

    def test_history_has_gaps_for_missing_readings_and_clears_on_disconnect(self):
        self._connect_fake_modem()
        dashboard = self.window._dashboard
        snapshot = NetworkSnapshot()
        snapshot.signals["RSRP"] = Measurement("-95 dBm", -95)
        dashboard.show_snapshot(snapshot)
        dashboard.show_snapshot(NetworkSnapshot())
        self.assertEqual(len(dashboard._timeline.samples), 2)
        self.assertIsNone(dashboard._timeline.samples[-1].values["RSRP"])
        self.assertEqual(dashboard._history.cell_changes, 0)
        dashboard._trace_metric.setCurrentText("SINR")
        self.assertEqual(dashboard._timeline.metric, "SINR")
        self.window._on_disconnect()
        self.assertEqual(len(dashboard._timeline.samples), 0)
        self.assertEqual(len(dashboard._history.events), 0)

    def test_context_unknown_state_is_not_displayed_as_inactive_or_zero_active(self):
        dashboard = self.window._dashboard
        snapshot = NetworkSnapshot(contexts=[DataContext("1", apn="example")])
        dashboard.show_snapshot(snapshot)
        self.assertIn("Context state not reported", dashboard._data_hint.text())
        self.assertEqual(dashboard._contexts.item(0, 1).text(), NOT_REPORTED)
        self.assertEqual(dashboard._ip.text(), "No IP reported")

    def test_help_and_configuration_actions_are_available(self):
        menus = {a.text(): a.menu() for a in self.window.menuBar().actions()}
        self.assertIn("How to Use…", [a.text() for a in menus["Help"].actions()])
        self.assertIn("Restore default command library…", [a.text() for a in menus["Settings"].actions()])
        self.assertIn(self.window._cmd_panel.undo_action, menus["Edit"].actions())
        with patch.object(QMessageBox, "information") as information:
            self.window._show_how_to_use()
            self.window._show_config_location()
        self.assertEqual(information.call_count, 2)

    def test_updates_popup_keeps_privacy_details_separate_without_starting_a_check(self):
        help_menu = next(a.menu() for a in self.window.menuBar().actions() if a.text() == "Help")
        self.assertIn("Updates…", [a.text() for a in help_menu.actions()])
        self.assertIn("Check for updates now", [a.text() for a in help_menu.actions()])
        def inspect(box):
            self.assertEqual(box.windowTitle(), "Updates")
            self.assertIn(mw.__version__, box.text())
            self.assertNotIn("IP address", box.informativeText())
            self.assertNotIn("signed policy", box.informativeText())
            self.assertIn("IP address", box.detailedText())
            self.assertIn("GitHub", box.detailedText())
            self.assertIn("every five minutes", box.detailedText())
            self.assertIn("unsupported version", box.detailedText())
            button = next(button for button in box.buttons() if button.text() == "View releases")
            button.click()
            return 0
        with patch.object(QMessageBox, "exec", new=inspect), \
                patch.object(self.window._release_guard._network, "get") as get, \
                patch.object(mw.QDesktopServices, "openUrl", return_value=True) as open_url:
            self.window._show_release_status()
        get.assert_not_called()
        self.assertEqual(open_url.call_args.args[0].toString(), mw.__url__ + "/releases")

    def test_updates_popup_still_shows_available_updates_and_release_restrictions(self):
        guard = self.window._release_guard
        guard.apply_policy(policy_document(self.policy_key, latest_version="1.3.0"))
        content = []
        def inspect(box):
            content.append(box.informativeText())
            return 0
        with patch.object(QMessageBox, "exec", new=inspect):
            self.window._show_release_status()
            guard.apply_policy(policy_document(self.policy_key, sequence=2, revoke_all=True))
            self.window._show_release_status()
        self.assertIn("1.3.0", content[0])
        self.assertIn(guard.message, content[1])
        self.assertTrue(guard.revoked)
        self.assertFalse(self.window._port_selector.isEnabled())

    def test_continuous_log_controls_record_traffic_and_results_before_disconnect(self):
        self._connect_fake_modem()
        self.window._start_logging()
        first = Path(self.window._log_path)
        self.assertEqual(self.window._log_btn.text(), "● Logging")
        with patch.object(self.window._serial, "send", return_value=(True, None)):
            self.window._send_command("AT+CSQ")
        self.window._on_data_received("+CSQ: 23,99\r\nOK\r\n")
        self.window._on_step_result(0, "TIMEOUT", False)
        self.assertIn("AT+CSQ", first.read_text())
        self.assertIn("+CSQ: 23,99", first.read_text())
        self.assertIn("FAIL — TIMEOUT", first.read_text())
        self.window._log_btn.click()
        self.assertFalse(self.window._logger.active)
        self.window._log_btn.click()
        self.assertTrue(self.window._logger.active)
        self.assertNotEqual(first, Path(self.window._log_path))
        self.window._on_disconnect()
        self.assertFalse(self.window._logger.active)
        self.assertFalse(self.window._stop_log_action.isEnabled())

    def test_every_connection_starts_logging_automatically(self):
        def connect(*args, **kwargs):
            self._connect_fake_modem()
            return True, None
        with patch.object(self.window._serial, "connect", side_effect=connect):
            self.window._on_connect("COM7", 9600, {})
        self.assertTrue(self.window._logger.active)
        self.assertIn("9600", Path(self.window._log_path).read_text())

    def test_failed_logging_remains_visible_and_does_not_interrupt_received_data(self):
        from unittest.mock import Mock
        self._connect_fake_modem()
        stream = Mock()
        stream.write.side_effect = OSError("disk full")
        self.window._logger._fh = stream
        self.window._on_data_received("+CSQ: 23,99\r\nOK\r\n")
        self.assertEqual(self.window._log_btn.text(), "Log error")
        self.assertIn("disk full", self.window._terminal._output.toPlainText())
        self.assertIn("+CSQ: 23,99", self.window._terminal._output.toPlainText())
        self.assertTrue(self.window._start_log_action.isEnabled())

    def test_configuration_import_applies_commands_automations_and_theme_without_restart(self):
        from modemdiag.core.config_transfer import export_configuration, read_archive
        root = self.window._appearance_file.parent
        source = root / "source"
        (source / "macros").mkdir(parents=True)
        (source / "user_commands.yaml").write_text("added: []\nremoved: []\nvendors: [Imported Vendor]\n")
        (source / "quick_send.yaml").write_text("- {command: AT+CSQ, label: Imported Signal}\n")
        (source / "appearance.json").write_text('{"theme":"light"}')
        (source / "ai-settings.json").write_text('{"provider":"compatible","model":"imported-model","base_url":"http://localhost:1234/v1"}')
        (source / "macros/imported.yaml").write_text("name: Imported Suite\nsteps: [{command: AT}]\n")
        (root / "macros").mkdir()
        archive = root / "config.zip"
        export_configuration(source, archive)
        with patch.object(cp, "USER_FILE", str(root / "user_commands.yaml")), \
                patch.object(cp, "FAVORITES_FILE", str(root / "quick_send.yaml")), \
                patch.object(ap, "MACROS_DIR", str(root / "macros")), \
                patch.object(ap, "DIAG_DIR", str(source / "empty")), \
                patch.object(mw.QFileDialog, "getOpenFileName", return_value=(str(archive), "")), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(QMessageBox, "information"):
            self.window._import_configuration()
        self.assertIn("Imported Vendor", self.window._cmd_panel._user["vendors"])
        self.assertEqual(self.window._theme, "light")
        self.assertEqual(self.window._ai_panel._model.text(), "imported-model")
        self.assertEqual(self.window._ai_panel._base.text(), "http://localhost:1234/v1")
        self.assertTrue(any("Imported Suite" in self.window._auto_panel._list.item(i).text()
                            for i in range(self.window._auto_panel._list.count())))
        backup = next((root / "backups").glob("*.zip"))
        self.assertNotIn("user_commands.yaml", read_archive(backup))

    def test_configuration_import_is_blocked_during_connection_or_unsaved_automation(self):
        self._connect_fake_modem()
        with patch.object(mw.QFileDialog, "getOpenFileName") as choose, \
                patch.object(QMessageBox, "information"):
            self.window._import_configuration()
            choose.assert_not_called()
            self.window._on_disconnect()
            self.window._auto_panel._dirty = True
            self.window._import_configuration()
            choose.assert_not_called()
        self.window._auto_panel._dirty = False

    def test_invalid_configuration_archive_is_rejected_before_confirmation(self):
        archive = self.window._appearance_file.parent / "invalid.zip"
        archive.write_text("not an archive")
        with patch.object(mw.QFileDialog, "getOpenFileName", return_value=(str(archive), "")), \
                patch.object(QMessageBox, "question") as confirm, \
                patch.object(QMessageBox, "warning") as warning:
            self.window._import_configuration()
        confirm.assert_not_called()
        warning.assert_called_once()

    def test_licenses_action_explains_distribution_and_opens_license_and_source_links(self):
        help_menu = next(a.menu() for a in self.window.menuBar().actions() if a.text() == "Help")
        actions = help_menu.actions()
        labels = [a.text() for a in actions]
        self.assertEqual(labels[labels.index("About ModemDiag…") + 1], "Licenses…")
        def inspect(dialog):
            browser = dialog.findChild(QTextBrowser)
            content = browser.toPlainText()
            self.assertIn("MIT License", content)
            self.assertIn("GPLv3", content)
            self.assertIn("support are not guaranteed", content)
            self.assertIn("License", content)
            self.assertIn("Source code", content)
            self.assertEqual([button.text().replace("&", "") for button in dialog.findChildren(QPushButton)], ["Close"])
            browser.anchorClicked.emit(QUrl("modemdiag:licenses"))
            browser.anchorClicked.emit(QUrl(mw.__url__ + "/releases/tag/v" + mw.__version__))
            return 0
        with patch.object(QDialog, "exec", new=inspect), \
                patch.object(self.window, "_show_license_notices") as notices, \
                patch.object(mw.QDesktopServices, "openUrl", return_value=True) as open_url:
            actions[labels.index("Licenses…")].trigger()
        notices.assert_called_once()
        self.assertEqual([call.args[0].toString() for call in open_url.call_args_list],
                         [mw.__url__ + "/releases/tag/v" + mw.__version__])

    def test_full_notices_use_external_release_notice_then_bundled_fallback(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        external = root / "NOTICES.txt"
        external.write_text("Supplemental release notices")
        embedded = root / "bundle/release/NOTICES.txt"
        embedded.parent.mkdir(parents=True)
        embedded.write_text("Embedded license text")
        displayed = []
        def inspect(dialog):
            displayed.append(dialog.findChild(QTextBrowser).toPlainText())
            return 0
        with patch.object(mw.sys, "frozen", True, create=True), \
                patch.object(mw.sys, "_MEIPASS", str(root / "bundle"), create=True), \
                patch.object(mw.sys, "executable", str(root / "ModemDiag")), \
                patch.object(QDialog, "exec", new=inspect):
            self.window._show_license_notices()
            external.unlink()
            self.window._show_license_notices()
        self.assertEqual(displayed, ["Supplemental release notices", "Embedded license text"])

    def test_payload_editor_keeps_workflow_table_usable_at_1280_by_780(self):
        from PyQt6.QtWidgets import QTabWidget
        self.window.resize(1280, 780)
        self.window.show()
        self.window.findChild(QTabWidget).setCurrentWidget(self.window._auto_panel)
        panel = self.window._auto_panel
        panel._load({'name': 'Payload example', 'steps': [
            {'command': 'AT+SEND={length}', 'prompt': '>', 'payload': 'hello'} for _ in range(8)]})
        panel._on_row_selected(0)
        APP.processEvents()
        self.assertLessEqual(self.window.minimumSizeHint().height(), 780)
        self.assertGreaterEqual(panel._table.height(), 100)

    def test_theme_switch_preserves_session_text_and_recolors_live_results(self):
        from unittest.mock import Mock
        terminal = self.window._terminal
        terminal.append_tx('AT+TEST="#79c0ff"')
        terminal.append_rx_raw("+TEST: sample\r\nOK")
        terminal._input.setText("AT+CSQ")
        set_tooltip(terminal._send_btn, 'AT+TEST="#79c0ff"')
        original = terminal._output.toPlainText()
        panel = self.window._auto_panel
        panel._load({"steps": [{"command": "AT"}, {"command": "AT+BAD"}]})
        panel._run_rows = [0, 1]
        panel.on_step_started(0, 'AT+TEST="#79c0ff"', "")
        panel.on_step_result(0, "OK", True)
        panel.on_step_result(1, "ERROR", False)
        self.window._serial._port = Mock(is_open=True)
        port = self.window._serial._port
        self.window._runner._running = True
        with patch.object(self.window._serial, "send") as send:
            self.window._theme_btn.click()
        self.assertEqual(self.window._theme, "light")
        self.assertEqual(terminal._output.toPlainText(), original)
        self.assertEqual(terminal._input.text(), "AT+CSQ")
        self.assertEqual(terminal._send_btn.toolTip(), 'AT+TEST="#79c0ff"')
        self.assertIn('AT+TEST="#79c0ff"', panel._status_lbl.text())
        self.assertEqual(panel._table.item(0, 0).background().color(), QColor("#dafbe1"))
        self.assertEqual(panel._table.item(1, 0).background().color(), QColor("#ffebe9"))
        self.assertTrue(self.window._runner.is_running)
        port.close.assert_not_called()
        send.assert_not_called()
        terminal.append_rx_raw("+TEST: subsequent response\r\nOK")
        self.assertIn("subsequent response", terminal._output.toPlainText())
        self.window._theme_btn.click()
        self.assertEqual(self.window._theme, "dark")
        self.assertEqual(panel._table.item(0, 0).background().color(), QColor("#1a3d1a"))

    def test_theme_preference_is_loaded_by_the_next_window(self):
        self.window._theme_btn.click()
        other = mw.MainWindow()
        self.addCleanup(other.deleteLater)
        self.addCleanup(other.close)
        self.assertEqual(other._theme, "light")
        self.assertEqual(other._theme_btn.text(), "Dark theme")
        # Commands constructed while light mode is active also use light colors.
        self.assertIn("#f6f8fa", other._cmd_panel._search_box.styleSheet())

    def test_theme_switch_does_not_unlock_a_revoked_release(self):
        self.window._release_guard.apply_policy(policy_document(self.policy_key, revoke_all=True))
        self.window._theme_btn.click()
        self.assertTrue(self.window._release_guard.revoked)
        self.assertFalse(self.window._port_selector.isEnabled())
        self.assertFalse(self.window._terminal._input.isEnabled())
        self.assertFalse(self.window._auto_panel._run_btn.isEnabled())

    def test_manual_commands_are_blocked_during_automation(self):
        self.window._runner._running = True
        with patch.object(self.window._serial, "send") as send:
            self.window._send_command("AT")
        send.assert_not_called()

    def test_disconnect_stops_runner_and_updates_automation_controls(self):
        self.window._runner._running = True
        self.window._auto_panel._running = True
        self.window._on_disconnect()
        self.assertFalse(self.window._runner.is_running)
        self.assertFalse(self.window._auto_panel._running)
        self.assertTrue(self.window._auto_panel._run_btn.isEnabled())
        self.assertFalse(self.window._auto_panel._stop_btn.isEnabled())

    def test_unexpected_connection_loss_stops_runner(self):
        self.window._runner._running = True
        self.window._auto_panel._running = True
        self.window._on_connection_changed(False)
        self.assertFalse(self.window._runner.is_running)
        self.assertFalse(self.window._auto_panel._running)

    def test_revocation_stops_running_suite_disconnects_and_blocks_all_send_paths(self):
        from unittest.mock import Mock
        runner = self.window._runner
        self.window._serial._port = Mock(is_open=True)
        disconnected = self.window._serial._port
        disconnected.write.side_effect = lambda data: len(data)
        runner.run([{"command": "AT+CHECK"}, {"command": "AT+NEXT"}])
        self.assertTrue(runner.is_running)
        self.window._release_guard.apply_policy(policy_document(self.policy_key, revoke_all=True))
        self.assertFalse(runner.is_running)
        disconnected.close.assert_called_once()
        self.assertTrue(self.window._release_guard.revoked)
        self.assertFalse(self.window._release_banner.isHidden())
        self.assertFalse(self.window._port_selector.isEnabled())
        self.assertFalse(self.window._auto_panel._run_btn.isEnabled())
        with patch.object(self.window._serial, "send") as send, \
                patch.object(self.window._serial, "connect") as connect:
            self.window._send_command("AT")
            self.window._on_connect("COM1", 115200, {})
            self.window._start_suite([{"command": "AT"}])
        send.assert_not_called()
        connect.assert_not_called()
        # Saving logs and editing user files remain accessible.
        self.assertTrue(self.window._cmd_panel.isEnabled())
        self.assertTrue(self.window._auto_panel.isEnabled())

    def test_newer_policy_unlocks_without_reconnecting_or_resuming(self):
        guard = self.window._release_guard
        guard.apply_policy(policy_document(self.policy_key, revoke_all=True))
        guard.apply_policy(policy_document(self.policy_key, sequence=2))
        self.assertFalse(guard.revoked)
        self.assertTrue(self.window._port_selector.isEnabled())
        self.assertTrue(self.window._release_banner.isHidden())
        self.assertFalse(self.window._serial.is_connected)
        self.assertFalse(self.window._runner.is_running)

    def test_invalid_policy_cannot_disable_the_window(self):
        self.assertFalse(self.window._release_guard.apply_policy(b'{}'))
        self.assertFalse(self.window._release_guard.revoked)
        self.assertTrue(self.window._port_selector.isEnabled())

    def test_revocation_keeps_run_disabled_when_switching_or_editing_suites(self):
        from unittest.mock import Mock
        guard = self.window._release_guard
        panel = self.window._auto_panel
        guard.apply_policy(policy_document(self.policy_key, revoke_all=True))
        panel._load({"name": "Edited suite", "steps": [{"command": "AT"}]})
        panel._on_stop()
        self.assertFalse(panel._run_btn.isEnabled())
        run = Mock()
        panel.run_suite_requested.connect(run)
        panel._on_run()
        run.assert_not_called()

    def test_update_notice_does_not_disconnect_or_stop_active_automation(self):
        from unittest.mock import Mock
        self.window._serial._port = Mock(is_open=True)
        port = self.window._serial._port
        port.write.side_effect = lambda data: len(data)
        self.window._runner.run([{"command": "AT+CHECK"}])
        self.window._auto_panel._running = True
        self.window._release_guard.apply_policy(policy_document(self.policy_key, latest_version="1.3.0"))
        self.assertTrue(self.window._runner.is_running)
        self.assertTrue(self.window._serial.is_connected)
        port.close.assert_not_called()
        self.assertFalse(self.window._release_banner.isHidden())
        self.assertIn("An update is available", self.window._release_notice.text())
        self.assertIn("continue using", self.window._release_notice.text())
        self.assertTrue(self.window._port_selector.isEnabled())

    def test_disappearing_usb_port_stops_work_and_returns_to_disconnected_state(self):
        self._connect_fake_modem()
        self.window._serial._port.name = "COM7"
        self.window._runner._running = True
        self.window._run_source = "automation"
        self.window._auto_panel._running = True
        self.window._on_ports_available(["COM7"])
        self.window._on_ports_available([])
        self.assertTrue(self.window._serial.is_connected)
        self.window._on_ports_available([])
        self.assertFalse(self.window._serial.is_connected)
        self.assertFalse(self.window._runner.is_running)
        self.assertFalse(self.window._auto_panel._running)
        self.assertIn("disappeared", self.window._terminal._output.toPlainText())

    def test_write_failure_during_runner_send_stops_without_reentrant_completion(self):
        self._connect_fake_modem()
        self.window._serial._port.write.side_effect = OSError("USB removed")
        self.window._run_source = "automation"
        self.window._runner.run([{"command": "AT"}])
        self.assertFalse(self.window._runner.is_running)
        self.assertFalse(self.window._serial.is_connected)
        self.assertEqual(self.window._runner._results, [])

    def test_manual_cr_only_final_result_releases_modem_for_other_work(self):
        self._connect_fake_modem()
        with patch.object(self.window._serial, "send", return_value=(True, None)):
            self.window._send_command("AT")
        self.assertTrue(self.window._manual_timer.isActive())
        self.window._on_data_received("OK\r")
        self.assertFalse(self.window._manual_timer.isActive())

    def test_required_update_displays_default_message_and_preserves_export_access(self):
        self.window._release_guard.apply_policy(policy_document(
            self.policy_key, versions=[mw.__version__], latest_version="1.3.0", message=""))
        self.assertIn("no longer supported", self.window._release_notice.text())
        self.assertIn("1.3.0", self.window._release_notice.text())
        self.assertFalse(self.window._port_selector.isEnabled())
        self.assertTrue(self.window._release_button.isEnabled())
        self.assertTrue(self.window._auto_panel.isEnabled())

    def test_update_button_opens_only_the_official_release_page(self):
        self.window._release_guard.apply_policy(policy_document(
            self.policy_key, latest_version="1.3.0", update_message="https://example.invalid"))
        with patch.object(mw.QDesktopServices, "openUrl", return_value=True) as open_url:
            self.window._release_button.click()
        self.assertEqual(open_url.call_args.args[0].toString(), mw.__url__ + "/releases")

    def test_newer_policy_hides_update_notice_without_locking_commands(self):
        self.window._release_guard.apply_policy(policy_document(self.policy_key, latest_version="1.3.0"))
        self.window._release_guard.apply_policy(policy_document(self.policy_key, sequence=2))
        self.assertTrue(self.window._release_banner.isHidden())
        self.assertTrue(self.window._port_selector.isEnabled())


if __name__ == "__main__":
    unittest.main()
