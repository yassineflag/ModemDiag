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

    def __init__(self, log_dir: str = "logs"):
        self._dir = log_dir
        self._fh  = None
        os.makedirs(log_dir, exist_ok=True)

    # ── Session control ───────────────────────────────────────────────────────

    def start(self, port: str, baudrate: int) -> str:
        """Open a new log file; returns the file path."""
        now   = datetime.datetime.now()
        fname = now.strftime("session_%Y%m%d_%H%M%S.log")
        path  = os.path.join(self._dir, fname)
        self._fh = open(path, "w", encoding="utf-8")
        self._write("# ModemDiag Session Log")
        self._write(f"# Date : {now.isoformat()}")
        self._write(f"# Port : {port}   Baud : {baudrate}")
        self._write("─" * 60)
        return path

    def stop(self):
        if self._fh:
            self._write("# Session ended")
            self._fh.close()
            self._fh = None

    # ── Logging methods ───────────────────────────────────────────────────────

    def tx(self, command: str):
        self._write(f"TX  {command.strip()}")

    def rx(self, data: str):
        for line in data.strip().splitlines():
            if stripped := line.strip():
                self._write(f"RX  {stripped}")

    def info(self, message: str):
        self._write(f"INF {message}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write(self, text: str):
        if not self._fh:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._fh.write(f"[{ts}] {text}\n")
        self._fh.flush()

    @property
    def active(self) -> bool:
        return self._fh is not None
