"""
ui/main_window.py
Main application window.
Layout: Connection toolbar | [Dashboard / Commands / Automation / AI Diag] | [Terminal]
"""
from modemdiag.ui.theme import set_style, theme_color
from copy import deepcopy
import os
import shutil
import sys
from html import escape
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QLabel,
    QMessageBox, QFileDialog,
    QStatusBar, QToolBar, QPushButton, QDialog, QDialogButtonBox, QTextBrowser,
)
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QIcon, QDesktopServices

from modemdiag.core.serial_manager  import SerialManager
from modemdiag.core.command_runner  import CommandRunner
from modemdiag.core.logger          import SessionLogger
from modemdiag.core.paths           import logs_dir, assets_dir, get_user_dir, get_project_dir
from modemdiag.core.release_check   import ReleaseGuard
from modemdiag.core.release_policy  import POLICY_URL
from modemdiag.core.appearance      import load_theme, save_theme
from modemdiag.core.config_transfer import export_configuration, read_archive, import_configuration
from modemdiag.core.at_parser       import split_lines, complete_lines, LineType
from modemdiag.core.network_status  import overview_steps, cell_steps, parse_snapshot
from modemdiag.core.ai_diagnostics  import diagnostic_context, execution_steps
from modemdiag.ui.theme            import apply_theme

from modemdiag.ui.port_selector    import PortSelectorWidget
from modemdiag.ui.terminal_panel   import TerminalPanel
from modemdiag.ui.command_panel    import CommandPanel
from modemdiag.ui.automation_panel import AutomationPanel
from modemdiag.ui.dashboard_panel  import DashboardPanel
from modemdiag.ui.ai_panel         import AIDiagPanel

