"""
core/logger.py
Session Logger — writes all TX/RX to a timestamped log file.
"""
import os
import datetime


class SessionLogger:
    """
    Writes a timestamped plain-text session log to logs/<session>.log.
    Call start() after connect, stop() after disconnect.
    """

    def __init__(self, log_dir: str = "logs", on_error=None):
        self._dir = log_dir
        self._fh  = None
        self._on_error = on_error or (lambda message: None)
        self.last_error = ""

    # ── Session control ───────────────────────────────────────────────────────

    def start(self, port: str, baudrate: int) -> str:
        """Open a new log file; returns the file path."""
        self.stop()
        self.last_error = ""
        now   = datetime.datetime.now()
        fname = now.strftime("session_%Y%m%d_%H%M%S_%f.log")
        path  = os.path.join(self._dir, fname)
        try:
            os.makedirs(self._dir, exist_ok=True)
            self._fh = open(path, "x", encoding="utf-8")
        except OSError as exc:
            self._fail(exc)
            return ""
        self._write("# ModemDiag Session Log")
        self._write(f"# Date : {now.isoformat()}")
        self._write(f"# Port : {port}   Baud : {baudrate}")
        self._write("─" * 60)
        return path if self.active else ""

    def stop(self):
        if self._fh:
            self._write("# Session ended")
            if self._fh:
                stream, self._fh = self._fh, None
                try:
                    stream.close()
                except OSError as exc:
                    self._fail(exc)

    # ── Logging methods ───────────────────────────────────────────────────────

    def tx(self, command: str):
        self._write(f"TX  {command.strip()}")

    def rx(self, data: str):
        # Preserve chunk boundaries, whitespace and unfinished lines, including
        # the last bytes received before an unexpected disconnect or crash.
        self._write(f"RX  {data!r}")

    def info(self, message: str):
        self._write(f"INF {message}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write(self, text: str):
        if not self._fh:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            self._fh.write(f"[{ts}] {text}\n")
            self._fh.flush()  # Persist during execution, independent of Save Log.
        except OSError as exc:
            self._fail(exc)

    def _fail(self, exc):
        stream, self._fh = self._fh, None
        if stream:
            try:
                stream.close()
            except OSError:
                pass
        self.last_error = str(exc)
        self._on_error(self.last_error)

    @property
    def active(self) -> bool:
        return self._fh is not None
