import unittest
from unittest.mock import Mock, patch

from modemdiag.core.serial_manager import SerialManager


class SerialReleaseLockTests(unittest.TestCase):
    def test_raw_payload_preserves_exact_bytes_without_newline(self):
        import serial
        manager = SerialManager()
        manager._port = serial.serial_for_url('loop://', timeout=0.2)
        self.addCleanup(manager._port.close)
        payload = b'\x00\xffcertificate\n\xc3\xa9'
        self.assertEqual(manager.send_raw(payload), (True,None))
        self.assertEqual(manager._port.read(len(payload)+2), payload)

    def test_raw_payload_cannot_bypass_revocation_or_partial_write_detection(self):
        manager = SerialManager(operation_allowed=lambda: False)
        manager._port = Mock(is_open=True)
        self.assertFalse(manager.send_raw(b'secret')[0])
        manager._port.write.assert_not_called()
        manager._operation_allowed = lambda: True
        manager._port.write.return_value = 2
        self.assertFalse(manager.send_raw(b'hello')[0])

    def test_revoked_release_cannot_open_ports(self):
        manager = SerialManager(operation_allowed=lambda: False)
        with patch("modemdiag.core.serial_manager.serial.Serial") as port:
            success, message = manager.connect("COM1")
        self.assertFalse(success)
        self.assertIn("disabled", message)
        port.assert_not_called()

    def test_revoked_release_cannot_write_even_if_port_is_open(self):
        manager = SerialManager(operation_allowed=lambda: False)
        manager._port = Mock(is_open=True)
        self.assertFalse(manager.send("AT")[0])
        manager._port.write.assert_not_called()

    def test_revoked_release_cannot_probe_ports(self):
        with patch.object(SerialManager, "list_ports") as ports:
            self.assertIsNone(SerialManager.auto_detect_at_port(lambda: False))
        ports.assert_not_called()

    def test_revocation_during_probe_stops_before_writing(self):
        with patch.object(SerialManager, "list_ports", return_value=[{"device": "COM1"}]), \
                patch("modemdiag.core.serial_manager.serial.Serial") as port:
            allowed = Mock(side_effect=[True, True, False])
            self.assertIsNone(SerialManager.auto_detect_at_port(allowed))
        port.return_value.__enter__.return_value.write.assert_not_called()

    def test_noncellular_at_module_can_be_detected_without_ati_support(self):
        with patch.object(SerialManager, 'list_ports', return_value=[{'device':'COM7'}]), \
                patch('modemdiag.core.serial_manager.serial.Serial') as port, \
                patch('modemdiag.core.serial_manager.time.sleep'):
            peer = port.return_value.__enter__.return_value
            peer.in_waiting = 7
            peer.read.side_effect = [b'ERROR\r\n', b'OK\r\n']
            self.assertEqual(SerialManager.auto_detect_at_port(), 'COM7')
            self.assertEqual([c.args[0] for c in peer.write.call_args_list], [b'ATI\r\n', b'AT\r\n'])


if __name__ == "__main__":
    unittest.main()
