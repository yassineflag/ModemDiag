"""
core/command_runner.py
Sequential AT command executor for macros and diagnostic suites.
"""
from copy import deepcopy
import re

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from modemdiag.core.at_parser import split_lines, LineType

class CommandRunner(QObject):
    """
    Runs an ordered list of AT command steps one-by-one.
    Each step dict may contain:
        command     (str)  – the AT command to send
        enabled     (bool) – false keeps a step in the workflow without sending it
        description (str)  – human-readable label
        expect      (str)  – substring expected in response  (default "OK")
        expect_regex(str) – optional regex instead of substring matching
        wait_for    (str) – optional completion-URC regex, including after OK
        fail_on     (str) – optional failure-URC regex
        prompt      (str) – prompt to wait for before sending payload
        payload     (str) – exact UTF-8 text or hex bytes; {length} in command is byte count
        payload_encoding – "utf-8" (default) or "hex"
        payload_ending   – "none" (default), "ctrl-z", or "crlf"
        final_ok    (str) – successful final line (default "OK", e.g. ESP-AT "SEND OK")
        timeout_ms  (int)  – ms to wait for response         (default 5000)
        delay_ms    (int)  – ms to wait before next step     (default 300)
        on_failure  (str)  – "continue" | "stop" | "retry"   (default "continue")
                             "retry" re-sends the step once before moving on

    Signals
    -------
    step_started(index, command, description)
    step_result (index, response, success)
    suite_finished(all_passed, results_list)
    """

    step_started   = pyqtSignal(int, str, str)
    step_result    = pyqtSignal(int, str, bool)
    suite_finished = pyqtSignal(bool, list)

    def __init__(self, serial_manager, parent=None):
        super().__init__(parent)
        self._serial  = serial_manager
        self._steps:   list = []
        self._idx:     int  = 0
        self._running: bool = False
        self._waiting: bool = False   # True only between send and response
        self._retried: bool = False
        self._buf:     str  = ""
        self._results: list = []
        self._gen:     int  = 0       # invalidates delayed steps of a stopped run
        self._payload_sent = False
        self._payload = b""

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(self, steps: list):
        """Start a new sequence. Silently ignored if already running."""
        if self._running:
            return
        self._gen    += 1
        self._steps   = deepcopy([step for step in steps if step.get("enabled", True)])
        self._idx     = 0
        self._results = []
        self._retried = False
        self._running = True
        self._exec_step()

    def stop(self):
        """Abort the current sequence."""
        self._gen    += 1
        self._running = False
        self._waiting = False
        self._timer.stop()
        self._payload = b""

    def feed(self, data: str):
        """Feed incoming serial data; used to detect expected responses."""
        if self._waiting:
            self._buf += data
            self._check_response()

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Internal ───────────────────────────────────────────────────────────────

    def _exec_step(self):
        if not self._running or self._idx >= len(self._steps):
            self._finish()
            return

        step = self._steps[self._idx]
        cmd  = step.get("command", "").strip()
        desc = step.get("description", "")
        try:
            timeout = int(step.get("timeout_ms", 5000))
            if timeout <= 0 or int(step.get("delay_ms", 300)) < 0:
                raise ValueError("Timeout must be positive and delay must be nonnegative")
            for key in ("expect_regex", "wait_for", "fail_on"):
                if step.get(key):
                    re.compile(step[key], re.MULTILINE)
            encoding = step.get("payload_encoding", "utf-8")
            if encoding not in ("utf-8", "hex"):
                raise ValueError("Payload encoding must be utf-8 or hex")
            self._payload = (bytes.fromhex(step.get("payload", "")) if encoding == "hex"
                             else step.get("payload", "").encode("utf-8"))
            ending = {"none": b"", "ctrl-z": b"\x1a", "crlf": b"\r\n"}[step.get("payload_ending", "none")]
            cmd = cmd.replace("{length}", str(len(self._payload)))
            self._payload += ending
            if "payload" in step and not step.get("prompt"):
                raise ValueError("Payload steps require a prompt")
            if step.get("prompt") and "payload" not in step:
                raise ValueError("Prompt steps require a payload")
        except (ValueError, TypeError, KeyError, AttributeError, re.error) as exc:
            self.step_started.emit(self._idx, cmd, desc)
            self._complete(f"INVALID STEP: {exc}", False)
            return
        self._active_command = cmd
        self._payload_sent = False
        self.step_started.emit(self._idx, cmd, desc)
        self._buf = ""
        self._waiting = True
        self._timer.start(timeout)
        ok, err = self._serial.send(cmd)
        if not ok:
            self._timer.stop()
            self._complete(f"SEND ERROR: {err}", False)

    def _check_response(self):
        step   = self._steps[self._idx]
        expect = str(step.get("expect", "OK"))
        cmd    = self._active_command.upper()

        # Only complete lines count — a chunk may end mid-line.
        complete = self._buf.rsplit("\n", 1)[0] if "\n" in self._buf else ""
        # Drop the ATE1 echo so e.g. "AT+QIGETERROR" isn't read as an ERROR.
        lines = [(s, k) for s, k in split_lines(complete) if s.upper() != cmd]
        # Some Sierra firmware reports a failed GNSS operation followed by OK.
        if any(re.match(r"^(?:ERROR\s*CODE|ERRCODE)\s*=", line, re.I) for line, _ in lines):
            self._timer.stop()
            self._complete("\n".join(line for line, _ in lines), False)
            return
        response = "\n".join(line for line, _ in lines)
        if any(kind in (LineType.ERROR, LineType.CME_ERROR, LineType.CMS_ERROR) for _, kind in lines):
            self._timer.stop()
            self._complete(response, False)
            return
        if step.get("fail_on") and re.search(step["fail_on"], response, re.MULTILINE):
            self._timer.stop()
            self._complete(response, False)
            return
        if step.get("prompt") and not self._payload_sent:
            # Prompts are often not terminated by a newline; ignore tokens in command echoes.
            prompt = re.search(r"(?:^|[\r\n])[ \t]*" + re.escape(step["prompt"]), self._buf)
            if prompt:
                self._buf = self._buf[prompt.end():]
                self._payload_sent = True
                ok, err = self._serial.send_raw(self._payload)
                if not ok:
                    self._timer.stop()
                    self._complete(f"PAYLOAD ERROR: {err}", False)
                elif self._waiting:
                    self._check_response()
            return
        final_ok = step.get("final_ok", "OK")
        if not any(line == final_ok for line, _ in lines):
            return

        # An error final result must not pass just because earlier data contains
        # the expected substring (for example a payload containing "OK").
        final_index = next(i for i, (line, _) in enumerate(lines) if line == final_ok)
        if not step.get("wait_for"):
            lines = lines[:final_index + 1]
        response = "\n".join(s for s, _ in lines)
        if step.get("wait_for") and not re.search(step["wait_for"], response, re.MULTILINE):
            return
        matches = (bool(re.search(step["expect_regex"], response, re.MULTILINE))
                   if step.get("expect_regex") else expect in response)
        self._timer.stop()
        self._complete(response, matches)

    def _on_timeout(self):
        if self._waiting:
            self._complete("TIMEOUT", False)

    def _complete(self, response: str, success: bool):
        self._waiting = False
        step   = self._steps[self._idx]
        policy = step.get("on_failure", "continue")

        if not success and policy == "retry" and not self._retried:
            self._retried = True
            self._schedule(step.get("delay_ms", 300))
            return

        self.step_result.emit(self._idx, response, success)
        self._results.append({
            "index":    self._idx,
            "command":  step.get("command", ""),
            "response": response,
            "success":  success,
        })

        if not success and policy == "stop":
            self._finish()
            return

        self._idx    += 1
        self._retried = False
        self._schedule(step.get("delay_ms", 300))

    def _schedule(self, delay_ms):
        gen = self._gen
        try:
            delay = max(0, int(delay_ms))
        except (ValueError, TypeError):
            delay = 300
        QTimer.singleShot(delay,
                          lambda: gen == self._gen and self._exec_step())

    def _finish(self):
        self._running = False
        self._waiting = False
        all_ok = all(r["success"] for r in self._results)
        self.suite_finished.emit(all_ok, list(self._results))
