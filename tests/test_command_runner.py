import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from unittest.mock import Mock
import yaml

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
from modemdiag.core.command_runner import CommandRunner

APP = QCoreApplication.instance() or QCoreApplication([])


class CommandRunnerTests(unittest.TestCase):
    def setUp(self):
        self.serial = Mock()
        self.serial.send.return_value = (True, None)
        self.runner = CommandRunner(self.serial)
        self.addCleanup(self.runner.stop)
        self.results = []
        self.runner.step_result.connect(lambda i, response, success: self.results.append((i, response, success)))

    def test_error_final_result_cannot_pass_from_earlier_expected_text(self):
        for final in ("ERROR", "+CME ERROR: 3", "+CMS ERROR: 500"):
            with self.subTest(final=final):
                self.runner.run([{"command": "AT+CHECK", "expect": "OK"}])
                self.runner.feed("\r\n+DATA: OK\r\n" + final + "\r\n")
                self.assertFalse(self.results[-1][2])
                self.runner.stop()

    def test_fragmented_response_waits_for_complete_final_result(self):
        self.runner.run([{"command": "AT+CHECK", "expect": "READY"}])
        self.runner.feed("AT+CHECK\r\n+CHECK: READY\r\nO")
        self.assertFalse(self.results)
        self.runner.feed("K\r\n")
        self.assertTrue(self.results[0][2])
        self.assertNotIn("AT+CHECK", self.results[0][1])

    def test_vendor_error_code_cannot_pass_with_ok(self):
        for error in ("ERROR CODE = 3", "ERRCODE = 2"):
            with self.subTest(error=error):
                self.runner.run([{"command": "AT!GPSFIX=1,60,100", "on_failure": "stop"}])
                self.runner.feed(error + "\r\nOK\r\n")
                self.assertFalse(self.results[-1][2])
                self.assertFalse(self.runner.is_running)

    def test_missing_expected_value_fails_even_with_ok(self):
        self.runner.run([{"command": "AT+CHECK", "expect": "READY"}])
        self.runner.feed("\r\n+CHECK: NOT READY YET\r\nOK\r\n")
        # Expectations remain substrings, as documented.
        self.assertTrue(self.results[0][2])
        self.runner.stop()
        self.runner.run([{"command": "AT+CHECK", "expect": "REGISTERED"}])
        self.runner.feed("\r\n+CHECK: SEARCHING\r\nOK\r\n")
        self.assertFalse(self.results[-1][2])

    def test_running_steps_are_a_snapshot(self):
        steps = [{"command": "AT+CHECK", "expect": "READY", "on_failure": "stop"}]
        self.runner.run(steps)
        steps[0]["expect"] = "OTHER"
        steps[0]["command"] = "AT+DIFFERENT"
        self.runner.feed("\r\nREADY\r\nOK\r\n")
        self.assertTrue(self.results[0][2])
        self.assertEqual(self.runner._results[0]["command"], "AT+CHECK")

    def test_unchecked_workflow_steps_are_not_sent(self):
        self.runner.run([{"command": "AT+PAYLOAD", "enabled": False}, {"command": "AT"}])
        self.serial.send.assert_called_once_with("AT")
        self.runner.feed("\r\nOK\r\n")
        self.assertEqual(self.runner._results[0]["command"], "AT")

    def test_stop_invalidates_pending_delayed_steps(self):
        self.runner.run([{"command": "AT+ONE", "delay_ms": 0}, {"command": "AT+TWO"}])
        self.runner.feed("\r\nOK\r\n")
        self.runner.stop()
        APP.processEvents()
        self.serial.send.assert_called_once_with("AT+ONE")

    def test_diagnostic_checks_device_state_instead_of_only_response_prefix(self):
        root = Path(__file__).resolve().parents[1] / "resources/diagnostics/4g_lte.yaml"
        steps = {s['command']: s for s in yaml.safe_load(root.read_text())['steps']}
        for command, response in [('AT+CPIN?', '+CPIN: SIM PIN'),
                                  ('AT+CEREG?', '+CEREG: 1,3'), ('AT+CGATT?', '+CGATT: 0')]:
            with self.subTest(command=command):
                self.runner.run([steps[command]])
                self.runner.feed(response + '\r\nOK\r\n')
                self.assertFalse(self.results[-1][2])
                self.runner.stop()
        for response in ['+CEREG: 0,1', '+CEREG: 2,5,"1234","5678",7']:
            self.runner.run([steps['AT+CEREG?']])
            self.runner.feed(response + '\r\nOK\r\n')
            self.assertTrue(self.results[-1][2])
            self.runner.stop()

    def test_async_completion_requires_both_ok_and_completion_urc(self):
        self.runner.run([{'command':'AT+OPEN', 'wait_for':r'^\+OPEN: 0,0$'}])
        self.runner.feed('OK\r\n')
        self.assertFalse(self.results)
        self.runner.feed('+OPEN: 0,')
        self.assertFalse(self.results)
        self.runner.feed('0\r\n')
        self.assertTrue(self.results[-1][2])

    def test_async_failure_urc_fails_even_after_ok(self):
        self.runner.run([{'command':'AT+OPEN', 'wait_for':r'^\+OPEN: 0,0$',
                          'fail_on':r'^\+OPEN: 0,[1-9]\d*$'}])
        self.runner.feed('OK\r\n+OPEN: 0,3\r\n')
        self.assertFalse(self.results[-1][2])

    def test_payload_waits_for_fragmented_prompt_and_uses_utf8_byte_length(self):
        self.serial.send_raw.return_value = (True,None)
        self.runner.run([{'command':'AT+PUBLISH={length}', 'prompt':'DOWNLOAD', 'payload':'é\n'}])
        self.serial.send.assert_called_once_with('AT+PUBLISH=3')
        self.runner.feed('AT+PUBLISH=3\r\nDOWN')
        self.serial.send_raw.assert_not_called()
        self.runner.feed('LOAD')
        self.serial.send_raw.assert_called_once_with(b'\xc3\xa9\n')
        self.assertFalse(self.results)
        self.runner.feed('\r\nOK\r\n')
        self.assertTrue(self.results[-1][2])

    def test_hex_payload_and_ctrl_z_are_exact_and_sent_only_once(self):
        self.serial.send_raw.return_value = (True,None)
        self.runner.run([{'command':'AT+SEND={length}', 'prompt':'>', 'payload':'00 ff 41',
                          'payload_encoding':'hex', 'payload_ending':'ctrl-z'}])
        self.runner.feed('\r\n>')
        self.runner.feed('\r\n>')
        self.serial.send.assert_called_once_with('AT+SEND=3')
        self.serial.send_raw.assert_called_once_with(b'\x00\xffA\x1a')

    def test_esp_payload_requires_send_ok_instead_of_intermediate_ok(self):
        self.serial.send_raw.return_value = (True,None)
        self.runner.run([{'command':'AT+CIPSEND={length}', 'prompt':'>', 'payload':'hello',
                          'final_ok':'SEND OK', 'expect':'SEND OK'}])
        self.runner.feed('OK\r\n>')
        self.serial.send_raw.assert_called_once_with(b'hello')
        self.runner.feed('\r\nOK\r\n')
        self.assertFalse(self.results)
        self.runner.feed('SEND OK\r\n')
        self.assertTrue(self.results[-1][2])

    def test_invalid_regex_and_payload_do_not_send_commands(self):
        for invalid in [{'expect_regex':'['}, {'prompt':'>','payload':'invalid hex','payload_encoding':'hex'},
                        {'payload':'hello'}, {'timeout_ms':0}]:
            self.runner.run([{'command':'AT+SEND','on_failure':'stop',**invalid}])
            self.assertFalse(self.results[-1][2])
            self.assertIn('INVALID STEP',self.results[-1][1])
        self.serial.send.assert_not_called()

    def test_stop_or_modem_error_before_prompt_never_sends_payload(self):
        self.runner.run([{'command':'AT+SEND','prompt':'>','payload':'hello'}])
        self.runner.feed('ERROR\r\n>')
        self.assertFalse(self.results[-1][2])
        self.serial.send_raw.assert_not_called()
        self.runner.stop()
        self.runner.run([{'command':'AT+SEND','prompt':'>','payload':'hello'}])
        self.runner.stop()
        self.runner.feed('>')
        self.serial.send_raw.assert_not_called()

    def test_bundled_prompt_workflows_complete_with_simulated_serial_peer(self):
        root = Path(__file__).resolve().parents[1] / 'resources/macros'
        for filename in ['example_simcom_mqtt.yaml', 'example_quectel_certificate.yaml', 'example_espressif_tcp.yaml']:
            with self.subTest(template=filename):
                steps = yaml.safe_load((root / filename).read_text())['steps']
                for step in steps:
                    step.update(enabled=True, delay_ms=0)
                    if filename == 'example_quectel_certificate.yaml':
                        step['payload'] = 'public certificate text\né\n'
                serial = Mock()
                runner = CommandRunner(serial)
                loop = QEventLoop()
                finished = []
                runner.suite_finished.connect(lambda ok, result: (finished.append(ok), loop.quit()))
                def send(command):
                    if command.startswith('AT+CNACT='):
                        response = 'OK\r\n+APP PDP: 0,ACTIVE\r\n'
                    elif command == 'AT+SMSTATE?':
                        response = '+SMSTATE: 1\r\nOK\r\n'
                    elif command.startswith('AT+SMPUB=') or command.startswith('AT+CIPSEND='):
                        response = '\r\n>'
                    elif command.startswith('AT+QFUPL='):
                        response = '\r\nCONNECT\r\n'
                    else:
                        response = 'OK\r\n'
                    QTimer.singleShot(0, lambda: runner.feed(response))
                    return True, None
                def send_raw(payload):
                    response = ('SEND OK\r\n' if filename == 'example_espressif_tcp.yaml' else
                                '+QFUPL: 28,0000\r\nOK\r\n' if filename == 'example_quectel_certificate.yaml' else 'OK\r\n')
                    QTimer.singleShot(0, lambda: runner.feed(response))
                    return True, None
                serial.send.side_effect = send
                serial.send_raw.side_effect = send_raw
                timeout = QTimer()
                timeout.setSingleShot(True)
                timeout.timeout.connect(loop.quit)
                timeout.start(3000)
                runner.run(steps)
                loop.exec()
                timeout.stop()
                runner.stop()
                self.assertEqual(finished, [True], runner._results)
                payload_step = next(s for s in steps if 'payload' in s)
                serial.send_raw.assert_called_once_with(payload_step['payload'].encode('utf-8'))
                runner.deleteLater()


if __name__ == "__main__":
    unittest.main()
