import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from modemdiag.core.logger import SessionLogger


class SessionLoggerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.errors = []
        self.logger = SessionLogger(str(self.root), on_error=self.errors.append)
        self.addCleanup(self.logger.stop)

    def test_records_are_readable_while_logging_is_still_active(self):
        path = Path(self.logger.start("COM4", 115200))
        self.logger.tx("AT+CSQ")
        self.logger.rx("\r\n+CS")
        self.logger.rx("Q: 23,99\r\nOK\r\n ")
        self.logger.info("step 1: PASS")
        content = path.read_text()
        self.assertTrue(self.logger.active)
        self.assertIn("TX  AT+CSQ", content)
        self.assertIn(repr("\r\n+CS"), content)
        self.assertIn(repr("Q: 23,99\r\nOK\r\n "), content)
        self.assertIn("step 1: PASS", content)
        self.assertNotIn("Session ended", content)

    def test_new_sessions_never_truncate_the_previous_log(self):
        first = Path(self.logger.start("COM4", 115200))
        self.logger.tx("AT+FIRST")
        second = Path(self.logger.start("COM4", 115200))
        self.assertNotEqual(first, second)
        self.assertIn("AT+FIRST", first.read_text())
        self.assertIn("Session ended", first.read_text())
        self.assertNotIn("AT+FIRST", second.read_text())

    def test_open_failure_is_reported_and_can_be_retried(self):
        with patch("builtins.open", side_effect=PermissionError("read only")):
            self.assertEqual(self.logger.start("COM4", 115200), "")
        self.assertFalse(self.logger.active)
        self.assertEqual(self.errors, ["read only"])
        self.assertTrue(self.logger.start("COM4", 115200))
        self.assertTrue(self.logger.active)
        self.assertEqual(self.logger.last_error, "")

    def test_disk_write_failure_does_not_escape_into_modem_execution(self):
        stream = Mock()
        stream.write.side_effect = OSError("disk full")
        self.logger._fh = stream
        self.logger.rx("OK\r\n")
        self.assertFalse(self.logger.active)
        self.assertEqual(self.errors, ["disk full"])
        stream.close.assert_called_once()
        self.logger.tx("AT")  # Continued execution while logging is unavailable.

    def test_abrupt_process_exit_preserves_already_received_traffic(self):
        script = """
import os, sys
from modemdiag.core.logger import SessionLogger
logger = SessionLogger(sys.argv[1])
logger.start('COM4', 115200)
logger.tx('AT+CSQ')
logger.rx('+CSQ: 23,99\\r\\nOK\\r\\n')
os._exit(7)
"""
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        process = subprocess.run([sys.executable, "-c", script, str(self.root)], env=environment, check=False)
        self.assertEqual(process.returncode, 7)
        content = next(self.root.glob("*.log")).read_text()
        self.assertIn("AT+CSQ", content)
        self.assertIn("+CSQ: 23,99", content)
        self.assertNotIn("Session ended", content)
