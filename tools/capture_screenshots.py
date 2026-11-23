#!/usr/bin/env python3
"""Refresh README screenshots using the real UI and entirely simulated data.

Run: QT_QPA_PLATFORM=offscreen python3 tools/capture_screenshots.py
No serial ports, network checks, existing settings or session logs are used.
"""
import argparse
from datetime import datetime, timedelta, timezone
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
from PyQt6.QtWidgets import QApplication, QMessageBox

from modemdiag.core import paths
from modemdiag.core.serial_manager import SerialManager
from modemdiag.core.release_check import ReleaseGuard
from modemdiag.core.network_status import parse_snapshot
from modemdiag.core.ai_client import AIClient
from modemdiag.core.ai_diagnostics import parse_plan
from modemdiag.ui.port_selector import PortSelectorWidget
from modemdiag.ui.styles import DARK_THEME
from modemdiag.ui.terminal_panel import TerminalPanel


RESPONSES = {
    "AT": "OK",
    "ATI": "Fibocom\r\nFM160 (example)\r\nRevision: DEMO-FIRMWARE\r\nOK",
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

DASHBOARD_RESPONSES = {
    **RESPONSES,
    "ATI": "Quectel\r\nRM520N-GL (example)\r\nRevision: DEMO-FIRMWARE\r\nOK",
    "AT+COPS?": '+COPS: 0,0,"Example Mobile",7\r\nOK',
    "AT+CESQ": "+CESQ: 99,99,255,255,20,50\r\nOK",
    "AT+CREG?": "+CREG: 0,0\r\nOK",
    "AT+CGREG?": "+CGREG: 0,0\r\nOK",
    "AT+CEREG?": '+CEREG: 2,1,"1234","1234567",7\r\nOK',
    "AT+C5GREG?": "+C5GREG: 0,0\r\nOK",
    "AT+CIREG?": "+CIREG: 0,1\r\nOK",
    "AT+CFUN?": "+CFUN: 1\r\nOK",
    "AT+CGDCONT?": '+CGDCONT: 1,"IPV4V6","internet.example","0.0.0.0"\r\n+CGDCONT: 2,"IPV6","ims.example","::"\r\nOK',
    "AT+CGACT?": "+CGACT: 1,1\r\n+CGACT: 2,1\r\nOK",
    "AT+CGPADDR": '+CGPADDR: 1,"198.51.100.24"\r\n+CGPADDR: 2,"2001:db8::24"\r\nOK',
    'AT+QENG="servingcell"': (
        '+QENG: "servingcell","NOCONN"\r\n'
        '+QENG: "LTE","FDD",001,01,1234567,123,1650,3,5,5,1234,-91,-10,-67,18,9,230,-\r\n'
        '+QENG: "NR5G-NSA",001,01,747,-93,17,-11,627264,78,12,1\r\nOK'),
    'AT+QENG="neighbourcell"': (
        '+QENG: "neighbourcell intra","LTE",1650,124,-9,-86,-66,18,37,7,16,6,44\r\n'
        '+QENG: "neighbourcell inter","LTE",2850,201,-12,-98,-74,8,30,7,16,6,44\r\n'
        '+QENG: "neighbourcell intra","LTE",1650,305,-15,-104,-79,3,20,7,16,6,44\r\nOK'),
}

DEMO_STEPS = [
    {"command": "ATI", "description": "Identify Fibocom module", "expect": "OK"},
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
    parser.add_argument("--only", choices=("dashboard.png", "ai-diag.png", "fibocom.png", "automation.png", "updates.png"))
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
            patch.object(AIClient, "request", side_effect=refuse_io), \
            patch.object(TerminalPanel, "_ts", return_value="12:00:00.000 "):
        # Import after redirecting paths: module-level settings paths are isolated too.
        (Path(temporary) / "appearance.json").write_text(json.dumps({"theme": args.theme}))
        from modemdiag.ui.main_window import MainWindow, __version__

        demo_path = Path(temporary) / "macros" / "fibocom_iot_demo.yaml"
        demo_path.parent.mkdir(exist_ok=True)
        demo_path.write_text(yaml.safe_dump({
            "name": "Fibocom IoT Data Check",
            "description": "Fibocom FM160 data-session example.",
            "manufacturer": "Fibocom",
            "steps": DEMO_STEPS,
        }, sort_keys=False), encoding="utf-8")

        def save(filename, target=None):
            if args.only and args.only != filename:
                return
            if args.theme == "light":
                filename = Path(filename).stem + "-light.png"
            capture(app, target if target is not None else window, filename)

        window = MainWindow()
        try:
            window.resize(1440, 900)
            window._port_selector._port_combo.addItem("/dev/ttyUSB2 — Demo AT port", "/dev/ttyUSB2")
            window._port_selector.set_connected(True)
            window._terminal.set_input_enabled(True)
            window._conn_lbl.setText("● Example session")
            window._conn_lbl.setStyleSheet("color:#3fb950; margin-right:12px;")
            window._status_bar.showMessage(f"ModemDiag {__version__} — interface and workflow examples")
            window.centralWidget().layout().itemAt(1).widget().setSizes([900, 540])
            window.show()

            panel = window._cmd_panel
            terminal = window._terminal
            window.resize(1440, 1180)
            window._dashboard.set_connected(True)
            # Build a deterministic history from sample observations, never I/O.
            end = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
            levels = [32, 34, 33, 36, 39, 37, 40, 42, 41, 39, 44, 46, 45, 44,
                      47, 49, 46, 43, 44, 45, 48, 51, 49, 47, 46, 44, 47, 49,
                      51, 53, 52, 50, 48, 49, 52, 54, 53, 51, 49, 48, 49, 50]
            for index, level in enumerate(levels):
                sample = dict(DASHBOARD_RESPONSES)
                sample["AT+CESQ"] = f"+CESQ: 99,99,255,255,20,{level}\r\nOK"
                sample["AT+CSQ"] = f"+CSQ: {min(31, 15 + index // 5)},99\r\nOK"
                if index < 26:
                    pci = "120" if index < 12 else "121"
                    sample['AT+QENG="servingcell"'] = sample['AT+QENG="servingcell"'].replace(",123,1650,", f",{pci},1650,")
                if index < 28:
                    sample["AT+CIREG?"] = "+CIREG: 0,0\r\nOK"
                window._dashboard.show_snapshot(parse_snapshot([
                    {"command": command, "response": response, "success": True}
                    for command, response in sample.items()]), observed_at=end - timedelta(seconds=(len(levels) - index - 1) * 10))
            window._dashboard._status.setText("Updated 12:00:00 · Example session")
            terminal.clear()
            terminal.append_info("Network dashboard · Example session")
            for command in ("AT+CPIN?", "AT+COPS?", "AT+CSQ", "AT+CESQ", "AT+CEREG?", "AT+CIREG?", 'AT+QENG="servingcell"'):
                exchange(terminal, command, DASHBOARD_RESPONSES)
            save("dashboard.png")

            window.resize(1440, 900)
            window._tabs.setCurrentWidget(window._ai_panel)
            ai = window._ai_panel
            ai.set_connected(True)
            ai._connection_status.setText("Example conversation")
            ai._chat.clear()
            ai._append("AI Diag ★ Beta · Example", "Example workflow: inspect the modem overview and review the next diagnostic steps.")
            ai._append("You", "My modem registers on LTE, but data does not work. Can you inspect the connection?")
            ai._on_answer(parse_plan(json.dumps({
                "reply": "The shared overview reports a ready SIM, LTE registration and packet attachment. Those states do not establish Internet access.\n\nNext I would check the APN definition, whether the data context is active, and which IP addresses the modem reports. These queries read the current state without changing your configuration.\n\nSelect the steps below and choose Run selected & analyze. I can use the returned evidence to narrow the next step.",
                "commands": [
                    {"command": "AT+CGDCONT?", "reason": "Inspect the configured APN and IP protocol."},
                    {"command": "AT+CGACT?", "reason": "Check which PDP contexts are active."},
                    {"command": "AT+CGPADDR", "reason": "Read the addresses reported for the data contexts."},
                ],
            })))
            ai._status.setText("Enter your provider and API key to start diagnostics")
            save("ai-diag.png")

            window.resize(1440, 900)
            window._tabs.setCurrentWidget(panel)
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
            terminal.append_info("Fibocom IoT data-session example")
            for step in DEMO_STEPS:
                exchange(terminal, step["command"])
            terminal.append_info("Example sequence complete")
            save("fibocom.png")

            window._tabs.setCurrentWidget(window._auto_panel)
            automation = window._auto_panel
            automation._select_by_path(str(demo_path))
            automation._run_rows = list(range(len(automation._steps)))
            terminal.clear()
            terminal.append_info("Fibocom automation example")
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
            terminal.append_info("Example sequence complete")
            automation._table.selectRow(4)
            save("automation.png")
            def capture_updates(dialog):
                dialog.show()
                save("updates.png", dialog)
                dialog.close()
                return 0
            with patch.object(QMessageBox, "exec", new=capture_updates):
                window._show_release_status()
        finally:
            window.close()


if __name__ == "__main__":
    main()
