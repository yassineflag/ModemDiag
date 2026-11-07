"""
ui/main_window.py
Main application window.
Layout: Connection toolbar | [Commands / Automation tabs] | [Terminal]
"""
from modemdiag.ui.theme import set_style, theme_color
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
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QAction, QKeySequence, QIcon, QDesktopServices

from modemdiag.core.serial_manager  import SerialManager
from modemdiag.core.command_runner  import CommandRunner
from modemdiag.core.logger          import SessionLogger
from modemdiag.core.paths           import logs_dir, assets_dir, get_user_dir, get_project_dir
from modemdiag.core.release_check   import ReleaseGuard
from modemdiag.core.release_policy  import POLICY_URL
from modemdiag.core.appearance      import load_theme, save_theme
from modemdiag.ui.theme            import apply_theme

from modemdiag.ui.port_selector    import PortSelectorWidget
from modemdiag.ui.terminal_panel   import TerminalPanel
from modemdiag.ui.command_panel    import CommandPanel
from modemdiag.ui.automation_panel import AutomationPanel

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
        self._logger   = SessionLogger(str(logs_dir()))
        self._runner   = CommandRunner(self._serial, self)
        self._log_path = ""

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
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)

        # Left: tabbed panels (Commands, Automation)
        self._cmd_panel      = CommandPanel()
        self._auto_panel     = AutomationPanel()

        self._edit_menu.addAction(self._cmd_panel.undo_action)
        self._edit_menu.addAction(self._cmd_panel.redo_action)

        left_tabs = QTabWidget()
        left_tabs.setTabPosition(QTabWidget.TabPosition.North)
        left_tabs.setMinimumWidth(430)
        left_tabs.addTab(self._cmd_panel,  "📋  Commands")
        left_tabs.addTab(self._auto_panel, "⚡  Automation")
        splitter.addWidget(left_tabs)
        splitter.setStretchFactor(0, 0)  # left: don't grow on window resize

        # Right: terminal
        self._terminal = TerminalPanel()
        self._terminal.setMinimumWidth(360)
        splitter.addWidget(self._terminal)
        splitter.setStretchFactor(1, 1)  # right: gets extra space on resize
        splitter.setSizes([720, 480])    # ~60/40 — commands panel is larger
        root.addWidget(splitter, 1)

        # ── Status bar ─────────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
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
        a = QAction("Restore default command library…", self)
        a.triggered.connect(lambda: self._cmd_panel.reset_defaults())
        settings_m.addAction(a)

        sess_m = mb.addMenu("Session")
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
        a = QAction("Release status…", self)
        a.triggered.connect(self._show_release_status)
        help_m.addAction(a)
        a = QAction("Check release status now", self)
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

        self._serial.data_received.connect(self._on_data_received)
        self._serial.error_occurred.connect(self._on_serial_error)
        self._serial.connection_changed.connect(self._on_connection_changed)

        self._terminal.command_send_requested.connect(self._send_command)
        self._cmd_panel.command_triggered.connect(self._send_command)

        self._auto_panel.run_suite_requested.connect(self._start_suite)
        self._auto_panel.stop_requested.connect(self._runner.stop)

        self._runner.step_started.connect(self._auto_panel.on_step_started)
        self._runner.step_result.connect(self._auto_panel.on_step_result)
        self._runner.suite_finished.connect(self._auto_panel.on_suite_finished)

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
        self._log_path = self._logger.start(port, baudrate)
        self._terminal.append_info(f"Connected to {port} @ {baudrate} baud")
        self._terminal.set_input_enabled(True)
        self._status_bar.showMessage(
            f"Connected: {port}  @  {baudrate}  |  Log: {self._log_path}"
        )

    def _on_disconnect(self):
        if self._runner.is_running:
            self._auto_panel._on_stop()
        self._serial.disconnect()
        if self._logger.active:
            self._logger.stop()
        self._terminal.set_input_enabled(False)
        self._terminal.append_info("Disconnected.")
        self._status_bar.showMessage("Disconnected.")

    def _on_connection_changed(self, connected: bool):
        self._port_selector.set_connected(connected)
        if connected:
            set_style(self._conn_lbl, "color:#3fb950; margin-right:12px;")
            self._conn_lbl.setText(f"● {self._serial.port_name}")
        else:
            if self._runner.is_running:
                self._auto_panel._on_stop()
            set_style(self._conn_lbl, "color:#484f58; margin-right:12px;")
            self._conn_lbl.setText("● Disconnected")

    # ── Data flow ──────────────────────────────────────────────────────────────

    def _send_command(self, command: str):
        if self._release_guard.revoked:
            self._terminal.append_error(self._release_guard.message)
            return
        if self._runner.is_running:
            self._terminal.append_error("Stop the automation before sending a manual command.")
            return
        if not self._serial.is_connected:
            self._terminal.append_error(
                "Not connected — select a port and click Connect first."
            )
            return
        ok, err = self._serial.send(command)
        if ok:
            self._terminal.append_tx(command)
            self._logger.tx(command)
        else:
            self._terminal.append_error(f"Send failed: {err}")

    def _on_step_started(self, idx: int, cmd: str, _desc: str):
        self._terminal.append_tx(f"[{idx+1}] {cmd}")
        self._logger.tx(cmd)

    def _on_data_received(self, data: str):
        self._terminal.append_rx_raw(data)
        self._logger.rx(data)
        self._runner.feed(data)

    def _on_serial_error(self, error: str):
        self._terminal.append_error(f"Serial error: {error}")
        self._status_bar.showMessage(f"Serial error: {error}")

    def _start_suite(self, steps):
        if self._release_guard.revoked:
            self._auto_panel._on_stop()
            self._auto_panel._run_btn.setEnabled(False)
            self._terminal.append_error(self._release_guard.message)
            return
        self._runner.run(steps)

    def _apply_release_state(self, revoked, message):
        self._auto_panel.set_execution_enabled(not revoked)
        if revoked:
            self._auto_panel._on_stop()
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
        box = QMessageBox(self)
        box.setWindowTitle("Release status")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(self._release_guard.status)
        box.setInformativeText(
            "ModemDiag checks a signed policy at startup and every five minutes. "
            "Offline use is allowed unless this release was already revoked. "
            "Only a newer signed policy can restore access.\n\n"
            "An update-available notice alone does not block commands. "
            "View releases opens the official GitHub release page; updates are installed manually.\n\n"
            "The check uploads no logs, modem data or device identifiers. "
            "GitHub receives the normal HTTPS request, including your IP address.\n\n"
            "Policy: " + POLICY_URL)
        box.exec()

    def _show_config_location(self):
        location = escape(str(get_user_dir()))
        QMessageBox.information(
            self, "Configuration location",
            f"<p>User data folder: <tt>{location}</tt></p>"
            "<p><b>user_commands.yaml</b>: edits, removed commands, vendors and layout.<br>"
            "<b>quick_send.yaml</b>: favorites.<br>"
            "<b>appearance.json</b>: dark/light theme.<br>"
            "<b>macros/</b>: saved user automations.<br>"
            "<b>logs/</b>: session logs.</p>"
            "<p>Changes are saved automatically. Undo/Redo is available during this session. "
            "Settings → Restore default command library resets commands and layout.</p>",
        )

    def _show_how_to_use(self):
        QMessageBox.information(
            self, "How to Use ModemDiag",
            "<p><b>Connect:</b> choose a serial port and baud rate, then click Connect.</p>"
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
            "<p><b>Configuration:</b> Settings shows the storage location and offers a "
            "default command-library reset. Saved automations and logs are kept.</p>"
            "<p><b>Appearance:</b> use the Light theme / Dark theme button at the bottom "
            "to switch colors. Your choice is saved locally.</p>"
            f"<p><a href='{__url__}#usage'>GitHub usage guide</a> · "
            f"<a href='{__url__}/issues'>Report an issue</a></p>",
        )

    # ── Menu actions ───────────────────────────────────────────────────────────

    def _save_log(self):
        if not self._log_path:
            QMessageBox.information(self, "No Log",
                                    "Start a session first by connecting to a port.")
            return
        dst, _ = QFileDialog.getSaveFileName(
            self, "Save Log As", os.path.basename(self._log_path),
            "Log files (*.log);;All files (*)")
        if dst:
            shutil.copy2(self._log_path, dst)

    def _show_about(self):
        QMessageBox.about(
            self, "About ModemDiag",
            f"<h2>🔧 ModemDiag  <small>v{__version__}</small></h2>"
            f"<p><b>Author:</b> {__author__}</p>"
            f"<p><b>Project code license:</b> {__license__}</p>"
            "<p>The combined open-source executable is distributed under GPLv3 terms. "
            "You may modify and redistribute under the applicable licenses. No warranty. "
            "See Help → Licenses for full notices and matching source downloads.</p>"
            "<p>Release status is checked on GitHub. An authenticated revocation blocks modem "
            "operations; offline use otherwise remains available. See Help → Release status.</p>"
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
        self._release_guard.stop()
        self._runner.stop()
        self._port_selector.shutdown()
        if self._serial.is_connected:
            self._serial.disconnect()
        if self._logger.active:
            self._logger.stop()
        event.accept()
