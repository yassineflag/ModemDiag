import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from modemdiag.core.serial_manager import SerialManager, _SerialWorker
from modemdiag.core.command_runner import CommandRunner
from modemdiag.ui.port_selector import PortSelectorWidget
from modemdiag.ui.terminal_panel import TerminalPanel

APP = QApplication.instance() or QApplication([])


class SerialCompatibilityTests(unittest.TestCase):
    def test_command_endings_are_exact_and_payloads_are_unchanged(self):
        manager = SerialManager()
        manager._port = Mock(is_open=True)
        manager._port.write.side_effect = lambda data: len(data)
        for ending, expected in (("CR", b"AT\r"), ("LF", b"AT\n"), ("CRLF", b"AT\r\n")):
            manager.line_ending = ending
            for command in ("AT", "AT\r", "AT\r\n"):
                self.assertTrue(manager.send(command)[0])
                self.assertEqual(manager._port.write.call_args.args[0], expected)
        payload = b"\x00\xffhello\r\n"
        self.assertTrue(manager.send_raw(payload)[0])
        self.assertEqual(manager._port.write.call_args.args[0], payload)

    def test_control_lines_are_set_before_open_and_close_before_reopen(self):
        manager = SerialManager()
        self.addCleanup(manager.disconnect)
        peer = Mock(is_open=True)
        opened = []
        peer.open.side_effect = lambda: opened.append((peer.port, peer.dtr, peer.rts))
        with patch("modemdiag.core.serial_manager.serial.Serial", return_value=peer) as factory, \
                patch.object(_SerialWorker, "start"):
            self.assertTrue(manager.connect("COM8", dtr=False, rts=False, line_ending="CRLF")[0])
        self.assertIsNone(factory.call_args.kwargs["port"])
        self.assertEqual(opened, [("COM8", False, False)])
        self.assertEqual(manager.line_ending, "CRLF")
        manager.disconnect()
        peer.close.assert_called_once()

    def test_late_events_from_old_usb_session_do_not_disconnect_or_pollute_new_session(self):
        manager = SerialManager()
        self.addCleanup(manager.disconnect)
        received, errors = [], []
        manager.data_received.connect(received.append)
        manager.error_occurred.connect(errors.append)
        with patch("modemdiag.core.serial_manager.serial.Serial", side_effect=[Mock(is_open=True), Mock(is_open=True)]), \
                patch.object(_SerialWorker, "start"):
            self.assertTrue(manager.connect("COM8")[0])
            old = manager._generation
            self.assertTrue(manager.connect("COM9")[0])
            manager._receive(old, "stale response\r\n")
            manager._worker_error(old, "old USB error")
            manager._receive(manager._generation, "OK\r")
        self.assertEqual(received, ["OK\r"])
        self.assertEqual(errors, [])
        self.assertTrue(manager.is_connected)

    def test_write_error_and_partial_write_release_the_dead_handle(self):
        for error in (OSError("device removed"), None):
            with self.subTest(error=error):
                manager = SerialManager()
                peer = Mock(is_open=True)
                manager._port = peer
                errors, states = [], []
                manager.error_occurred.connect(errors.append)
                manager.connection_changed.connect(states.append)
                peer.write.side_effect = error
                peer.write.return_value = 1
                self.assertFalse(manager.send("AT")[0])
                self.assertFalse(manager.is_connected)
                self.assertEqual(states, [False])
                self.assertEqual(len(errors), 1)
                peer.close.assert_called_once()

    def test_cr_only_response_completes_runner_without_waiting_for_lf(self):
        serial = Mock()
        serial.send.return_value = (True, None)
        runner = CommandRunner(serial)
        self.addCleanup(runner.stop)
        results = []
        runner.step_result.connect(lambda *result: results.append(result))
        runner.run([{"command": "AT+CFUN?", "expect": "+CFUN: 1"}])
        runner.feed("AT+CF")
        runner.feed("UN?\r+CFUN: 1\rO")
        self.assertFalse(results)
        runner.feed("K\r")
        self.assertTrue(results[0][2])
        self.assertNotIn("AT+CFUN?", results[0][1])

    def test_reader_preserves_utf8_characters_split_across_usb_reads(self):
        peer = Mock(is_open=True, in_waiting=1)
        peer.read.side_effect = [b"\xc3", b"\xa9"]
        worker = _SerialWorker(peer)
        self.addCleanup(worker.deleteLater)
        received = []
        worker.data_received.connect(lambda text: (received.append(text), worker.stop()))
        worker.run()
        self.assertEqual(received, ["é"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux pseudo-terminal integration test")
    def test_usb_removal_simulation_closes_connection(self):
        import pty
        master, slave = pty.openpty()
        self.addCleanup(os.close, slave)
        manager = SerialManager()
        self.addCleanup(manager.disconnect)
        success, error = manager.connect(os.ttyname(slave))
        self.assertTrue(success, error)
        errors = []
        loop = QEventLoop()
        manager.error_occurred.connect(lambda error: (errors.append(error), loop.quit()))
        os.close(master)
        QTimer.singleShot(1500, loop.quit)
        loop.exec()
        self.assertTrue(errors)
        self.assertFalse(manager.is_connected)


class TerminalStreamTests(unittest.TestCase):
    def setUp(self):
        self.panel = TerminalPanel()
        self.addCleanup(self.panel.deleteLater)

    def test_fragments_and_cr_only_lines_are_shown_once(self):
        for chunk in ("+CF", "UN: 1\rO", "K\r", "\n"):
            self.panel.append_rx_raw(chunk)
        output = self.panel._output.toPlainText()
        self.assertEqual(output.count("+CFUN: 1"), 1)
        self.assertEqual(output.count("OK"), 1)
        self.assertNotIn("+CF\n", output)

    def test_prompt_is_immediately_visible_and_info_survives_partial_replacement(self):
        self.panel.append_rx_raw(">")
        self.assertIn(">", self.panel._output.toPlainText())
        self.panel.append_info("Connection note")
        self.panel.append_rx_raw(" hello\r\nOK\r\n")
        output = self.panel._output.toPlainText()
        self.assertIn("Connection note", output)
        self.assertEqual(output.count("> hello"), 1)

    def test_reconnect_does_not_join_old_partial_data_to_the_new_response(self):
        self.panel.append_rx_raw("old unfinished")
        self.panel.reset_rx_state()
        self.panel.append_rx_raw("OK\r\n")
        self.assertNotIn("old unfinishedOK", self.panel._output.toPlainText())


class PortSelectionTests(unittest.TestCase):
    def setUp(self):
        with patch.object(PortSelectorWidget, "_start_detector"):
            self.selector = PortSelectorWidget()
        self.addCleanup(self.selector.deleteLater)
        self.addCleanup(self.selector.shutdown)

    def test_manually_typed_port_never_uses_previous_item_data(self):
        self.selector._port_combo.addItem("COM3 [AT]", "COM3")
        self.selector._port_combo.setEditText("COM8")
        self.assertEqual(self.selector.selected_port(), "COM8")

    def test_selected_port_survives_polling_and_classification(self):
        from PyQt6.QtTest import QTest
        self.selector._usb_only = False
        self.selector._port_combo.addItem("COM3", "COM3")
        edit = self.selector._port_combo.lineEdit()
        edit.selectAll()
        QTest.keyClicks(edit, "COM8")
        self.selector._on_all_ports(["COM3", "COM8"])
        self.selector._on_ports_classified({"at": "COM3"})
        self.assertEqual(self.selector.selected_port(), "COM8")

    def test_auto_detect_does_not_allow_a_parallel_connection(self):
        self.selector._port_combo.addItem("COM3", "COM3")
        self.selector._probing = True
        connected = Mock()
        self.selector.connect_requested.connect(connected)
        self.selector._on_connect_clicked()
        connected.assert_not_called()

    def test_auto_detect_choice_is_not_replaced_by_a_later_classification(self):
        self.selector._port_combo.addItem("COM3", "COM3")
        self.selector._port_combo.addItem("COM8", "COM8")
        self.selector._on_auto_done((self.selector._probe_generation, "COM8"))
        self.selector._on_ports_classified({"at": "COM3"})
        self.assertEqual(self.selector.selected_port(), "COM8")