from modemdiag import __version__
__author__  = "Yassine El Harami"
__license__ = "MIT"
__url__     = "https://github.com/yassineflag/ModemDiag"


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._appearance_file = get_user_dir() / "appearance.json"
        self._theme = load_theme(self._appearance_file)
        apply_theme(QApplication.instance(), self._theme, self)
        self._release_guard = ReleaseGuard(__version__, get_user_dir() / "release-policy-cache.json", self)
        self._serial   = SerialManager(self, operation_allowed=lambda: not self._release_guard.revoked)
        self._logger   = SessionLogger(str(get_user_dir() / "logs"), on_error=self._on_logging_error)
        self._runner   = CommandRunner(self._serial, self)
        self._log_path = ""
        self._baudrate = 115200
        self._missing_port_scans = 0
        self._port_seen_in_scan = False
        self._run_source = None
        self._dashboard_results = []
        self._dashboard_extended = False
        self._pending_suite = None
        self._manual_buffer = ""
        self._manual_timer = QTimer(self)
        self._manual_timer.setSingleShot(True)
        self._manual_timer.setInterval(5000)
        self._manual_timer.timeout.connect(self._manual_finished)

        self._build_ui()
        self._connect_signals()
        self._apply_release_state(self._release_guard.revoked, self._release_guard.message)
        self._release_guard.start()
        self.setWindowTitle(f"ModemDiag {__version__} — AT Command Tester")
        # App icon
        _icon = assets_dir() / "icon.png"
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))
        self.resize(1280, 780)
        self.setMinimumSize(870, 580)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_menu()

        # ── Connection toolbar ─────────────────────────────────────────────────
        conn_bar = QToolBar("Connection")
        conn_bar.setMovable(False)
        set_style(conn_bar, "QToolBar { background:#161b22; border-bottom:1px solid #21262d;"
            "padding:0; spacing:0; }")
        self._port_selector = PortSelectorWidget()
        self._port_selector.set_operation_allowed(lambda: not self._release_guard.revoked)
        conn_bar.addWidget(self._port_selector)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, conn_bar)

        # ── Central widget ─────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._release_banner = QWidget()
        self._release_banner.setObjectName("releaseBanner")
        notice_row = QHBoxLayout(self._release_banner)
        notice_row.setContentsMargins(10, 6, 10, 6)
        self._release_notice = QLabel()
        self._release_notice.setTextFormat(Qt.TextFormat.PlainText)
        self._release_notice.setWordWrap(True)
        notice_row.addWidget(self._release_notice, 1)
        self._release_button = QPushButton("View releases")
        self._release_button.setObjectName("small")
        self._release_button.clicked.connect(self._open_releases)
        notice_row.addWidget(self._release_button)
        self._release_banner.hide()
        root.addWidget(self._release_banner)

        # ── Main horizontal area ───────────────────────────────────────────────
        splitter = self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)

        # Left: tabbed panels (Dashboard, Commands, Automation, AI Diag)
        self._dashboard      = DashboardPanel()
        self._cmd_panel      = CommandPanel()
        self._auto_panel     = AutomationPanel()
        self._ai_panel       = AIDiagPanel(get_user_dir() / "ai-settings.json")
        self._ai_panel.set_context_provider(self._ai_context)

        self._edit_menu.addAction(self._cmd_panel.undo_action)
        self._edit_menu.addAction(self._cmd_panel.redo_action)

        left_tabs = self._tabs = QTabWidget()
        left_tabs.setTabPosition(QTabWidget.TabPosition.North)
        left_tabs.setMinimumWidth(430)
        left_tabs.setDocumentMode(False)
        left_tabs.tabBar().setDrawBase(False)
        left_tabs.addTab(self._dashboard, "Dashboard")
        left_tabs.addTab(self._cmd_panel,  "AT Commands")
        left_tabs.addTab(self._auto_panel, "Automation")
        left_tabs.addTab(self._ai_panel, "★  AI Diag · Beta")
        splitter.addWidget(left_tabs)
        splitter.setStretchFactor(0, 0)  # left: don't grow on window resize

        # Right: terminal
        self._terminal = TerminalPanel()
        self._terminal.setMinimumWidth(360)
        splitter.addWidget(self._terminal)
        splitter.setStretchFactor(1, 1)  # right: gets extra space on resize
        splitter.setSizes([720, 480])    # ~60/40 — commands panel is larger
        root.addWidget(splitter, 1)
        self._terminal_sizes = [720, 480]
        self._terminal.setVisible(False)
        self._tabs.currentChanged.connect(self._update_terminal_visibility)
        self._dashboard.terminal_toggled.connect(self._update_terminal_visibility)
        self._ai_panel.terminal_toggled.connect(self._update_terminal_visibility)

        # ── Status bar ─────────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._log_btn = QPushButton()
        self._log_btn.setObjectName("small")
        self._log_btn.clicked.connect(self._toggle_logging)
        self._status_bar.addPermanentWidget(self._log_btn)
        self._update_logging_state()
        self._theme_btn = QPushButton()
        self._theme_btn.setObjectName("small")
        self._theme_btn.clicked.connect(self._toggle_theme)
        self._update_theme_button()
        self._status_bar.addPermanentWidget(self._theme_btn)
        self._conn_lbl = QLabel("● Disconnected")
        set_style(self._conn_lbl, "color:#484f58; margin-right:12px;")
        self._status_bar.addPermanentWidget(self._conn_lbl)
        self._status_bar.showMessage(
            "Ready — select a port and click Connect."
        )


    def _update_terminal_visibility(self, *_):
        current = self._tabs.currentWidget()
        visible = (current._terminal_toggle.isChecked() if current in (self._dashboard, self._ai_panel) else True)
        if visible == (not self._terminal.isHidden()):
            return
        if not visible:
            self._terminal_sizes = self._main_splitter.sizes()
        self._terminal.setVisible(visible)
        if visible:
            self._main_splitter.setSizes(self._terminal_sizes)

    def _update_theme_button(self):
        target = "light" if self._theme == "dark" else "dark"
        self._theme_btn.setText(f"{target.title()} theme")
        self._theme_btn.setToolTip(f"Switch to {target} theme")

    def _toggle_theme(self):
        self._theme = "light" if self._theme == "dark" else "dark"
        apply_theme(QApplication.instance(), self._theme, self)
        self._update_theme_button()
        try:
            save_theme(self._appearance_file, self._theme)
        except OSError:
            self._status_bar.showMessage("Theme changed for this session; the preference could not be saved.")

    def _build_menu(self):
        mb = self.menuBar()

        file_m = mb.addMenu("File")
        a = QAction("Save Log…", self)
        a.setShortcut(QKeySequence("Ctrl+S"))
        a.triggered.connect(self._save_log)
        file_m.addAction(a)
        file_m.addSeparator()
        a = QAction("Quit", self)
        a.setShortcut(QKeySequence("Ctrl+Q"))
        a.triggered.connect(self.close)
        file_m.addAction(a)

        self._edit_menu = mb.addMenu("Edit")

        settings_m = mb.addMenu("Settings")
        a = QAction("Configuration location…", self)
        a.triggered.connect(self._show_config_location)
        settings_m.addAction(a)
        a = QAction("Open configuration folder", self)
        a.triggered.connect(lambda: self._open_folder(get_user_dir()))
        settings_m.addAction(a)
        a = QAction("Export configuration…", self)
        a.triggered.connect(self._export_configuration)
        settings_m.addAction(a)
        a = QAction("Import configuration…", self)
        a.triggered.connect(self._import_configuration)
        settings_m.addAction(a)
        settings_m.addSeparator()
        a = QAction("Restore default command library…", self)
        a.triggered.connect(lambda: self._cmd_panel.reset_defaults())
        settings_m.addAction(a)

        sess_m = mb.addMenu("Session")
        self._start_log_action = QAction("Start new log", self)
        self._start_log_action.triggered.connect(self._start_logging)
        sess_m.addAction(self._start_log_action)
        self._stop_log_action = QAction("Stop logging", self)
        self._stop_log_action.triggered.connect(self._stop_logging)
        sess_m.addAction(self._stop_log_action)
        a = QAction("Open logs folder", self)
        a.triggered.connect(lambda: self._open_folder(logs_dir()))
        sess_m.addAction(a)
        sess_m.addSeparator()
        a = QAction("Clear Terminal", self)
        a.setShortcut(QKeySequence("Ctrl+L"))
        a.triggered.connect(lambda: self._terminal.clear())
        sess_m.addAction(a)

        help_m = mb.addMenu("Help")
        a = QAction("How to Use…", self)
        a.setShortcut(QKeySequence("F1"))
        a.triggered.connect(self._show_how_to_use)
        help_m.addAction(a)
        help_m.addSeparator()
        a = QAction("Updates…", self)
        a.triggered.connect(self._show_release_status)
        help_m.addAction(a)
        a = QAction("Check for updates now", self)
        a.triggered.connect(self._release_guard.check_now)
        help_m.addAction(a)
        help_m.addSeparator()
        a = QAction("About ModemDiag…", self)
        a.triggered.connect(self._show_about)
        help_m.addAction(a)
        a = QAction("Licenses…", self)
        a.triggered.connect(self._show_licenses)
        help_m.addAction(a)

    # ── Signal wiring ──────────────────────────────────────────────────────────

    def _connect_signals(self):
        self._port_selector.connect_requested.connect(self._on_connect)
        self._port_selector.disconnect_requested.connect(self._on_disconnect)
        self._port_selector.ports_changed.connect(self._on_ports_available)

        self._serial.data_received.connect(self._on_data_received)
        self._serial.error_occurred.connect(self._on_serial_error)
        self._serial.connection_changed.connect(self._on_connection_changed)

        self._terminal.command_send_requested.connect(self._send_command)
        self._cmd_panel.command_triggered.connect(self._send_command)

        self._auto_panel.run_suite_requested.connect(self._start_suite)
        self._auto_panel.stop_requested.connect(self._stop_automation)
        self._dashboard.refresh_requested.connect(self._refresh_dashboard)
        self._ai_panel.commands_requested.connect(self._run_ai_commands)
        self._ai_panel.stop_requested.connect(self._stop_ai_commands)

        self._runner.step_result.connect(self._on_step_result)
        self._runner.payload_sent.connect(lambda payload: self._logger.info(f"TX payload ({len(payload)} bytes): {payload.hex()}"))
        self._runner.suite_finished.connect(self._on_suite_finished)

        self._runner.step_started.connect(self._on_step_started)
        self._release_guard.state_changed.connect(self._apply_release_state)
        self._release_guard.status_changed.connect(lambda message: self._status_bar.showMessage(message))

    # ── Connection ─────────────────────────────────────────────────────────────

    def _on_connect(self, port: str, baudrate: int, flow: dict):
        if self._release_guard.revoked:
            self._terminal.append_error(self._release_guard.message)
            return
        ok, err = self._serial.connect(port, baudrate, **flow)
        if not ok:
            QMessageBox.critical(self, "Connection Error", str(err))
            return
        self._baudrate = baudrate
        self._start_logging()
        self._terminal.append_info(f"Connected to {port} @ {baudrate} baud")
        self._terminal.set_input_enabled(True)
        if self._logger.active:
            self._status_bar.showMessage(f"Connected: {port}  @  {baudrate}  |  Log: {self._log_path}")

    def _on_disconnect(self):
        self._ai_panel.stop("Modem disconnected.", quiet=True)
        if self._auto_panel._running or (self._runner.is_running and self._run_source not in ("dashboard", "ai")):
            self._auto_panel._on_stop()
        self._serial.disconnect()
        if self._logger.active:
            self._logger.stop()
        self._update_logging_state()
        self._terminal.set_input_enabled(False)
        self._terminal.append_info("Disconnected.")
        self._status_bar.showMessage("Disconnected.")

    def _on_connection_changed(self, connected: bool):
        self._terminal.reset_rx_state()
        self._port_selector.set_connected(connected)
        self._dashboard.set_connected(connected)
        self._ai_panel.set_connected(connected)
        if connected:
            self._missing_port_scans = 0
            self._port_seen_in_scan = False
            set_style(self._conn_lbl, "color:#3fb950; margin-right:12px;")
            self._conn_lbl.setText(f"● {self._serial.port_name}")
        else:
            self._pending_suite = None
            self._manual_timer.stop()
            self._manual_buffer = ""
            if self._auto_panel._running or (self._runner.is_running and self._run_source not in ("dashboard", "ai")):
                self._auto_panel._on_stop()
            self._runner.stop()
            self._run_source = None
            self._terminal.set_input_enabled(False)
            if self._logger.active:
                self._logger.stop()
            set_style(self._conn_lbl, "color:#484f58; margin-right:12px;")
            self._conn_lbl.setText("● Disconnected")
        self._update_logging_state()

    def _on_ports_available(self, ports):
        if not self._serial.is_connected:
            return
        port = self._serial.port_name
        if not isinstance(port, str) or not (port.upper().startswith("COM") or port.startswith(("/dev/ttyUSB", "/dev/ttyACM", "/dev/ttyMODEM"))):
            return
        normalize = lambda value: value.upper() if port.upper().startswith("COM") else value
        if normalize(port) in {normalize(p) for p in ports}:
            self._port_seen_in_scan = True
            self._missing_port_scans = 0
            return
        # Some manually entered ports aren't exposed by enumeration. Only infer
        # removal after this connection's port has actually appeared in a scan.
        if not self._port_seen_in_scan:
            return
        self._missing_port_scans += 1
        if self._missing_port_scans >= 2:
            self._serial._on_worker_error(f"Port {port} disappeared, possibly during a modem reset. Select the returned port and Connect when the modem is ready.")

    # ── Data flow ──────────────────────────────────────────────────────────────

    def _send_command(self, command: str):
        if self._release_guard.revoked:
            self._terminal.append_error(self._release_guard.message)
            return
        if self._ai_panel.is_controlling:
            self._terminal.append_error("Stop AI diagnostics before sending a manual command.")
            return
        if self._runner.is_running:
            self._terminal.append_error(
                "Wait for the dashboard refresh before sending a manual command."
                if self._run_source == "dashboard" else
                "Stop the automation before sending a manual command.")
            return
        if not self._serial.is_connected:
            self._terminal.append_error(
                "Not connected — select a port and click Connect first."
            )
            return
        self._manual_buffer = ""
        self._manual_timer.start()
        ok, err = self._serial.send(command)
        if ok:
            self._logger.tx(command)
            self._terminal.append_tx(command)
        else:
            self._manual_timer.stop()
            self._terminal.append_error(f"Send failed: {err}")

    def _on_step_started(self, idx: int, cmd: str, _desc: str):
        self._logger.tx(cmd)
        if self._run_source == "dashboard":
            self._dashboard.set_busy(True, f"Refreshing · {_desc}…")
        elif self._run_source == "ai":
            self._ai_panel.on_step_started(idx, cmd, _desc)
        else:
            self._auto_panel.on_step_started(idx, cmd, _desc)
        self._terminal.append_tx(f"[{idx+1}] {cmd}")

    def _on_data_received(self, data: str):
        self._logger.rx(data)
        self._terminal.append_rx_raw(data)
        self._runner.feed(data)
        if self._manual_timer.isActive():
            self._manual_buffer = (self._manual_buffer + data)[-65536:]
            complete, _ = complete_lines(self._manual_buffer)
            if any(kind in (LineType.OK, LineType.ERROR, LineType.CME_ERROR, LineType.CMS_ERROR)
                   or line in ("NO CARRIER", "BUSY", "NO ANSWER")
                   for line, kind in split_lines(complete)):
                self._manual_finished()

    def _on_serial_error(self, error: str):
        self._logger.info(f"Serial error: {error}")
        self._terminal.append_error(f"Serial error: {error}")
        self._status_bar.showMessage(f"Serial error: {error}")

    def _start_suite(self, steps):
        if self._release_guard.revoked:
            self._auto_panel._on_stop()
            self._auto_panel._run_btn.setEnabled(False)
            self._terminal.append_error(self._release_guard.message)
            return
        if self._ai_panel.is_controlling or self._run_source == "ai":
            self._auto_panel._on_stop()
            self._terminal.append_error("Stop AI diagnostics before starting automation.")
            return
        if self._runner.is_running and self._run_source != "dashboard":
            return
        self._dashboard.set_busy(True, "Live refresh paused · automation running")
        if self._run_source == "dashboard" or self._manual_timer.isActive():
            self._pending_suite = deepcopy(steps)
            self._auto_panel._status_lbl.setText("Waiting for the current modem query…")
            return
        self._run_source = "automation"
        self._runner.run(steps)

    def _stop_automation(self):
        self._logger.info("Automation stopped by user")
        self._pending_suite = None
        if self._run_source not in ("dashboard", "ai"):
            self._runner.stop()
            self._run_source = None
            self._dashboard.set_busy(False, "Automation stopped · ready to refresh")

    def _manual_finished(self):
        self._manual_timer.stop()
        self._manual_buffer = ""
        if self._pending_suite is not None:
            steps, self._pending_suite = self._pending_suite, None
            self._start_suite(steps)

    def _ai_context(self, overview, recent, secrets):
        return diagnostic_context(
            self._dashboard._snapshot if overview else None,
            self._terminal._output.toPlainText() if recent else "",
            connected=self._serial.is_connected, secrets=secrets)

    def _run_ai_commands(self, proposals, automatic):
        if self._release_guard.revoked:
            self._ai_panel.execution_rejected(self._release_guard.message)
            return
        if not self._serial.is_connected:
            self._ai_panel.execution_rejected("Connect a modem before running diagnostic commands.")
            return
        if self._runner.is_running or self._manual_timer.isActive() or self._pending_suite is not None:
            self._ai_panel.execution_rejected("The modem is busy. Wait for the active command sequence, then run the selected steps again.")
            return
        try:
            steps = execution_steps(proposals, automatic)
        except (ValueError, TypeError) as exc:
            self._ai_panel.execution_rejected(str(exc))
            return
        self._run_source = "ai"
        self._dashboard.set_busy(True, "Live refresh paused · AI diagnostics running")
        self._runner.run(steps)

    def _stop_ai_commands(self):
        if self._run_source == "ai":
            self._runner.stop()
            self._run_source = None
            self._dashboard.set_busy(False, "AI diagnostics stopped · ready to refresh")

    def _refresh_dashboard(self):
        if self._release_guard.revoked or not self._serial.is_connected:
            return
        if self._runner.is_running or self._pending_suite is not None or self._ai_panel.is_controlling:
            return
        if self._manual_timer.isActive():
            self._dashboard.set_busy(False, "Waiting for the manual command response · refresh again shortly")
            return
        self._dashboard_results = []
        self._dashboard_extended = False
        self._run_source = "dashboard"
        self._dashboard.set_busy(True, "Refreshing modem information…")
        self._runner.run(overview_steps())

    def _on_step_result(self, index, response, success):
        self._logger.info(f"{self._run_source or 'automation'} step {index + 1}: {'PASS' if success else 'FAIL'} — {response}")
        if self._run_source == "ai":
            self._ai_panel.on_step_result(index, response, success)
        elif self._run_source != "dashboard":
            self._auto_panel.on_step_result(index, response, success)

    def _on_suite_finished(self, all_passed, results):
        if self._run_source == "ai":
            self._run_source = None
            self._dashboard.set_busy(False, "AI diagnostic queries finished · ready to refresh")
            self._ai_panel.on_execution_finished(results)
        elif self._run_source == "dashboard":
            self._dashboard_results.extend(results)
            snapshot = parse_snapshot(self._dashboard_results)
            if not self._dashboard_extended and self._pending_suite is None:
                self._dashboard_extended = True
                steps = cell_steps(snapshot)
                if steps:
                    self._runner.run(steps)
                    return
            self._run_source = None
            self._dashboard.show_snapshot(snapshot)
            self._dashboard.set_busy(False)
            if self._pending_suite is not None:
                steps, self._pending_suite = self._pending_suite, None
                self._start_suite(steps)
        else:
            self._run_source = None
            self._auto_panel.on_suite_finished(all_passed, results)
            self._dashboard.set_busy(False, "Automation finished · ready to refresh")

    def _apply_release_state(self, revoked, message):
        self._auto_panel.set_execution_enabled(not revoked)
        self._dashboard.set_execution_enabled(not revoked)
        self._ai_panel.set_execution_enabled(not revoked)
        if revoked:
            self._pending_suite = None
            self._auto_panel._on_stop()
            self._runner.stop()
            self._run_source = None
            self._manual_timer.stop()
            if self._serial.is_connected:
                self._on_disconnect()
            self._cmd_panel._param_bar.hide()
            self._terminal.append_error(message)
            self._auto_panel._run_btn.setEnabled(False)
            self._status_bar.showMessage("This version is no longer supported. Please update ModemDiag.")
        self._refresh_release_banner()
        self._port_selector.setEnabled(not revoked)
        self._terminal.set_input_enabled(not revoked and self._serial.is_connected)

    def _refresh_release_banner(self):
        guard = self._release_guard
        if guard.revoked:
            text = guard.message + " Your saved files remain available."
            if guard.update_available:
                text += " " + guard.update_message
            background, foreground, border = "#3d1a1a", "#ffa198", "#f85149"
        elif guard.update_available:
            text = guard.update_message + " You can continue using this version."
            background, foreground, border = "#332800", "#ffd977", "#e3b341"
        else:
            self._release_banner.hide()
            return
        set_style(self._release_banner, f"QWidget#releaseBanner{{background:{background};border-bottom:1px solid {border};}}"
            f"QLabel{{background:transparent;color:{foreground};border:none;}}")
        self._release_notice.setText(text)
        self._release_banner.show()

    def _open_releases(self):
        # Use the project's fixed release page, never a URL supplied by the policy.
        if not QDesktopServices.openUrl(QUrl(__url__ + "/releases")):
            self._status_bar.showMessage("Open " + __url__ + "/releases to download an update.")

    def _show_release_status(self):
        guard = self._release_guard
        box = QMessageBox(self)
        box.setWindowTitle("Updates")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"ModemDiag {__version__}")
        if guard.revoked:
            summary = guard.message or "This version is no longer supported. Please update ModemDiag."
        elif guard.update_available:
            summary = guard.update_message
        elif guard.state.policy is not None:
            summary = "No newer version is announced in the available release information."
        else:
            summary = "Update information is not available yet."
        box.setInformativeText(
            summary + "\n\nUpdates are checked automatically. Installation is manual.")
        box.setDetailedText(
            "Check status: " + guard.status + "\n\n"
            "Update information comes from a public file hosted on GitHub, checked at startup "
            "and every five minutes. ModemDiag sends no logs, modem data or device identifiers "
            "with this request. GitHub receives ordinary connection information, including "
            "the source IP address, under its privacy policy.\n\n"
            "GitHub privacy policy: https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement\n\n"
            "A signed release restriction can block modem operations on an unsupported version. "
            "Previously accepted restrictions still apply offline. A normal update notice "
            "does not block commands. Only a newer signed policy can change the recorded decision.\n\n"
            "Policy: " + POLICY_URL)
        box.setStandardButtons(QMessageBox.StandardButton.Close)
        releases = box.addButton("View releases", QMessageBox.ButtonRole.ActionRole)
        releases.clicked.connect(self._open_releases)
        box.exec()

    def _show_config_location(self):
        location = escape(str(get_user_dir()))
        QMessageBox.information(
            self, "Configuration location",
            f"<p>User data folder: <tt>{location}</tt></p>"
            "<p><b>user_commands.yaml</b>: edits, removed commands, vendors and layout.<br>"
            "<b>quick_send.yaml</b>: favorites.<br>"
            "<b>appearance.json</b>: dark/light theme.<br>"
            "<b>ai-settings.json</b>: AI provider, model and API URL; excludes API keys.<br>"
            "<b>macros/</b>: saved user automations.<br>"
            "<b>logs/</b>: session logs.</p>"
            "<p>Changes are saved automatically. Undo/Redo is available during this session. "
            "Automation edits need Save.</p>"
            "<p>Use <b>Settings → Export configuration…</b> to save a ZIP backup. "
            "On another PC, use <b>Settings → Import configuration…</b>. Matching files "
            "are replaced after confirmation; a backup of existing settings is kept in "
            "<b>backups/</b>. Other automations and logs are kept. No restart is needed.</p>"
            "<p>Updates to the installed executable keep this folder. For source runs, "
            "export before moving or replacing the project directory. Archives exclude "
            "logs, AI API keys and chats. Commands and automations can contain credentials; "
            "keep the archive private. This backs up the application library, not settings "
            "stored inside the modem.</p>",
        )

    def _show_how_to_use(self):
        QMessageBox.information(
            self, "How to Use ModemDiag",
            "<p><b>Connect:</b> choose a serial port and baud rate, then click Connect.</p>"
            "<p><b>Dashboard:</b> click Refresh for operator, radio, signal, SIM and CS/PS/IMS "
            "registration. Live refreshes every ten seconds while the dashboard is visible, "
            "pausing during automation and manual queries. Missing or unsupported information "
            "shows as Not reported. Cell details depend on the modem and registration settings.</p>"
            "<p><b>Explore the network:</b> click cells in the schematic or table for radio details. "
            "Select RSSI, RSRP, RSRQ or SINR above the history chart. Data contexts lists APNs, "
            "activation and IP addresses; Network events records observed state and cell changes. "
            "The schematic does not locate towers. Terminal opens the AT traffic alongside the dashboard.</p>"
            "<p><b>AI Diag ★ Beta:</b> enter an API key and model, then Test AI connection. "
            "Chat only gives advice; Review &amp; run proposes commands; Auto diagnostics runs approved "
            "read queries for up to four rounds. Review changes are unchecked and manual-only commands "
            "must be run separately. Preview shared context before sending. Stop cancels AI requests "
            "and its command sequence. API keys and chats stay in memory.</p>"
            "<p><b>Commands:</b> choose a vendor; use + beside Vendor to add your own. "
            "The model selector beside Search filters scoped entries; All commands keeps the full library. "
            "Click a command to send it. ⚙ opens parameter fields: "
            "Get reads the value, Caps lists supported values, Set sends your inputs. "
            "Availability depends on the command and module firmware.</p>"
            "<p><b>Edit:</b> right-click a command to edit, remove or favorite it. "
            "Green indicates a user-added or edited command. Use ↶ / ↷ or the Edit "
            "menu to undo/redo command-library changes during this session. "
            "Undo changes the library; it does not reverse commands sent to the modem.</p>"
            "<p><b>Organize:</b> drag a command onto another in the same category to swap "
            "positions. Drag sidebar categories to reorder; double-click to rename. "
            "Use + below the categories to add one. Search finds names and descriptions.</p>"
            "<p><b>Automation:</b> choose or create a suite, edit its steps and expected "
            "responses, set timeouts, delays and failure policies, then Save and Run. "
            "Choose Regex for exact state checks. Completion / payload provides completion and failure "
            "patterns, a payload prompt, exact text or hex bytes, and the final success line. "
            "Stop ends execution. Manual commands are blocked while a suite runs.</p>"
            "<p><b>Logs:</b> every connection starts a log automatically. Traffic and step "
            "results are flushed to disk during execution. The logging button at the bottom "
            "stops or resumes recording. Session → Open logs folder finds the saved files; "
            "File → Save Log… exports a copy. No final save is required.</p>"
            "<p><b>Configuration:</b> Settings shows and opens the storage folder. "
            "Export configuration creates a ZIP backup; Import configuration restores it "
            "on this PC or another one and backs up existing settings first. Save automation "
            "edits before exporting. Installed updates keep your user data.</p>"
            "<p><b>Appearance:</b> use the Light theme / Dark theme button at the bottom "
            "to switch colors. Your choice is saved locally.</p>"
            f"<p><a href='{__url__}#usage'>GitHub usage guide</a> · "
            f"<a href='{__url__}/issues'>Report an issue</a></p>",
        )

    # ── Menu actions ───────────────────────────────────────────────────────────

    def _open_folder(self, folder):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.information(self, "Folder location", str(folder))

    def _export_configuration(self):
        if self._auto_panel._dirty:
            QMessageBox.information(self, "Save automation first", "Save or discard your automation edits before exporting configuration.")
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, "Export configuration", "ModemDiag-configuration.zip", "Configuration archive (*.zip)")
        if not destination:
            return
        if not destination.lower().endswith(".zip"):
            destination += ".zip"
        try:
            export_configuration(get_user_dir(), destination)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Configuration exported",
            f"Saved configuration to:\n{destination}\n\nOn the other PC, choose Settings → Import configuration…\n"
            "Includes command edits, favorites, saved automations, theme and AI preferences. "
            "Logs, API keys and chats are excluded. Custom commands or scripts may contain credentials; keep this archive private.")

    def _import_configuration(self):
        if (self._serial.is_connected or self._runner.is_running or self._ai_panel.is_controlling
                or self._ai_panel._client.busy):
            QMessageBox.information(self, "Finish the session first", "Disconnect the modem and finish AI requests before importing configuration.")
            return
        if self._auto_panel._dirty:
            QMessageBox.information(self, "Save automation first", "Save or discard your automation edits before importing configuration.")
            return
        source, _ = QFileDialog.getOpenFileName(self, "Import configuration", "", "Configuration archive (*.zip)")
        if not source:
            return
        try:
            files = read_archive(source)
            if not files:
                raise ValueError("This archive contains no saved settings or automations.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        preview = "\n".join(sorted(files)[:15])
        if len(files) > 15:
            preview += f"\n… and {len(files) - 15} more"
        answer = QMessageBox.question(self, "Import configuration",
            f"Import {len(files)} files? Matching settings and automations will be replaced.\n\n{preview}\n\n"
            "Existing configuration will be backed up in the user data folder's backups directory. "
            "Other files and logs are kept. Command undo history will be cleared.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            backup = import_configuration(get_user_dir(), files)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self._cmd_panel.reload_configuration()
        self._auto_panel._current_path = ""
        self._auto_panel._load({"name": "", "steps": []})
        self._auto_panel._refresh_list()
        self._auto_panel._run_btn.setEnabled(False)
        self._ai_panel.reload_settings()
        self._theme = load_theme(self._appearance_file)
        apply_theme(QApplication.instance(), self._theme, self)
        self._update_theme_button()
        QMessageBox.information(self, "Configuration imported", f"Configuration is ready to use.\n\nPrevious settings backup:\n{backup}")

    def _update_logging_state(self):
        active = self._logger.active
        connected = self._serial.is_connected
        failed = bool(self._logger.last_error)
        self._log_btn.setText("● Logging" if active else "Log error" if failed else "Logging off")
        self._log_btn.setEnabled(connected)
        self._log_btn.setToolTip(
            f"Saving continuously to {self._log_path}\nClick to stop logging."
            if active else (f"Logging failed: {self._logger.last_error}\nClick to retry."
                           if failed else "Click to start saving a new log. Every connection starts logging automatically."))
        set_style(self._log_btn, f"color:{'#3fb950' if active else '#f85149' if failed else '#8b949e'};")
        self._start_log_action.setEnabled(connected)
        self._stop_log_action.setEnabled(active)

    def _on_logging_error(self, message):
        self._update_logging_state()
        self._terminal.append_error(f"Logging stopped: {message}. Use Session → Start new log to retry.")
        self._status_bar.showMessage(f"Logging failed: {message}")

    def _start_logging(self):
        if not self._serial.is_connected:
            return
        path = self._logger.start(self._serial.port_name, self._baudrate)
        if path:
            self._log_path = path
            self._logger.info(f"Serial settings: {self._serial.settings}")
            self._terminal.append_info(f"Saving log continuously: {path}")
            self._status_bar.showMessage(f"Saving log continuously: {path}")
        self._update_logging_state()

    def _stop_logging(self):
        self._logger.stop()
        self._update_logging_state()
        self._terminal.append_info("Logging stopped. Recorded traffic is already saved.")

    def _toggle_logging(self):
        if self._logger.active:
            self._stop_logging()
        else:
            self._start_logging()

    def _save_log(self):
        if not self._log_path:
            QMessageBox.information(self, "No Log",
                                    "Start a session first by connecting to a port.")
            return
        dst, _ = QFileDialog.getSaveFileName(
            self, "Save Log As", os.path.basename(self._log_path),
            "Log files (*.log);;All files (*)")
        if dst:
            try:
                if Path(dst).resolve() != Path(self._log_path).resolve():
                    shutil.copy2(self._log_path, dst)
            except OSError as exc:
                QMessageBox.warning(self, "Save log failed", str(exc))

    def _show_about(self):
        QMessageBox.about(
            self, "About ModemDiag",
            f"<h2>🔧 ModemDiag  <small>v{__version__}</small></h2>"
            f"<p><b>Author:</b> {__author__}</p>"
            f"<p><b>Project code license:</b> {__license__}</p>"
            "<p>The combined open-source executable is distributed under GPLv3 terms. "
            "You may modify and redistribute under the applicable licenses. No warranty. "
            "See Help → Licenses for full notices and matching source downloads.</p>"
            "<p>For version information and available downloads, see Help → Updates.</p>"
            "<hr>"
            "<p>Open-source AT command tester &amp; cellular modem diagnostic tool.</p>"
            "<p>Independent project. Manufacturer names identify compatibility; "
            "no vendor sponsorship, endorsement or certification is claimed. "
            "Trademarks belong to their respective owners.</p>"
            f"<p><a href='{__url__}#license'>Licenses and source</a> · "
            f"<a href='{__url__}'>GitHub</a></p>"
            "<hr>"
            "<p><b>Cross-platform:</b><br>"
            "Linux: <tt>/dev/ttyUSB*</tt>, <tt>/dev/ttyACM*</tt><br>"
            "Windows: <tt>COM1…COM99</tt></p>"
        )

    def _show_licenses(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("ModemDiag — Licenses")
        dialog.resize(680, 440)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setOpenLinks(False)
        link_color = theme_color("#58a6ff")
        text.setHtml(
            f"<h2>ModemDiag {__version__}</h2>"
            "<p>ModemDiag's own source code is licensed under the <b>MIT License</b>. "
            "The combined application is distributed under <b>GPLv3</b> terms because it "
            "includes open-source PyQt6. Third-party components retain their respective "
            "licenses and copyright notices. You may modify and redistribute the "
            "application under the applicable terms.</p>"
            "<p>AT-command features help users configure, test and communicate with compatible "
            "IoT devices. This is an independent project; no manufacturer sponsorship, "
            "endorsement or certification is claimed. Manufacturer names identify compatibility; "
            "trademarks belong to their respective owners.</p>"
            "<p>Provided without warranty, to the extent permitted by applicable law. "
            "Compatibility, continued maintenance and support are not guaranteed.</p>"
            "<p>The matching source archive is a separate download on the same release page "
            "as the Windows and Linux packages. It includes application source, build scripts "
            "and dependency-source records.</p>"
            f"<p><a style='color:{link_color}' href='modemdiag:licenses'>License</a>"
            f" &nbsp;·&nbsp; <a style='color:{link_color}' "
            f"href='{__url__}/releases/tag/v{__version__}'>Source code</a></p>"
        )
        def open_link(url):
            if url.toString() == "modemdiag:licenses":
                self._show_license_notices(dialog)
            else:
                QDesktopServices.openUrl(url)
        text.anchorClicked.connect(open_link)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_license_notices(self, parent=None):
        if getattr(sys, "frozen", False):
            candidates = [Path(sys.executable).resolve().parent / "NOTICES.txt",
                          Path(sys._MEIPASS) / "release" / "NOTICES.txt"]
        else:
            root = get_project_dir()
            candidates = [root / "LICENSE", root / "docs" / "THIRD_PARTY_NOTICES.md"]
        documents = []
        for path in candidates:
            try:
                documents.append(path.read_text(encoding="utf-8"))
                if getattr(sys, "frozen", False):
                    break
            except OSError:
                continue
        dialog = QDialog(parent or self)
        dialog.setWindowTitle("ModemDiag — Full license notices")
        dialog.resize(760, 580)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        text.setPlainText("\n\n".join(documents) if documents else
                          "License notices could not be read. See the NOTICES.txt supplied "
                          "with your release download and the project's GitHub license guide.")
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def closeEvent(self, event):
        self._ai_panel.stop("Application closing.", quiet=True)
        self._release_guard.stop()
        self._dashboard._timer.stop()
        self._manual_timer.stop()
        self._pending_suite = None
        self._runner.stop()
        self._port_selector.shutdown()
        if self._serial.is_connected:
            self._serial.disconnect()
        if self._logger.active:
            self._logger.stop()
        event.accept()
