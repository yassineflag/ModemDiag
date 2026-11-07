"""
core/serial_manager.py
Cross-platform serial port manager with background reader thread.
Emits Qt signals — safe to connect directly to UI slots.
"""
import time
import serial
import serial.tools.list_ports
from PyQt6.QtCore import QObject, QThread, pyqtSignal


# ─── Background reader thread ─────────────────────────────────────────────────

class _SerialWorker(QThread):
    data_received  = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, port_obj: serial.Serial):
        super().__init__()
        self._port    = port_obj
        self._running = True

    def run(self):
        while self._running:
            try:
                if self._port and self._port.is_open:
                    waiting = self._port.in_waiting
                    if waiting:
                        raw  = self._port.read(waiting)
                        text = raw.decode("utf-8", errors="replace")
                        self.data_received.emit(text)
                    else:
                        self.msleep(10)
                else:
                    self.msleep(50)
            except serial.SerialException as exc:
                self.error_occurred.emit(str(exc))
                break
            except Exception as exc:
                self.error_occurred.emit(str(exc))
                break

    def stop(self):
        self._running = False


# ─── Public manager ───────────────────────────────────────────────────────────

class SerialManager(QObject):
    """
    Thread-safe serial port manager.

    Signals
    -------
    data_received(str)      – new data from modem (emitted on main thread)
    error_occurred(str)     – serial error; port is closed after this
    connection_changed(bool)– True = connected, False = disconnected
    """

    data_received      = pyqtSignal(str)
    error_occurred     = pyqtSignal(str)
    connection_changed = pyqtSignal(bool)

    def __init__(self, parent=None, operation_allowed=None):
        super().__init__(parent)
        self._operation_allowed = operation_allowed or (lambda: True)
        self._port:   serial.Serial | None = None
        self._worker: _SerialWorker | None = None

    # ── Port discovery ────────────────────────────────────────────────────────

    @staticmethod
    def list_ports() -> list[dict]:
        """Return sorted list of available serial ports with metadata."""
        result = []
        for p in serial.tools.list_ports.comports():
            result.append({
                "device":       p.device,
                "description":  p.description  or p.device,
                "hwid":         p.hwid          or "",
                "vid":          p.vid,
                "pid":          p.pid,
                "manufacturer": p.manufacturer  or "",
                "product":      p.product       or "",
            })
        return sorted(result, key=lambda x: x["device"])

    @staticmethod
    def auto_detect_at_port(operation_allowed=None) -> str | None:
        """
        Probe available ports with ATI, then AT for non-cellular AT modules.
        Returns the first port whose response looks like a modem.
        """
        allowed = operation_allowed or (lambda: True)
        if not allowed():
            return None
        candidates = SerialManager.list_ports()
        keywords   = ("OK", "QUECTEL", "SIERRA", "FIBOCOM",
                       "UBLOX", "U-BLOX", "TELIT", "SIMCOM", "NORDIC", "ESP-AT",
                       "MANUFACTURER", "MODEL", "REVISION")
        for info in candidates:
            if not allowed():
                return None
            try:
                with serial.Serial(info["device"], 115200, timeout=1) as s:
                    for probe in (b"ATI\r\n", b"AT\r\n"):
                        if not allowed():
                            return None
                        s.write(probe)
                        time.sleep(0.6)
                        if s.in_waiting:
                            resp = s.read(s.in_waiting).decode("utf-8", errors="replace").upper()
                            if any(k in resp for k in keywords):
                                return info["device"]
            except Exception:
                continue
        return None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self, port: str, baudrate: int = 115200,
                bytesize: int = 8, parity: str = "N",
                stopbits: int = 1, xonxoff: bool = False,
                rtscts: bool = False, dsrdtr: bool = False) -> tuple[bool, str | None]:
        """Open port and start reader thread. Returns (success, error_message)."""
        if not self._operation_allowed():
            return False, "This release is disabled by the maintainer"
        try:
            self._port = serial.Serial(
                port=port, baudrate=baudrate, bytesize=bytesize,
                parity=parity, stopbits=stopbits, xonxoff=xonxoff,
                rtscts=rtscts, dsrdtr=dsrdtr, timeout=1, write_timeout=1,
            )
            self._worker = _SerialWorker(self._port)
            self._worker.data_received.connect(self.data_received)       # re-emit (queued)
            self._worker.error_occurred.connect(self._on_worker_error)
            self._worker.start()
            self.connection_changed.emit(True)
            return True, None
        except Exception as exc:
            self._port = None
            return False, str(exc)

    def disconnect(self):
        """Stop reader thread and close port."""
        if self._worker:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None
        if self._port and self._port.is_open:
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None
        self.connection_changed.emit(False)

    # ── I/O ───────────────────────────────────────────────────────────────────

    def send(self, command: str) -> tuple[bool, str | None]:
        """Send an AT command. Appends \\r\\n if missing."""
        raw = command if command.endswith("\r\n") else command + "\r\n"
        return self.send_raw(raw.encode("utf-8"))

    def send_raw(self, payload: bytes) -> tuple[bool, str | None]:
        """Write exact payload bytes without appending a command terminator."""
        if not self._operation_allowed():
            return False, "This release is disabled by the maintainer"
        if not self.is_connected:
            return False, "Not connected"
        try:
            written = self._port.write(payload)
            if written != len(payload):
                return False, f"Incomplete serial write: {written}/{len(payload)} bytes"
            return True, None
        except Exception as exc:
            return False, str(exc)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._port is not None and self._port.is_open

    @property
    def port_name(self) -> str:
        return self._port.name if self._port else ""

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_worker_error(self, msg: str):
        self.error_occurred.emit(msg)
        self.disconnect()
