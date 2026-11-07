#!/usr/bin/env python3
"""Refresh README screenshots using the real UI and entirely simulated data.

Run: QT_QPA_PLATFORM=offscreen python3 tools/capture_screenshots.py
No serial ports, network checks, existing settings or session logs are used.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QTabWidget

from modemdiag.core import paths
from modemdiag.core.serial_manager import SerialManager
from modemdiag.core.release_check import ReleaseGuard
from modemdiag.ui.port_selector import PortSelectorWidget
from modemdiag.ui.styles import DARK_THEME
from modemdiag.ui.terminal_panel import TerminalPanel


RESPONSES = {
    "AT": "OK",
    "ATI": "Fibocom\r\nFM160 (simulated)\r\nRevision: DEMO-FIRMWARE\r\nOK",
    "AT+CPIN?": "+CPIN: READY\r\nOK",
    "AT+COPS?": '+COPS: 0,2,"00101",7\r\nOK',
    "AT+CEREG?": "+CEREG: 2,1\r\nOK",
    "AT+CSQ": "+CSQ: 23,99\r\nOK",
    "AT+CESQ": "+CESQ: 99,99,255,255,20,60\r\nOK",
    "AT+CGDCONT?": '+CGDCONT: 1,"IP","example.test","0.0.0.0",0,0\r\nOK',
    "AT+CGATT?": "+CGATT: 1\r\nOK",
    "AT+CGPADDR=1": '+CGPADDR: 1,"198.51.100.24"\r\nOK',
    'AT+CGDCONT=1,"IP","example.test"': "OK",
    "AT+GTWWAN=1,1": "OK",
    "AT+GTWWAN=0,1": "OK",
}

DEMO_STEPS = [
    {"command": "ATI", "description": "Identify simulated Fibocom module", "expect": "OK"},
    {"command": "AT+CPIN?", "description": "SIM readiness", "expect": "READY"},
    {"command": "AT+CEREG?", "description": "LTE registration", "expect": "+CEREG:"},
    {"command": 'AT+CGDCONT=1,"IP","example.test"', "description": "Configure sample APN", "expect": "OK"},
    {"command": "AT+GTWWAN=1,1", "description": "Enable Fibocom data interface", "expect": "OK"},
    {"command": "AT+CGATT?", "description": "Packet-service attach status", "expect": "+CGATT:"},
    {"command": "AT+CGPADDR=1", "description": "Read sample IP address", "expect": "+CGPADDR:"},
    {"command": "AT+GTWWAN=0,1", "description": "Disable Fibocom data interface", "expect": "OK"},
]


def refuse_io(*args, **kwargs):
    raise RuntimeError("Screenshots must not use serial ports or release checks")


def exchange(terminal, command, responses=RESPONSES):
    response = responses.get(command, "ERROR")
    terminal.append_tx(command)
    terminal.append_rx_raw(response)
    return response


def capture(app, window, filename):
    window.setFocus()
    # Nested panels post layout requests while their splitter sizes settle.
    for _ in range(5):
        app.processEvents()
    destination = ROOT / "docs" / filename
    if not window.grab().save(str(destination)):
        raise RuntimeError(f"Cannot save {destination}")
    print(f"Saved docs/{filename}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    parser.add_argument("--only", choices=("fibocom.png", "automation.png"))
    args = parser.parse_args()
    app = QApplication([])
    app.setStyleSheet(DARK_THEME)
    app.setFont(QFont("Segoe UI", 10))
    with tempfile.TemporaryDirectory(prefix="modemdiag-screenshots-") as temporary, \
            patch.object(paths, "get_user_dir", return_value=Path(temporary)), \
            patch.object(PortSelectorWidget, "_start_detector"), \
            patch.object(SerialManager, "connect", side_effect=refuse_io), \
            patch.object(SerialManager, "send", side_effect=refuse_io), \
            patch.object(ReleaseGuard, "start"), \
            patch.object(ReleaseGuard, "check_now", side_effect=refuse_io), \
            patch.object(TerminalPanel, "_ts", return_value="12:00:00.000 "):
        # Import after redirecting paths: module-level settings paths are isolated too.
        (Path(temporary) / "appearance.json").write_text(json.dumps({"theme": args.theme}))
        from modemdiag.ui.main_window import MainWindow, __version__

        demo_path = Path(temporary) / "macros" / "fibocom_iot_demo.yaml"
        demo_path.parent.mkdir(exist_ok=True)
        demo_path.write_text(yaml.safe_dump({
            "name": "Fibocom IoT Data Check",
            "description": "SIMULATED Fibocom FM160 data-session check; no modem connected.",
            "manufacturer": "Fibocom",
            "steps": DEMO_STEPS,
        }, sort_keys=False), encoding="utf-8")

        def save(filename):
            if args.only and args.only != filename:
                return
            if args.theme == "light":
                filename = Path(filename).stem + "-light.png"
            capture(app, window, filename)

        window = MainWindow()
        try:
            window.resize(1440, 900)
            window._port_selector._port_combo.addItem("/dev/ttyUSB2 — Demo AT port", "/dev/ttyUSB2")
            window._port_selector.set_connected(True)
            window._terminal.set_input_enabled(True)
            window._conn_lbl.setText("● Simulated session")
            window._conn_lbl.setStyleSheet("color:#3fb950; margin-right:12px;")
            window._status_bar.showMessage(f"ModemDiag {__version__} — simulated data; no modem connected")
            window.centralWidget().layout().itemAt(1).widget().setSizes([900, 540])
            window.show()

            panel = window._cmd_panel
            terminal = window._terminal
            panel._mfr_combo.setCurrentText("Fibocom")
            for row in range(panel._cat_list.count()):
                if panel._cat_list.item(row).data(Qt.ItemDataRole.UserRole) == "Data":
                    panel._cat_list.setCurrentRow(row)
                    break
            command = next(c for c in panel._commands_for_function("Data")
                           if c.get("get") == "AT+GTWWAN?")
            panel._show_param_bar(command["name"], command["command"], command["params"],
                                  command.get("get", ""), command.get("caps", ""))
            terminal.clear()
            terminal.append_info("Fibocom IoT data session — SIMULATED")
            for step in DEMO_STEPS:
                exchange(terminal, step["command"])
            terminal.append_info("Demo complete — no modem connected")
            save("fibocom.png")

            window.findChild(QTabWidget).setCurrentIndex(1)
            automation = window._auto_panel
            automation._select_by_path(str(demo_path))
            automation._run_rows = list(range(len(automation._steps)))
            terminal.clear()
            terminal.append_info("Fibocom automation — SIMULATED")
            results = []
            for index, step in enumerate(automation._steps):
                command = step["command"]
                automation.on_step_started(index, command, step["description"])
                response = RESPONSES.get(command, "ERROR")
                success = response != "ERROR" and step["expect"] in response
                automation.on_step_result(index, response, success)
                results.append({"success": success})
                exchange(terminal, command)
            automation.on_suite_finished(all(r["success"] for r in results), results)
            terminal.append_info("Demo complete — no modem connected")
            automation._table.selectRow(4)
            save("automation.png")
        finally:
            window.close()


if __name__ == "__main__":
    main()
