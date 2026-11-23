"""
core/serial_manager.py
Cross-platform serial port manager with background reader thread.
Emits Qt signals — safe to connect directly to UI slots.
"""
import time
import codecs
import threading
import serial
import serial.tools.list_ports
from PyQt6.QtCore import QObject, pyqtSignal


# ─── Background reader thread ─────────────────────────────────────────────────

class _SerialWorker(QObject):
    data_received  = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, port_obj: serial.Serial):
        super().__init__()
        self._port    = port_obj
        self._running = True
        self._thread = None

    def start(self):
        # A defective USB driver must not leave a running QThread whose
        # destruction aborts the application. Qt signals still queue UI work.
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def wait(self, timeout_ms):
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout_ms / 1000)
        return not self.isRunning()

    def isRunning(self):
        return self._thread is not None and self._thread.is_alive()

    def run(self):
        try:
            self._read_loop()
        except RuntimeError:
            # The Qt application may already be gone during process shutdown.
            pass
        finally:
            try:
                self.finished.emit()
            except RuntimeError:
                pass

    def _read_loop(self):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while self._running:
            try:
                if self._port and self._port.is_open:
                    waiting = self._port.in_waiting
                    if waiting:
                        raw  = self._port.read(waiting)
                        text = decoder.decode(raw)
                        if text:
                            self.data_received.emit(text)
                    else:
                        time.sleep(0.01)
                else:
                    if self._running:
                        self.error_occurred.emit("The serial port closed unexpectedly.")
                    break
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
        self._generation = 0
        self.line_ending = "CR"
        self.settings = {}

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
    def auto_detect_at_port(operation_allowed=None, baudrate=115200, line_ending="CR", **settings) -> str | None:
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
            s = None
            try:
                s = serial.Serial(port=None, baudrate=baudrate, timeout=0.2, write_timeout=1,
                                  **{k: v for k, v in settings.items() if k not in ("dtr", "rts")})
                s.dtr, s.rts = settings.get("dtr", True), settings.get("rts", True)
                s.port = info["device"]
                s.open()
                ending = {"CR": b"\r", "LF": b"\n", "CRLF": b"\r\n"}[line_ending]
                for probe in (b"ATI" + ending, b"AT" + ending):
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
            finally:
                if s is not None:
                    try:
                        s.close()
                    except Exception:
                        pass
        return None

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self, port: str, baudrate: int = 115200,
                bytesize: int = 8, parity: str = "N",
                stopbits: int = 1, xonxoff: bool = False,
                rtscts: bool = False, dsrdtr: bool = False,
                dtr: bool = True, rts: bool = True, line_ending: str = "CR") -> tuple[bool, str | None]:
        """Open port and start reader thread. Returns (success, error_message)."""
        if not self._operation_allowed():
            return False, "This release is disabled by the maintainer"
        if line_ending not in ("CR", "LF", "CRLF"):
            return False, "Choose CR, LF or CRLF for the command line ending."
        self.disconnect()
        peer = None
        try:
            peer = serial.Serial(
                port=None, baudrate=baudrate, bytesize=bytesize,
                parity=parity, stopbits=stopbits, xonxoff=xonxoff,
                rtscts=rtscts, dsrdtr=dsrdtr, timeout=1, write_timeout=1,
            )
            # Apply control-line preferences before open, rather than pulsing
            # defaults and then changing them after a USB modem is connected.
            peer.dtr, peer.rts, peer.port = dtr, rts, port
            peer.open()
            self._port = peer
            self.line_ending = line_ending
            self.settings = dict(baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits=stopbits,
                                 xonxoff=xonxoff, rtscts=rtscts, dsrdtr=dsrdtr, dtr=dtr, rts=rts,
                                 line_ending=line_ending)
            self._generation += 1
            generation = self._generation
            self._worker = _SerialWorker(peer)
            self._worker.data_received.connect(lambda data: self._receive(generation, data))
            self._worker.error_occurred.connect(lambda message: self._worker_error(generation, message))
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker.start()
            self.connection_changed.emit(True)
            return True, None
        except Exception as exc:
            if peer is not None:
                try:
                    peer.close()
                except Exception:
                    pass
            self._port = None
            return False, str(exc)

    def disconnect(self):
        """Stop reader thread and close port."""
        self._generation += 1  # Ignore queued events from an earlier USB handle.
        worker, self._worker = self._worker, None
        peer, self._port = self._port, None
        if worker:
            worker.stop()
        if peer:
            try:
                peer.cancel_read()
            except (AttributeError, OSError, serial.SerialException):
                pass
            try:
                peer.close()
            except Exception:
                pass
        if worker:
            worker.wait(2000)
            # A slow reader retains itself through its daemon thread until it
            # exits; it cannot abort Qt or deliver data to a newer generation.
        if peer is not None or worker is not None:
            self.connection_changed.emit(False)

    # ── I/O ───────────────────────────────────────────────────────────────────

    def send(self, command: str) -> tuple[bool, str | None]:
        """Send exactly one configured line terminator; payload bytes use send_raw."""
        raw = command.rstrip("\r\n") + {"CR": "\r", "LF": "\n", "CRLF": "\r\n"}[self.line_ending]
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
                message = f"Incomplete serial write: {written}/{len(payload)} bytes"
                self._on_worker_error(message)
                return False, message
            return True, None
        except Exception as exc:
            self._on_worker_error(str(exc))
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

    def _receive(self, generation, data):
        if generation == self._generation and self.is_connected:
            self.data_received.emit(data)

    def _worker_error(self, generation, message):
        if generation == self._generation:
            self._on_worker_error(message)
