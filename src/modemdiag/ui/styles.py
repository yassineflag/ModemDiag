"""
ui/styles.py
Global dark theme — GitHub-dark inspired with terminal aesthetics.
"""

DARK_THEME = """
/* ── Reset ──────────────────────────────────────────────────── */
* { outline: none; }

QMainWindow, QDialog {
    background-color: #0d1117;
    color: #e6edf3;
}
QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: 'Segoe UI', 'Ubuntu', 'Helvetica Neue', sans-serif;
    font-size: 13px;
}

/* ── Splitter ────────────────────────────────────────────────── */
QSplitter::handle          { background-color: #21262d; }
QSplitter::handle:vertical { height: 2px; }
QSplitter::handle:horizontal { width: 2px; }

/* ── Toolbar ─────────────────────────────────────────────────── */
QToolBar {
    background-color: #161b22;
    border-bottom: 1px solid #21262d;
    padding: 4px 8px;
    spacing: 6px;
}
QToolButton {
    background-color: transparent;
    color: #8b949e;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
}
QToolButton:hover   { background-color: #21262d; color: #e6edf3; border-color: #30363d; }
QToolButton:pressed { background-color: #1c2128; }

/* ── Menu bar ────────────────────────────────────────────────── */
QMenuBar {
    background-color: #161b22;
    color: #e6edf3;
    border-bottom: 1px solid #21262d;
}
QMenuBar::item:selected { background-color: #21262d; border-radius: 4px; }
QMenu {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item          { padding: 6px 20px; border-radius: 4px; }
QMenu::item:selected { background-color: #21262d; }
QMenu::separator     { height: 1px; background-color: #30363d; margin: 4px; }

/* ── Status bar ──────────────────────────────────────────────── */
QStatusBar {
    background-color: #161b22;
    color: #8b949e;
    border-top: 1px solid #21262d;
    font-size: 12px;
    padding: 2px 8px;
}

/* ── Tab widget ──────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #21262d;
    border-radius: 0 6px 6px 6px;
    background-color: #0d1117;
    top: -1px;
}
QTabBar::tab {
    background-color: #0d1117;
    color: #8b949e;
    padding: 8px 18px;
    border: 1px solid transparent;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    margin-right: 2px;
    min-width: 80px;
}
QTabBar::tab:selected {
    background-color: #161b22;
    color: #58a6ff;
    border-color: #21262d;
    border-bottom: 2px solid #58a6ff;
}
QTabBar::tab:hover:!selected { background-color: #1c2128; color: #e6edf3; }

/* ── GroupBox ────────────────────────────────────────────────── */
QGroupBox {
    background-color: #161b22;
    border: 1px solid #21262d;
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #8b949e;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}

/* ── Labels ──────────────────────────────────────────────────── */
QLabel              { color: #e6edf3; background-color: transparent; }
QLabel#muted        { color: #8b949e; font-size: 12px; }
QLabel#caption      { color: #58a6ff; font-weight: 600; font-size: 11px;
                       text-transform: uppercase; letter-spacing: 0.08em; }
QLabel#badge_ok     { color: #3fb950; font-weight: 700; }
QLabel#badge_error  { color: #f85149; font-weight: 700; }

/* ── Buttons ─────────────────────────────────────────────────── */
QPushButton {
    background-color: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 13px;
    min-height: 28px;
}
QPushButton:hover    { background-color: #292e36; border-color: #58a6ff; color: #fff; }
QPushButton:pressed  { background-color: #1c2128; }
QPushButton:disabled { background-color: #161b22; color: #484f58; border-color: #21262d; }

/* Primary — Connect / Run */
QPushButton#primary {
    background-color: #1f6feb;
    color: #fff;
    border: 1px solid #388bfd;
    font-weight: 600;
}
QPushButton#primary:hover    { background-color: #388bfd; }
QPushButton#primary:pressed  { background-color: #1158c7; }
QPushButton#primary:disabled { background-color: #1158c7; color: #6e9ed6; }

/* Danger — Disconnect */
QPushButton#danger {
    background-color: #6e2020;
    color: #ffa198;
    border: 1px solid #f85149;
}
QPushButton#danger:hover { background-color: #8a2929; }

/* Success */
QPushButton#success {
    background-color: #1a4731;
    color: #56d364;
    border: 1px solid #3fb950;
}
QPushButton#success:hover { background-color: #206040; }

/* Generic AT command button (quick send sidebar) */
QPushButton#cmd_btn {
    background-color: #161b22;
    color: #79c0ff;
    border: 1px solid #21262d;
    border-radius: 4px;
    padding: 3px 6px;
    font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
    font-size: 11px;
    font-weight: 600;
    text-align: left;
    min-height: 22px;
}
QPushButton#cmd_btn:hover   { background-color: #1c2128; border-color: #58a6ff; color: #fff; }
QPushButton#cmd_btn:pressed { background-color: #0d1117; }

/* Small utility */
QPushButton#small {
    padding: 3px 8px;
    font-size: 11px;
    min-height: 22px;
}

/* ── Line edit ───────────────────────────────────────────────── */
QLineEdit {
    background-color: #0d1117;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    selection-background-color: #1f6feb;
}
QLineEdit:focus    { border-color: #58a6ff; }
QLineEdit:disabled { background-color: #161b22; color: #484f58; }

/* ── Text edit (terminal output) ─────────────────────────────── */
QTextEdit, QPlainTextEdit {
    background-color: #010409;
    color: #e6edf3;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 8px;
    font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
    font-size: 12px;
    selection-background-color: #1f6feb;
}

/* ── ComboBox ────────────────────────────────────────────────── */
QComboBox {
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 28px;
    font-size: 13px;
}
QComboBox:hover  { border-color: #58a6ff; }
QComboBox:focus  { border-color: #58a6ff; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: right center;
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #8b949e;
}
QComboBox::down-arrow:hover { border-top-color: #58a6ff; }
QComboBox QAbstractItemView {
    background-color: #1c2128;
    border: 1px solid #30363d;
    border-radius: 6px;
    selection-background-color: #21262d;
    selection-color: #58a6ff;
    padding: 4px;
}

/* ── Scroll bar ──────────────────────────────────────────────── */
QScrollBar:vertical {
    background-color: transparent;
    width: 8px; margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #30363d;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background-color: #484f58; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

QScrollBar:horizontal {
    background-color: transparent;
    height: 8px; margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #30363d;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background-color: #484f58; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

/* ── Scroll area ─────────────────────────────────────────────── */
QScrollArea { border: none; background-color: transparent; }
QScrollArea > QWidget > QWidget { background-color: transparent; }

/* ── Table / List ────────────────────────────────────────────── */
QTableWidget, QListWidget, QTreeWidget {
    background-color: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    gridline-color: #21262d;
    alternate-background-color: #1c2128;
    color: #e6edf3;
}
QTableWidget::item, QListWidget::item, QTreeWidget::item { padding: 4px 8px; }
QTableWidget::item:selected, QListWidget::item:selected, QTreeWidget::item:selected {
    background-color: #1f3a5f;
    color: #58a6ff;
}
QTableWidget::item:hover, QListWidget::item:hover { background-color: #1c2128; }
QHeaderView::section {
    background-color: #161b22;
    color: #8b949e;
    border: none;
    border-bottom: 1px solid #30363d;
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

/* ── Checkbox ────────────────────────────────────────────────── */
QCheckBox { color: #e6edf3; spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #30363d;
    border-radius: 3px;
    background-color: #0d1117;
}
QCheckBox::indicator:checked { background-color: #1f6feb; border-color: #388bfd; }
QCheckBox::indicator:hover   { border-color: #58a6ff; }

/* ── Progress bar ────────────────────────────────────────────── */
QProgressBar {
    background-color: #21262d;
    border: 1px solid #30363d;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background-color: #1f6feb;
    border-radius: 4px;
}

/* ── Spin box ────────────────────────────────────────────────── */
QSpinBox {
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 28px;
}
QSpinBox:focus { border-color: #58a6ff; }

/* ── Tooltip ─────────────────────────────────────────────────── */
QToolTip {
    background-color: #1c2128;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 5px;
    padding: 5px 8px;
    font-size: 12px;
}
"""

# Colour tokens for programmatic use (HTML terminal output)
COLOR = {
    "tx":        "#79c0ff",   # blue  – TX commands
    "ok":        "#3fb950",   # green – OK
    "error":     "#f85149",   # red   – ERROR / CME / CMS
    "urc":       "#d29922",   # amber – URCs
    "data":      "#c9d1d9",   # light grey – data lines
    "timestamp": "#484f58",   # dim – timestamps
    "info":      "#8b949e",   # muted – info messages
}
