"""
ui/terminal_panel.py
AT terminal — colour-coded scrollable output + command input with history.
"""
from modemdiag.ui.theme import set_style, set_tooltip
import datetime
import re
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser,
    QLineEdit, QPushButton, QCheckBox, QToolBar, QSizePolicy, QLabel,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QTextCursor, QKeyEvent, QFont, QAction

from modemdiag.core.at_parser import split_lines, LineType
from modemdiag.ui.styles import COLOR


class TerminalPanel(QWidget):
    """
    Emits:
        command_send_requested(command_str)
    """

    command_send_requested = pyqtSignal(str)

    MAX_HISTORY = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._hist_idx: int      = -1
        self._timestamps          = True
        self._autoscroll          = True
        self._last_tx: str        = ""
        self._build_ui()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ────────────────────────────────────────────────────────────
        bar = QToolBar()
        bar.setMovable(False)
        set_style(bar, "QToolBar { border-bottom: 1px solid #21262d; padding: 2px 6px; }")

        self._ts_cb = QCheckBox("Timestamps")
        self._ts_cb.setChecked(True)
        self._ts_cb.stateChanged.connect(lambda s: setattr(self, "_timestamps", bool(s)))
        bar.addWidget(self._ts_cb)

        self._scroll_cb = QCheckBox("Auto-scroll")
        self._scroll_cb.setChecked(True)
        self._scroll_cb.stateChanged.connect(lambda s: setattr(self, "_autoscroll", bool(s)))
        bar.addWidget(self._scroll_cb)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        clear_act = QAction("🗑  Clear", self)
        set_tooltip(clear_act, "Clear terminal output")
        clear_act.triggered.connect(self.clear)
        bar.addAction(clear_act)

        layout.addWidget(bar)

        # ── Output area ────────────────────────────────────────────────────────
        self._output = QTextBrowser()
        self._output.setReadOnly(True)
        self._output.setOpenLinks(False)
        self._output.setFont(QFont("Cascadia Code, Consolas, Courier New", 12))
        layout.addWidget(self._output, 1)

        # ── Input row ──────────────────────────────────────────────────────────
        input_row = QWidget()
        input_row.setObjectName("terminalInputRow")
        set_style(input_row, "QWidget#terminalInputRow { background-color: #161b22; border-top: 1px solid #21262d; }")
        row_lay = QHBoxLayout(input_row)
        row_lay.setContentsMargins(8, 6, 8, 6)
        row_lay.setSpacing(8)

        prompt = QLabel(">")
        set_style(prompt, "color: #79c0ff; font-family: 'Cascadia Code','Consolas','Courier New';"
            "font-size: 14px; font-weight: bold; background: transparent;")
        row_lay.addWidget(prompt)

        self._input = _HistoryLineEdit(self._history)
        self._input.setPlaceholderText("Type AT command and press Enter…")
        self._input.returnPressed.connect(self._on_send)
        row_lay.addWidget(self._input, 1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("primary")
        self._send_btn.setFixedWidth(80)
        self._send_btn.clicked.connect(self._on_send)
        row_lay.addWidget(self._send_btn)

        layout.addWidget(input_row)

    # ── Public API ─────────────────────────────────────────────────────────────

    def append_tx(self, command: str):
        # Remember it so the modem's ATE1 echo isn't printed a second time.
        # Automation steps arrive as "[3] AT+CSQ" — the echo carries no marker.
        self._last_tx = re.sub(r"^\[\d+\]\s*", "", command).strip()

        ts   = self._ts() if self._timestamps else ""
        html = (
            f'<span style="color:{COLOR["timestamp"]}">{ts}</span>'
            f'<span style="color:{COLOR["tx"]}; font-weight:bold; '
            f'font-family:\'Cascadia Code\',\'Consolas\',monospace">'
            f' {self._esc(command)}</span>'
        )
        self._append_html(html)

    def append_rx_raw(self, raw: str):
        for line, kind in split_lines(raw):
            self._append_rx_line(line, kind)

    def append_info(self, message: str):
        ts   = self._ts() if self._timestamps else ""
        html = (
            f'<span style="color:{COLOR["timestamp"]}">{ts}</span>'
            f'<span style="color:{COLOR["info"]}"> ℹ  {self._esc(message)}</span>'
        )
        self._append_html(html)

    def append_error(self, message: str):
        ts   = self._ts() if self._timestamps else ""
        html = (
            f'<span style="color:{COLOR["timestamp"]}">{ts}</span>'
            f'<span style="color:{COLOR["error"]}; font-weight:bold"> ✖  {self._esc(message)}</span>'
        )
        self._append_html(html)

    def clear(self):
        self._output.clear()

    def set_input_enabled(self, enabled: bool):
        self._input.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _append_rx_line(self, line: str, kind: LineType):
        # ATE1 echo: the modem mirrors the command back. It is not a response,
        # so consume it once and drop it — the command is already on screen.
        if self._last_tx and line.strip().upper() == self._last_tx.upper():
            self._last_tx = ""
            return

        ts = self._ts() if self._timestamps else ""
        if kind == LineType.OK:
            colour = COLOR["ok"]
        elif kind in (LineType.ERROR, LineType.CME_ERROR, LineType.CMS_ERROR):
            colour = COLOR["error"]
        elif kind == LineType.URC:
            colour = COLOR["urc"]
        else:
            colour = COLOR["data"]

        html = (
            f'<span style="color:{COLOR["timestamp"]}">{ts}</span>'
            f'<span style="color:{colour};'
            f'font-family:\'Cascadia Code\',\'Consolas\',monospace">'
            f' {self._esc(line)}</span>'
        )
        self._append_html(html)

    def _append_html(self, html: str):
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html + "<br>")
        if self._autoscroll:
            self._output.setTextCursor(cursor)
            self._output.ensureCursorVisible()

    def _on_send(self):
        cmd = self._input.text().strip()
        if not cmd:
            return
        # Add to history
        if not self._history or self._history[-1] != cmd:
            self._history.append(cmd)
            if len(self._history) > self.MAX_HISTORY:
                self._history.pop(0)
        self._hist_idx = -1
        self._input.clear()
        self.command_send_requested.emit(cmd)

    @staticmethod
    def _ts() -> str:
        return (
            f'<span style="font-family:\'Cascadia Code\',monospace; font-size:10px;">'
            f'{datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]}</span> '
        )

    @staticmethod
    def _esc(text: str) -> str:
        return (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))


class _HistoryLineEdit(QLineEdit):
    """QLineEdit with ↑/↓ history navigation."""

    def __init__(self, history: list, parent=None):
        super().__init__(parent)
        self._history = history
        self._idx     = -1

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Up:
            if self._history:
                self._idx = max(0, (self._idx if self._idx >= 0 else len(self._history)) - 1)
                self.setText(self._history[self._idx])
        elif event.key() == Qt.Key.Key_Down:
            if self._idx >= 0:
                self._idx += 1
                if self._idx >= len(self._history):
                    self._idx = -1
                    self.clear()
                else:
                    self.setText(self._history[self._idx])
        else:
            self._idx = -1
            super().keyPressEvent(event)
