"""
ui/port_selector.py
Connection bar — smart port detection using PortDetector (sysfs / registry),
with auto-select of AT port and full manual override via editable combo.
"""
from modemdiag.ui.theme import set_style, set_tooltip
import sys
import threading
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QFrame, QToolButton, QCheckBox, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer


class StatusLed(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self.set_connected(False)

    def set_connected(self, connected: bool):
        c = "#3fb950" if connected else "#484f58"
        b = "#26a641" if connected else "#30363d"
        set_style(self, f"background-color:{c}; border-radius:6px; border:1px solid {b};")


class PortSelectorWidget(QWidget):
    """
    Emits:
        connect_requested(port, baudrate, params_dict)
        disconnect_requested()
    """
    connect_requested    = pyqtSignal(str, int, dict)
    disconnect_requested = pyqtSignal()
    # Internal: carries the auto-detect result from the worker thread back to
    # the GUI thread (a queued signal — QTimer can't be used from a plain thread)
    _auto_detect_done    = pyqtSignal(object)

    BAUD_RATES   = ["9600", "19200", "38400", "57600", "115200",
                    "230400", "460800", "921600"]
    FLOW_OPTIONS = {
        "None":     {"xonxoff": False, "rtscts": False, "dsrdtr": False},
        "RTS/CTS":  {"xonxoff": False, "rtscts": True,  "dsrdtr": False},
        "XON/XOFF": {"xonxoff": True,  "rtscts": False, "dsrdtr": False},
        "DSR/DTR":  {"xonxoff": False, "rtscts": False, "dsrdtr": True},
    }

    # Port-type badge colors
    _TYPE_COLORS = {
        "at":     ("#388bfd", "AT"),    # blue
        "serial": ("#e3b341", "PPP"),   # amber
        "nmea":   ("#3fb950", "NMEA"),  # green
        "dm":     ("#f85149", "DM"),    # red
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected     = False
        self._usb_only      = sys.platform.startswith("linux")
        self._known_types   = {}   # device → type string ('at', 'serial', …)
        self._detector      = None
        self._operation_allowed = lambda: True
        self._build_ui()
        self._auto_detect_done.connect(self._on_auto_done)
        self._start_detector()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(8)

        self._led = StatusLed()
        lay.addWidget(self._led)

        lay.addWidget(self._lbl("Port:"))

        # Editable combo — user can always type a port manually
        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        self._port_combo.setMinimumWidth(240)
        set_tooltip(self._port_combo, "Select detected port or type any port manually\n"
            "(e.g. /dev/ttyUSB3 or COM5)")
        lay.addWidget(self._port_combo)

        # Refresh button
        self._refresh_btn = QToolButton()
        self._refresh_btn.setText("⟳")
        set_tooltip(self._refresh_btn, "Refresh port list")
        self._refresh_btn.clicked.connect(self._manual_refresh)
        lay.addWidget(self._refresh_btn)

        # USB-only filter (Linux only)
        self._usb_cb = QCheckBox("USB only")
        self._usb_cb.setChecked(self._usb_only)
        set_tooltip(self._usb_cb, "Show only /dev/ttyUSB* and /dev/ttyACM* (typical modem ports)\n"
            "Uncheck to see all serial devices including /dev/ttyS*")
        self._usb_cb.setVisible(sys.platform.startswith("linux"))
        self._usb_cb.stateChanged.connect(self._on_filter_changed)
        lay.addWidget(self._usb_cb)

        # Auto-detect (ATI probe)
        self._auto_btn = QPushButton("Auto-Detect")
        self._auto_btn.setObjectName("small")
        set_tooltip(self._auto_btn, "Probe each port with ATI to find a modem")
        self._auto_btn.clicked.connect(self._run_auto_detect)
        lay.addWidget(self._auto_btn)

        lay.addWidget(self._sep())

        lay.addWidget(self._lbl("Baud:"))
        self._baud_combo = QComboBox()
        self._baud_combo.addItems(self.BAUD_RATES)
        self._baud_combo.setCurrentText("115200")
        self._baud_combo.setMinimumWidth(90)
        lay.addWidget(self._baud_combo)

        lay.addWidget(self._lbl("Flow:"))
        self._flow_combo = QComboBox()
        self._flow_combo.addItems(self.FLOW_OPTIONS.keys())
        self._flow_combo.setMinimumWidth(90)
        lay.addWidget(self._flow_combo)

        lay.addStretch()

        self._connect_btn = QPushButton("  Connect")
        self._connect_btn.setObjectName("primary")
        self._connect_btn.setMinimumWidth(110)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        lay.addWidget(self._connect_btn)

    # ── Smart port detection ──────────────────────────────────────────────────

    def _start_detector(self):
        try:
            from modemdiag.core.port_detector import PortDetector
            self._detector = PortDetector(self)
            self._detector.all_ports_signal.connect(self._on_all_ports)
            self._detector.ports_updated.connect(self._on_ports_classified)
            self._detector.start()
            # Stop the background thread cleanly when the app quits
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                app.aboutToQuit.connect(self._stop_detector)
        except Exception:
            # Fallback to simple polling if detector unavailable
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._simple_refresh)
            self._timer.start(3000)
            self._simple_refresh()

    def _stop_detector(self):
        """Gracefully stop the PortDetector QThread before app exit."""
        if self._detector and self._detector.isRunning():
            self._detector.stop()

    def shutdown(self):
        """Stop background port polling (call when the window closes)."""
        self._stop_detector()

    def _on_all_ports(self, ports: list):
        """Called every ~2 s by PortDetector with the full list of serial devices."""
        if self._connected:
            return
        prev = self._port_combo.currentText()
        self._port_combo.blockSignals(True)
        self._port_combo.clear()

        for dev in ports:
            if self._usb_only and sys.platform.startswith("linux"):
                if not any(dev.startswith(p) for p in
                           ("/dev/ttyUSB", "/dev/ttyACM", "/dev/ttyMODEM")):
                    continue
            ptype = self._known_types.get(dev)
            if ptype and ptype in self._TYPE_COLORS:
                color, badge = self._TYPE_COLORS[ptype]
                label = f"{dev}  [{badge}]"
            else:
                label = dev
            self._port_combo.addItem(label, userData=dev)

        # Restore previous text (user may have typed something)
        idx = self._port_combo.findText(prev)
        if idx >= 0:
            self._port_combo.setCurrentIndex(idx)
        elif prev:
            self._port_combo.setEditText(prev)

        self._port_combo.blockSignals(False)

    def _on_ports_classified(self, classified: dict):
        """
        Called every ~2 s with {'at': '/dev/ttyUSB3', 'nmea': ..., ...}.
        Update type map and auto-select the AT port if nothing selected yet.
        """
        self._known_types = {v: k for k, v in classified.items() if v}

        at_port = classified.get("at")
        if at_port and not self._connected:
            current = self._port_combo.currentData() or self._port_combo.currentText()
            # Only auto-select if nothing useful is chosen yet
            if not current or current not in self._known_types:
                idx = self._port_combo.findData(at_port)
                if idx >= 0:
                    self._port_combo.setCurrentIndex(idx)

    # ── Fallback simple refresh (when PortDetector unavailable) ───────────────

    def _simple_refresh(self):
        import serial.tools.list_ports
        if self._connected:
            return
        prev = self._port_combo.currentData() or self._port_combo.currentText()
        self._port_combo.blockSignals(True)
        self._port_combo.clear()
        for p in sorted(serial.tools.list_ports.comports(), key=lambda x: x.device):
            dev = p.device
            if self._usb_only and sys.platform.startswith("linux"):
                if not any(dev.startswith(pfx) for pfx in
                           ("/dev/ttyUSB", "/dev/ttyACM", "/dev/ttyMODEM")):
                    continue
            self._port_combo.addItem(f"{dev}  —  {p.description or dev}", userData=dev)
        idx = self._port_combo.findData(prev)
        if idx >= 0:
            self._port_combo.setCurrentIndex(idx)
        elif prev:
            self._port_combo.setEditText(prev)
        self._port_combo.blockSignals(False)

    def _manual_refresh(self):
        """Force an immediate rescan."""
        if self._detector:
            # Trigger a one-shot scan on a thread
            threading.Thread(
                target=lambda: (
                    self._detector.all_ports_signal.emit(self._detector._all()),
                    self._detector.ports_updated.emit(self._detector._scan()),
                ),
                daemon=True,
            ).start()
        else:
            self._simple_refresh()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_connected(self, connected: bool):
        self._connected = connected
        self._led.set_connected(connected)
        if connected:
            self._connect_btn.setText("  Disconnect")
            self._connect_btn.setObjectName("danger")
        else:
            self._connect_btn.setText("  Connect")
            self._connect_btn.setObjectName("primary")
        self._connect_btn.style().unpolish(self._connect_btn)
        self._connect_btn.style().polish(self._connect_btn)
        for w in (self._port_combo, self._baud_combo, self._flow_combo,
                  self._auto_btn, self._refresh_btn, self._usb_cb):
            w.setEnabled(not connected)

    def selected_port(self) -> str:
        return self._port_combo.currentData() or self._port_combo.currentText()

    def set_operation_allowed(self, callback):
        self._operation_allowed = callback

    def selected_baud(self) -> int:
        return int(self._baud_combo.currentText())

    def selected_flow_params(self) -> dict:
        return self.FLOW_OPTIONS[self._flow_combo.currentText()]

    def refresh_ports(self):
        self._manual_refresh()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_filter_changed(self, state: int):
        self._usb_only = bool(state)
        self._manual_refresh()

    def _on_connect_clicked(self):
        if not self._operation_allowed():
            return
        if self._connected:
            self.disconnect_requested.emit()
        else:
            port = self.selected_port()
            if not port:
                QMessageBox.warning(self, "No Port",
                                    "No serial port selected or detected.")
                return
            self.connect_requested.emit(port, self.selected_baud(),
                                        self.selected_flow_params())

    def _run_auto_detect(self):
        if not self._operation_allowed():
            return
        self._auto_btn.setEnabled(False)
        self._auto_btn.setText("Scanning…")

        def _detect():
            from modemdiag.core.serial_manager import SerialManager
            self._auto_detect_done.emit(SerialManager.auto_detect_at_port(self._operation_allowed))

        threading.Thread(target=_detect, daemon=True).start()

    def _on_auto_done(self, found: str | None):
        self._auto_btn.setEnabled(True)
        self._auto_btn.setText("Auto-Detect")
        if not self._operation_allowed():
            return
        if found:
            idx = self._port_combo.findData(found)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)
            else:
                self._port_combo.addItem(found, userData=found)
                self._port_combo.setCurrentIndex(self._port_combo.count() - 1)
        else:
            QMessageBox.information(
                self, "Auto-Detect",
                "No modem port found automatically.\n\n"
                "Tips:\n"
                "• Make sure the modem is plugged in via USB\n"
                "• On Linux: check 'ls /dev/ttyUSB* /dev/ttyACM*'\n"
                "• On Windows: check Device Manager → Ports (COM & LPT)\n"
                "• Type the port name directly in the Port box if you know it"
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _lbl(text: str) -> QLabel:
        l = QLabel(text)
        l.setObjectName("muted")
        return l

    @staticmethod
    def _sep() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setFixedWidth(1)
        set_style(f, "background-color: #30363d;")
        return f
