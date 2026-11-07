import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox, QDialog, QTextBrowser, QPushButton
from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtGui import QColor
from modemdiag.ui.theme import apply_theme, set_tooltip
from modemdiag.ui import command_panel as cp, automation_panel as ap, main_window as mw
from modemdiag.ui.port_selector import PortSelectorWidget
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

    def test_help_and_configuration_actions_are_available(self):
        menus = {a.text(): a.menu() for a in self.window.menuBar().actions()}
        self.assertIn("How to Use…", [a.text() for a in menus["Help"].actions()])
        self.assertIn("Restore default command library…", [a.text() for a in menus["Settings"].actions()])
        self.assertIn(self.window._cmd_panel.undo_action, menus["Edit"].actions())
        with patch.object(QMessageBox, "information") as information:
            self.window._show_how_to_use()
            self.window._show_config_location()
        self.assertEqual(information.call_count, 2)

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
        self.window.findChild(QTabWidget).setCurrentIndex(1)
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
        self.window._runner.run([{"command": "AT+CHECK"}])
        self.window._auto_panel._running = True
        self.window._release_guard.apply_policy(policy_document(self.policy_key, latest_version="1.2.0"))
        self.assertTrue(self.window._runner.is_running)
        self.assertTrue(self.window._serial.is_connected)
        port.close.assert_not_called()
        self.assertFalse(self.window._release_banner.isHidden())
        self.assertIn("An update is available", self.window._release_notice.text())
        self.assertIn("continue using", self.window._release_notice.text())
        self.assertTrue(self.window._port_selector.isEnabled())

    def test_required_update_displays_default_message_and_preserves_export_access(self):
        self.window._release_guard.apply_policy(policy_document(
            self.policy_key, versions=[mw.__version__], latest_version="1.2.0", message=""))
        self.assertIn("no longer supported", self.window._release_notice.text())
        self.assertIn("1.2.0", self.window._release_notice.text())
        self.assertFalse(self.window._port_selector.isEnabled())
        self.assertTrue(self.window._release_button.isEnabled())
        self.assertTrue(self.window._auto_panel.isEnabled())

    def test_update_button_opens_only_the_official_release_page(self):
        self.window._release_guard.apply_policy(policy_document(
            self.policy_key, latest_version="1.2.0", update_message="https://example.invalid"))
        with patch.object(mw.QDesktopServices, "openUrl", return_value=True) as open_url:
            self.window._release_button.click()
        self.assertEqual(open_url.call_args.args[0].toString(), mw.__url__ + "/releases")

    def test_newer_policy_hides_update_notice_without_locking_commands(self):
        self.window._release_guard.apply_policy(policy_document(self.policy_key, latest_version="1.2.0"))
        self.window._release_guard.apply_policy(policy_document(self.policy_key, sequence=2))
        self.assertTrue(self.window._release_banner.isHidden())
        self.assertTrue(self.window._port_selector.isEnabled())


if __name__ == "__main__":
    unittest.main()
