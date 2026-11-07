"""
ui/command_panel.py
Functional command panel.
  • First category: ⭐ Favorites (loaded from quick_send.yaml — same file as before)
  • Other categories: functional groups with 3GPP (blue) + vendor (amber) + user (green)
  • Right-click any button → Send | Edit | Remove | ⭐ Add to Favorites
  • Drag a button onto another to swap their positions
  • All user edits persisted to user_commands.yaml
"""
from modemdiag.ui.theme import set_style, set_tooltip
import os
from copy import deepcopy
from pathlib import Path
import tempfile
from string import Formatter
import yaml
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QStackedWidget, QScrollArea,
    QLabel, QPushButton, QComboBox,
    QGridLayout, QFrame, QSizePolicy, QMenu,
    QDialog, QFormLayout, QLineEdit,
    QDialogButtonBox, QMessageBox, QInputDialog,
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QMimeData
from PyQt6.QtGui import QDrag, QAction
from modemdiag.core.paths import commands_dir, user_commands_file, favorites_file

# ── Paths (resolved via core.paths — works from source AND as a binary) ────────
COMMANDS_DIR   = str(commands_dir())
USER_FILE      = str(user_commands_file())
FAVORITES_FILE = str(favorites_file())

MANUFACTURER_FILES = {
    "3GPP Only":       "",
    "Sierra Wireless": "sierra_wireless.yaml",
    "Fibocom":         "fibocom.yaml",
    "Quectel":         "quectel.yaml",
    "u-blox":          "ublox.yaml",
    "Telit":           "telit.yaml",
    "SIMCom":          "simcom.yaml",
    "Nordic":          "nordic.yaml",
    "Espressif ESP-AT": "espressif.yaml",
}

# First entry is Favorites (special); rest are functional categories
FUNCTION_TABS = [
    ("⭐ Favorites",  [], []),
    ("Identity",     ["Identity & Info"],
                     ["Version & Identity", "Status & Info"]),
    ("SIM",          ["SIM Card"],
                     ["SIM Info"]),
    ("Network",      ["Network Registration", "Signal Quality", "5G / NR"],
                     ["Network Info", "Network & Signal", "Network",
                      "Radio Access", "Band & RAT", "Network (GT)"]),
    ("Data",         ["Data / PDP"],                  ["Data / IP (GT)"]),
    ("TCP/IP",       [],
                     ["TCP/IP Stack"]),
    ("PSM / NTN",    ["PSM & eDRX"],                  ["Sleep / Low Power"]),
    ("Power",        ["Power & Mode", "Serial / Terminal"],
                     ["Power & Reset", "Power"]),
    ("SMS",          ["SMS"],                         []),
    ("Calls",        ["Calls"],                       []),
    ("GNSS",         [],
                     ["GNSS", "GNSS (Fibocom GT)", "GNSS (GT commands)"]),
    ("IoT/Cloud",    [],
                     ["HTTP / MQTT", "HTTP", "MQTT", "FTP",
                      "SSL/TLS", "Security"]),
    ("Config",       [],
                     ["Configuration", "USB & Interface", "Firmware Update",
                      "Security & Access", "GPIO", "Firmware / Misc",
                      "URC/Status", "Module Control", "Hardware / IO"]),
    ("Other",        [],
                     ["Audio", "Other", "Wi-Fi", "Bluetooth LE"]),
]

_FAV_DEFAULTS = [
    {"label": "ATI",       "command": "ATI"},
    {"label": "CPIN?",     "command": "AT+CPIN?"},
    {"label": "CFUN?",     "command": "AT+CFUN?"},
    {"label": "COPS?",     "command": "AT+COPS?"},
    {"label": "CSQ",       "command": "AT+CSQ"},
    {"label": "CEREG?",    "command": "AT+CEREG?"},
    {"label": "CESQ",      "command": "AT+CESQ"},
    {"label": "CGDCONT?",  "command": "AT+CGDCONT?"},
    {"label": "Echo Off",  "command": "ATE0"},
    {"label": "CMEE=2",    "command": "AT+CMEE=2"},
]


# ── Persistence helpers ────────────────────────────────────────────────────────

def _load_yaml(filename: str) -> dict:
    path = os.path.join(COMMANDS_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_cat_map(data: dict) -> dict:
    categories = {}
    for category in data.get("categories", []):
        categories.setdefault(category["name"], []).extend(category.get("commands", []))
    return categories


def _load_user() -> dict:
    if not os.path.exists(USER_FILE):
        return {"added": [], "removed": []}
    with open(USER_FILE, "r", encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    d.setdefault("added",   [])
    d.setdefault("removed", [])
    return d


def _save_user(data: dict):
    _write_yaml(USER_FILE, data)


def _write_yaml(filename, data):
    """Replace a config file only after its complete contents are written."""
    path = Path(filename)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, delete=False) as f:
            temporary = f.name
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _load_favorites() -> list:
    if os.path.exists(FAVORITES_FILE):
        try:
            with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
                d = yaml.safe_load(f) or []
            if isinstance(d, list):
                return d
        except Exception:
            pass
    return deepcopy(_FAV_DEFAULTS)


def _save_favorites(favs: list):
    _write_yaml(FAVORITES_FILE, favs)


# ── Panel ──────────────────────────────────────────────────────────────────────

class CommandPanel(QWidget):
    """Emits: command_triggered(command_str)"""
    command_triggered = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._3gpp          = _build_cat_map(_load_yaml("3gpp.yaml"))
        self._vendor_cache: dict = {}
        self._vendor_info: dict = {}
        self._profile_choices: dict = {}
        self._vendor_color  = "#e3b341"
        self._current_mfr   = list(MANUFACTURER_FILES.keys())[0]
        self._user          = _load_user()
        self._recover_legacy_parameters()
        self._user_categories: list[str] = self._user.get("user_categories", [])
        self._undo: list = []
        self._redo: list = []
        self._saved_state = self._snapshot()
        self.undo_action = QAction("Undo command library change", self)
        self.redo_action = QAction("Redo command library change", self)
        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)
        self._update_history_actions()
        self._fn_to_page: dict[str, QScrollArea] = {}
        self._pages: list[QScrollArea] = []
        self._build_ui()

    def _recover_legacy_parameters(self):
        """Recover schemas discarded by older versions when only labels were edited."""
        libraries = [self._3gpp] + [_build_cat_map(_load_yaml(filename))
                                   for filename in MANUFACTURER_FILES.values() if filename]
        for entry in self._user.get("added", []):
            if not any(r.get("function") == entry.get("function")
                       and r.get("command") == entry.get("command")
                       for r in self._user.get("removed", [])):
                continue
            tab = next((t for t in FUNCTION_TABS if t[0] == entry.get("function")), None)
            if tab is None:
                continue
            candidates = [cmd for library in libraries for category in tab[1] + tab[2]
                          for cmd in library.get(category, [])
                          if cmd.get("command") == entry.get("command") and cmd.get("params")]
            if candidates and all(c.get("params") == candidates[0].get("params")
                                  for c in candidates):
                for key in ("params", "get", "caps"):
                    if key not in entry and key in candidates[0]:
                        entry[key] = deepcopy(candidates[0][key])

    def _snapshot(self, favorites=None):
        return deepcopy((self._user, _load_favorites() if favorites is None else favorites))

    def _update_history_actions(self):
        self.undo_action.setEnabled(bool(self._undo))
        self.redo_action.setEnabled(bool(self._redo))

    def _commit_config(self, favorites=None):
        state = self._snapshot(favorites)
        _save_user(state[0])
        if favorites is not None:
            _save_favorites(state[1])
        if state != self._saved_state:
            self._undo.append(self._saved_state)
            self._redo.clear()
            self._saved_state = state
        self._update_history_actions()
        self._param_bar.hide()
        if self._search_box.text().strip():
            self._on_search(self._search_box.text())

    def _restore_state(self, state):
        user, favorites = deepcopy(state)
        _save_user(user)
        _save_favorites(favorites)
        self._user = user
        self._saved_state = deepcopy(state)
        self._refresh_structure()
        self._update_history_actions()

    def undo(self):
        if self._undo:
            state = self._undo[-1]
            previous = self._saved_state
            self._restore_state(state)
            self._undo.pop()
            self._redo.append(previous)
            self._update_history_actions()

    def redo(self):
        if self._redo:
            state = self._redo[-1]
            previous = self._saved_state
            self._restore_state(state)
            self._redo.pop()
            self._undo.append(previous)
            self._update_history_actions()

    def _refresh_structure(self):
        """Recreate category/vendor widgets after undo, redo or reset."""
        item = self._cat_list.currentItem()
        selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._user_categories = self._user.get("user_categories", [])
        for page in self._pages:
            self._stack.removeWidget(page)
            page.deleteLater()
        self._pages = []
        for _ in FUNCTION_TABS + [(n, [], []) for n in self._user_categories]:
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._stack.insertWidget(self._stack.count() - 1, page)
            self._pages.append(page)
        self._build_fn_to_page()
        self._build_sidebar_items()
        self._mfr_combo.blockSignals(True)
        self._mfr_combo.clear()
        self._mfr_combo.addItems(list(MANUFACTURER_FILES) + self._user.get("vendors", []))
        if self._current_mfr not in [self._mfr_combo.itemText(i)
                                     for i in range(self._mfr_combo.count())]:
            self._current_mfr = "3GPP Only"
        self._mfr_combo.setCurrentText(self._current_mfr)
        self._mfr_combo.blockSignals(False)
        self._on_mfr(self._current_mfr)
        row = next((i for i in range(self._cat_list.count())
                    if self._cat_list.item(i).data(Qt.ItemDataRole.UserRole) == selected), 0)
        self._cat_list.setCurrentRow(row)
        self._param_bar.hide()
        self._on_search(self._search_box.text())

    def reset_defaults(self):
        answer = QMessageBox.question(
            self, "Restore default command library",
            "Restore bundled commands, favorites, vendors and category layout?\n\n"
            "Custom command-library changes will be removed. Automations and logs "
            "are kept. You can Undo this reset until you close the application.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._user = {"added": [], "removed": []}
            self._commit_config(deepcopy(_FAV_DEFAULTS))
            self._refresh_structure()

    def _add_vendor(self):
        name, ok = QInputDialog.getText(self, "Add Vendor", "Vendor name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        existing = list(MANUFACTURER_FILES) + self._user.get("vendors", [])
        if name.casefold() in {v.casefold() for v in existing}:
            QMessageBox.warning(self, "Vendor exists", "That vendor already exists.")
            return
        self._user.setdefault("vendors", []).append(name)
        self._commit_config()
        self._mfr_combo.addItem(name)
        self._mfr_combo.setCurrentText(name)

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar — compact: dropdown + colored dot legend + save button
        top = QWidget()
        set_style(top, "background:#161b22; border-bottom:1px solid #21262d;")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(8, 4, 8, 4)
        tl.setSpacing(6)

        vendor_lbl = QLabel("Vendor:")
        set_style(vendor_lbl, "color:#8b949e; font-size:11px; background:transparent;")
        tl.addWidget(vendor_lbl)

        self._mfr_combo = QComboBox()
        self._mfr_combo.addItems(list(MANUFACTURER_FILES) + self._user.get("vendors", []))
        self._mfr_combo.setMinimumWidth(120)
        self._mfr_combo.setMaximumWidth(160)
        self._mfr_combo.currentTextChanged.connect(self._on_mfr)
        tl.addWidget(self._mfr_combo)

        add_vendor = QPushButton("+")
        add_vendor.setFixedSize(24, 24)
        compact_style = (
            "QPushButton{padding:0;min-height:0;border-radius:4px;font-size:16px;}"
        )
        set_style(add_vendor, compact_style + "QPushButton{color:#56d364;}")
        set_tooltip(add_vendor, "Add vendor manually")
        add_vendor.clicked.connect(self._add_vendor)
        tl.addWidget(add_vendor)

        tl.addStretch()

        # Colored dot + text legend
        for color, text, tip in [
            ("#79c0ff", "3GPP",   "Blue = 3GPP standard command"),
            ("#e3b341", "Vendor",    "Amber = vendor-specific command"),
            ("#56d364", "User",   "Green = user-added or edited command"),
        ]:
            lbl = QLabel(f"● {text}")
            set_style(lbl, f"color:{color}; font-size:11px; font-weight:600;")
            set_tooltip(lbl, tip)
            tl.addWidget(lbl)

        tl.addSpacing(4)
        undo_btn = QPushButton("↶")
        undo_btn.setFixedSize(24, 24)
        set_style(undo_btn, compact_style)
        set_tooltip(undo_btn, "Undo command library change")
        undo_btn.clicked.connect(self.undo)
        undo_btn.setEnabled(self.undo_action.isEnabled())
        self.undo_action.changed.connect(
            lambda: undo_btn.setEnabled(self.undo_action.isEnabled()))
        tl.addWidget(undo_btn)
        redo_btn = QPushButton("↷")
        redo_btn.setFixedSize(24, 24)
        set_style(redo_btn, compact_style)
        set_tooltip(redo_btn, "Redo command library change")
        redo_btn.clicked.connect(self.redo)
        redo_btn.setEnabled(self.redo_action.isEnabled())
        self.redo_action.changed.connect(
            lambda: redo_btn.setEnabled(self.redo_action.isEnabled()))
        tl.addWidget(redo_btn)
        self._save_btn = QPushButton("💾 Save")
        self._save_btn.setFixedHeight(24)
        set_style(self._save_btn, "QPushButton{background:#21262d;border:1px solid #30363d;color:#8b949e;"
            "border-radius:4px;padding:0 8px;font-size:11px;}"
            "QPushButton:hover{background:#388bfd22;border-color:#388bfd;color:#e6edf3;}")
        self._save_btn.clicked.connect(self._save_all_config)
        tl.addWidget(self._save_btn)

        root.addWidget(top)

        # Search bar
        search_row = QWidget()
        set_style(search_row, "background:#0d1117; border-bottom:1px solid #21262d;")
        sl = QHBoxLayout(search_row)
        sl.setContentsMargins(8, 4, 8, 4)
        sl.setSpacing(6)
        search_icon = QLabel("🔍")
        set_style(search_icon, "background:transparent; font-size:12px;")
        sl.addWidget(search_icon)
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search commands…")
        set_style(self._search_box, "QLineEdit{background:#161b22;border:1px solid #30363d;border-radius:4px;"
            "color:#e6edf3;padding:3px 8px;font-size:11px;}"
            "QLineEdit:focus{border-color:#388bfd;}")
        self._search_box.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(
            lambda: self._on_search(self._search_box.text())
        )
        self._search_box.textChanged.connect(lambda _: self._search_timer.start())
        sl.addWidget(self._search_box, 1)
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(150)
        self._profile_combo.setMaximumWidth(220)
        self._profile_combo.currentIndexChanged.connect(self._on_profile)
        set_tooltip(self._profile_combo, "Model / firmware family. All commands retains the full library; "
                    "a profile shows scoped entries. Check Caps and your firmware manual.")
        sl.addWidget(self._profile_combo)
        root.addWidget(search_row)

        # Body: narrow vertical list + stacked pages
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setHandleWidth(1)

        self._cat_list = QListWidget()
        set_style(self._cat_list, """
            QListWidget {
                border: none;
                border-right: 1px solid #21262d;
                background: #161b22;
            }
            QListWidget::item {
                padding: 8px 10px;
                color: #8b949e;
                font-size: 12px;
            }
            QListWidget::item:selected {
                background: #0d1117;
                color: #58a6ff;
                font-weight: 600;
                border-left: 3px solid #58a6ff;
                padding-left: 7px;
            }
            QListWidget::item:hover:!selected {
                background: #1c2128;
                color: #e6edf3;
            }
        """)
        self._cat_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._cat_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._cat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        _add_cat_btn = QPushButton("+")
        set_tooltip(_add_cat_btn, "Add category")
        _add_cat_btn.setFixedHeight(22)
        set_style(_add_cat_btn, "QPushButton{border-radius:0;border:none;border-top:1px solid #21262d;"
            "color:#56d364;font-size:16px;font-weight:700;background:#161b22;}"
            "QPushButton:hover{background:#1c2128;color:#79c0ff;}")
        _add_cat_btn.clicked.connect(self._add_category)

        _sidebar = QWidget()
        _sidebar.setFixedWidth(110)
        _sidebar_lay = QVBoxLayout(_sidebar)
        _sidebar_lay.setContentsMargins(0, 0, 0, 0)
        _sidebar_lay.setSpacing(0)
        _sidebar_lay.addWidget(self._cat_list, 1)
        _sidebar_lay.addWidget(_add_cat_btn)

        body.addWidget(_sidebar)
        body.setCollapsible(0, False)

        self._stack = QStackedWidget()
        for _ in FUNCTION_TABS:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._stack.addWidget(sa)
            self._pages.append(sa)
        for _ in self._user_categories:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._stack.addWidget(sa)
            self._pages.append(sa)

        # ── Inline param bar lives inside the right column (below command grid only)
        self._param_bar = _ParamBar()
        self._param_bar.send_requested.connect(self.command_triggered)

        right_panel = QWidget()
        right_lay = QVBoxLayout(right_panel)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        right_lay.addWidget(self._stack, 1)       # command grid stretches
        right_lay.addWidget(self._param_bar, 0)   # param bar docks at bottom

        body.addWidget(right_panel)
        body.setCollapsible(1, False)
        body.setSizes([110, 600])

        # Search results page (hidden until query typed)
        self._search_page = QScrollArea()
        self._search_page.setWidgetResizable(True)
        self._search_page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._stack.addWidget(self._search_page)   # last index = search

        self._cat_list.currentRowChanged.connect(self._on_cat_changed)
        self._cat_list.model().rowsMoved.connect(self._on_cat_reordered)
        self._cat_list.itemChanged.connect(self._on_cat_renamed)
        self._cat_list.customContextMenuRequested.connect(self._cat_context_menu)
        root.addWidget(body, 1)

        # Load data and show
        self._build_fn_to_page()
        self._build_sidebar_items()
        self._on_mfr(self._current_mfr)
        self._cat_list.setCurrentRow(0)

    # ── Vendor change ──────────────────────────────────────────────────────────

    def _on_mfr(self, name: str):
        self._current_mfr = name
        fn = MANUFACTURER_FILES.get(name, "")
        if name == "3GPP Only":
            # No vendor file — store empty cache with 3GPP blue tint
            self._vendor_cache.setdefault(name, ({}, "#58a6ff"))
        elif name not in self._vendor_cache and fn:
            raw = _load_yaml(fn)
            self._vendor_info[name] = raw
            self._vendor_cache[name] = (_build_cat_map(raw),
                                        raw.get("color", "#e3b341"))
        _, color = self._vendor_cache.get(name, ({}, "#e3b341"))
        self._vendor_color = color
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        self._profile_combo.addItem("All commands", "")
        for key, label in self._vendor_info.get(name, {}).get("profiles", {}).items():
            self._profile_combo.addItem(label, key)
        self._profile_combo.setCurrentIndex(max(0, self._profile_combo.findData(self._profile_choices.get(name, ""))))
        self._profile_combo.setVisible(self._profile_combo.count() > 1)
        self._profile_combo.blockSignals(False)
        # Tint the combo to reflect the manufacturer color
        set_style(self._mfr_combo, f"QComboBox {{ border-color: {color}55; }}"
            f"QComboBox:focus {{ border-color: {color}; }}")
        self._rebuild_all()

    def _on_profile(self):
        self._profile_choices[self._current_mfr] = self._profile_combo.currentData() or ""
        self._param_bar.hide()
        self._rebuild_all()

    # ── Page population ────────────────────────────────────────────────────────


    def _on_search(self, text: str):
        """Filter all commands across all categories and show results page."""
        text = text.strip().lower()
        if not text:
            self._on_cat_changed(self._cat_list.currentRow())
            return

        self._stack.setCurrentIndex(self._stack.count() - 1)

        results_w = QWidget()
        rl = QVBoxLayout(results_w)
        rl.setContentsMargins(8, 8, 8, 8)
        rl.setSpacing(4)
        found = 0

        for fn_name in [t[0] for t in FUNCTION_TABS[1:]] + self._user_categories:
            for cmd in self._commands_for_function(fn_name):
                haystack = " ".join([cmd.get("name", ""), cmd.get("command", ""),
                                     cmd.get("description", "")]).lower()
                if text in haystack:
                    src = cmd.get("_src", cmd.get("source", "user"))
                    variant = {"gpp": "cmd_3gpp", "vendor": "cmd_vendor",
                               "user": "cmd_user"}[src]
                    btn = self._make_search_result(cmd, fn_name, "", variant)
                    btn.customContextMenuRequested.connect(
                        lambda pos, b=btn, c=dict(cmd), fn=fn_name:
                            self._cmd_ctx_menu(b, pos, c, fn))
                    rl.addWidget(btn)
                    found += 1

        if found == 0:
            no = QLabel(f'No commands match "{text}"')
            set_style(no, "color:#484f58; font-size:12px; padding:20px;")
            no.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rl.addWidget(no)
        rl.addStretch()
        self._search_page.setWidget(results_w)

    def _make_search_result(self, cmd: dict, category: str,
                            color: str, variant: str) -> "_CmdButton":
        """Build a command button for the search results page."""
        name     = cmd.get("name", "")
        template = cmd.get("command", name)
        params   = cmd.get("params", [])
        get_cmd  = cmd.get("get", "")
        caps_cmd = cmd.get("caps", "")
        desc     = cmd.get("description", "")

        btn = _CmdButton(
            command=name or template,
            name=name, description=desc, variant=variant,
            has_params=bool(params or get_cmd or caps_cmd),
            raw_command=template,
        )
        set_tooltip(btn, f"<tt>{template}</tt><br>"
            f"<span style='color:#8b949e'>{category} → {desc}</span>")
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        if params or get_cmd or caps_cmd:
            btn.clicked.connect(
                lambda _n=name, _t=template, _p=list(params), _g=get_cmd, _c=caps_cmd:
                    self._show_param_bar(_n, _t, _p, _g, _c)
            )
        else:
            btn.clicked.connect(lambda cc=template: self._send_direct(cc))
        return btn


    def _rebuild_all(self):
        # Rebuild favorites page (index 0)
        self._fill_favorites(self._pages[0])

        for i, (fn_name, _, _) in enumerate(FUNCTION_TABS[1:], start=1):
            self._fill_page(self._pages[i], fn_name, self._commands_for_function(fn_name))

        for fn_name in self._user_categories:
            page = self._fn_to_page.get(fn_name)
            if page is None:
                continue
            self._fill_page(page, fn_name, self._commands_for_function(fn_name))

        if self._search_box.text().strip():
            self._on_search(self._search_box.text())

    def _commands_for_function(self, fn_name):
        vcats, _ = self._vendor_cache.get(self._current_mfr, ({}, ""))
        tab = next((t for t in FUNCTION_TABS if t[0] == fn_name), (fn_name, [], []))
        gpp_names, vendor_names = tab[1:]
        if fn_name == "Other":
            # A newly introduced YAML category must never silently vanish.
            mapped_gpp = {n for _, names, _ in FUNCTION_TABS for n in names}
            mapped_vendor = {n for _, _, names in FUNCTION_TABS for n in names}
            gpp_names = list(gpp_names) + [n for n in self._3gpp if n not in mapped_gpp]
            vendor_names = list(vendor_names) + [n for n in vcats if n not in mapped_vendor]
        commands = []
        profile = self._profile_combo.currentData() or ""
        for source, categories, names in (("gpp", self._3gpp, gpp_names),
                                           ("vendor", vcats, vendor_names)):
            if source == "gpp" and not self._vendor_info.get(self._current_mfr, {}).get("include_3gpp", True):
                continue
            for name in names:
                for cmd in categories.get(name, []):
                    if source == "vendor" and profile and profile not in cmd.get("profiles", []):
                        continue
                    if not any(r.get("function") == fn_name
                               and r.get("command") == cmd.get("command")
                               and r.get("source", source) == source
                               and r.get("manufacturer", self._current_mfr) == self._current_mfr
                               for r in self._user.get("removed", [])):
                        commands.append({**cmd, "_src": source})
        # Removal markers hide bundled originals, never user replacements.
        commands.extend({**c, "_src": "user"} for c in self._user.get("added", [])
                        if c.get("function") == fn_name
                        and c.get("manufacturer", self._current_mfr) == self._current_mfr)
        return self._apply_order(commands, fn_name)

    def _apply_order(self, cmds: list, fn_name: str) -> list:
        """Re-sort cmds according to user-saved order for this tab, if any."""
        order = self._user.get("order", {}).get(fn_name)
        if not order:
            return cmds
        positions = {command: i for i, command in enumerate(order)}
        return sorted(cmds, key=lambda c: positions.get(c.get("command", ""), len(order)))

    # ── Param dialog ───────────────────────────────────────────────────────────

    def _send_direct(self, cmd: str):
        """Send a plain command and close the param bar if it was open."""
        self._param_bar.hide()
        self.command_triggered.emit(cmd)

    def _show_param_bar(self, name: str, template: str, params: list,
                         get_cmd: str = "", caps_cmd: str = ""):
        self._param_bar.load(name, template, params, get_cmd, caps_cmd)

    # ── Favorites page ─────────────────────────────────────────────────────────

    def _fill_favorites(self, scroll: QScrollArea):
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lbl = QLabel("Favorites")
        set_style(lbl, "color:#ffd700; font-size:10px; font-weight:600;"
            "text-transform:uppercase; letter-spacing:0.08em;")
        lay.addWidget(lbl)

        hint = QLabel("Click to send  •  Right-click to edit / move / remove")
        set_style(hint, "color:#484f58; font-size:10px;")
        lay.addWidget(hint)

        favs  = _load_favorites()
        if not Path(FAVORITES_FILE).exists():
            favs = self._vendor_info.get(self._current_mfr, {}).get("favorites", favs)
        grid  = QWidget()
        g     = QGridLayout(grid)
        g.setSpacing(3)
        g.setContentsMargins(0, 0, 0, 0)
        cols  = 3

        for idx, fav in enumerate(favs):
            label   = fav.get("label", fav.get("command", ""))
            command = fav.get("command", "")
            btn = _CmdButton(
                command=label,
                name=label,
                description=fav.get("description", command if label != command else ""),
                variant="fav",
                has_params=bool(fav.get("params") or fav.get("get") or fav.get("caps")),
                raw_command=command,
            )
            set_tooltip(btn, f"<tt>{command}</tt>")
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if fav.get("params") or fav.get("get") or fav.get("caps"):
                btn.clicked.connect(
                    lambda f=deepcopy(fav): self._show_param_bar(
                        f.get("label", ""), f.get("command", ""), f.get("params", []),
                        f.get("get", ""), f.get("caps", "")))
            else:
                btn.clicked.connect(lambda cc=command: self._send_direct(cc))
            btn.move_requested.connect(
                lambda src, dst: self._swap_favs(src, dst)
            )
            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, f=dict(fav), i=idx:
                    self._fav_ctx_menu(b, pos, f, i)
            )
            g.addWidget(btn, idx // cols, idx % cols)

        lay.addWidget(grid)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        set_style(sep, "color:#21262d; max-height:1px; margin:4px 0;")
        lay.addWidget(sep)

        add_btn = QPushButton("⭐  Add to Favorites")
        add_btn.setObjectName("small")
        set_style(add_btn, "color:#ffd700; border-color:#ffd70044; margin-top:2px;")
        add_btn.clicked.connect(self._add_favorite)
        lay.addWidget(add_btn)

        lay.addStretch()
        scroll.setWidget(w)

    # ── General page ───────────────────────────────────────────────────────────

    def _fill_page(self, scroll: QScrollArea, fn_name: str, cmds: list):
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        if cmds:
            lay.addWidget(self._grid(cmds, "mixed", fn_name))
        else:
            ph = QLabel(
                "No commands for this category.\n"
                "Try a different manufacturer or add your own below."
            )
            ph.setObjectName("muted")
            ph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(ph, 1, Qt.AlignmentFlag.AlignCenter)

        add_btn = QPushButton(f"＋  Add command to  {fn_name}")
        add_btn.setObjectName("small")
        set_style(add_btn, "color:#56d364; border-color:#3fb95055; margin-top:4px;")
        add_btn.clicked.connect(lambda: self._add_user_cmd(fn_name))
        lay.addWidget(add_btn)
        lay.addStretch()
        scroll.setWidget(w)


    def _grid(self, cmds: list, variant: str, fn_name: str) -> QWidget:
        wrap = QWidget()
        g    = QGridLayout(wrap)
        g.setSpacing(3)
        g.setContentsMargins(0, 0, 0, 0)
        cols = 3
        for i, cmd in enumerate(cmds):
            tmpl   = cmd.get("command", "")
            params = cmd.get("params", [])
            name_  = cmd.get("name", cmd.get("label", tmpl))
            desc   = cmd.get("description", "")

            # "mixed" = flat reordered list — pick colour from each cmd's _src tag
            if variant == "mixed":
                src = cmd.get("_src", cmd.get("source", ""))
                v = ("cmd_user"   if src == "user"
                     else "cmd_vendor" if src == "vendor"
                     else "cmd_3gpp")
            else:
                v = variant

            btn = _CmdButton(
                command=name_ or tmpl,
                name=name_,
                description=desc,
                variant=v,
                has_params=bool(params or cmd.get("get") or cmd.get("caps")),
                raw_command=tmpl,
            )
            tip = (f"<tt>{tmpl}</tt><br>"
                   f"<span style='color:#8b949e'>{desc}</span>")
            if params:
                fields = ", ".join(p.get("label","") for p in params)
                tip += f"<br><i style='color:#e3b341'>⚙ Parameters: {fields}</i>"
            tip += "<br><i style='color:#484f58'>Right-click for more options</i>"
            set_tooltip(btn, tip)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            get_cmd  = cmd.get("get",  "")
            caps_cmd = cmd.get("caps", "")

            if params or get_cmd or caps_cmd:
                btn.clicked.connect(
                    lambda _name=name_, _tmpl=tmpl, _p=list(params),
                           _g=get_cmd, _c=caps_cmd:
                        self._show_param_bar(_name, _tmpl, _p, _g, _c)
                )
            else:
                btn.clicked.connect(
                    lambda cc=tmpl: self._send_direct(cc)
                )

            btn.customContextMenuRequested.connect(
                lambda pos, b=btn, cm=dict(cmd), f=fn_name, ac=cmds, ci=i:
                    self._cmd_ctx_menu(b, pos, cm, f, ac, ci)
            )
            btn.move_requested.connect(
                lambda src, dst, f=fn_name, ac=list(cmds):
                    self._swap_cmds(src, dst, f, ac)
            )
            g.addWidget(btn, i // cols, i % cols)
        return wrap

    # ── Context menus ──────────────────────────────────────────────────────────

    def _fav_ctx_menu(self, btn, pos, fav: dict, idx: int):
        menu = QMenu(self)
        menu.addAction(f"▶  Send  {fav.get('command','')}").triggered.connect(btn.clicked.emit)
        menu.addSeparator()
        menu.addAction("✎  Edit").triggered.connect(lambda: self._edit_favorite(idx))
        menu.addAction("✕  Remove from Favorites").triggered.connect(
            lambda: self._remove_favorite(idx)
        )
        menu.addSeparator()
        move_m = menu.addMenu("↕  Move")
        favs   = _load_favorites()
        n      = len(favs)
        if idx > 0:
            move_m.addAction("⤒  To Top").triggered.connect(
                lambda: self._move_fav(idx, "top"))
            move_m.addAction("↑  Move Up").triggered.connect(
                lambda: self._move_fav(idx, "up"))
        if idx < n - 1:
            move_m.addAction("↓  Move Down").triggered.connect(
                lambda: self._move_fav(idx, "down"))
            move_m.addAction("⤓  To Bottom").triggered.connect(
                lambda: self._move_fav(idx, "bottom"))
        menu.exec(btn.mapToGlobal(pos))

    def _cmd_ctx_menu(self, btn, pos, cmd: dict, fn_name: str,
                       all_cmds: list | None = None, cmd_idx: int = -1):
        menu = QMenu(self)
        # Same as a left-click: parameterised commands open the param bar
        # instead of sending the raw "{placeholder}" template.
        menu.addAction(f"▶  Send  {cmd.get('command','')}").triggered.connect(
            btn.clicked.emit
        )
        menu.addSeparator()
        menu.addAction("⭐  Add to Favorites").triggered.connect(
            lambda: self._add_cmd_to_favorites(cmd)
        )
        menu.addSeparator()
        menu.addAction("✎  Edit").triggered.connect(
            lambda: self._edit_cmd(cmd, fn_name)
        )
        menu.addAction("✕  Remove from this category").triggered.connect(
            lambda: self._remove_cmd(cmd, fn_name)
        )
        menu.exec(btn.mapToGlobal(pos))

    # ── Favorites CRUD ─────────────────────────────────────────────────────────

    def _add_favorite(self):
        dlg = _CmdDialog(self, label_hint="Label", cmd_hint="AT+…")
        if dlg.exec():
            r = dlg.get_result()
            favs = _load_favorites()
            favs.append({"label": r["name"], "command": r["command"],
                         "description": r["description"]})
            self._commit_config(favs)
            self._fill_favorites(self._pages[0])

    def _add_cmd_to_favorites(self, cmd: dict):
        favs  = _load_favorites()
        label = cmd.get("name", cmd.get("command", ""))
        entry = {"label": label, "command": cmd.get("command", "")}
        entry.update({key: deepcopy(cmd[key]) for key in ("description", "params", "get", "caps")
                      if key in cmd})
        if entry not in favs:
            favs.append(entry)
            self._commit_config(favs)
            self._fill_favorites(self._pages[0])

    def _edit_favorite(self, idx: int):
        favs = _load_favorites()
        if idx >= len(favs):
            return
        fav = favs[idx]
        dlg = _CmdDialog(self, name=fav.get("label",""),
                         command=fav.get("command",""),
                         description=fav.get("description", ""))
        if dlg.exec():
            r = dlg.get_result()
            favs[idx] = {**fav, "label": r["name"], "command": r["command"],
                         "description": r["description"]}
            self._commit_config(favs)
            self._fill_favorites(self._pages[0])

    def _remove_favorite(self, idx: int):
        favs = _load_favorites()
        if 0 <= idx < len(favs):
            favs.pop(idx)
            self._commit_config(favs)
            self._fill_favorites(self._pages[0])

    def _move_fav(self, idx: int, direction: str):
        favs = _load_favorites()
        n    = len(favs)
        if direction == "top":
            item = favs.pop(idx); favs.insert(0, item)
        elif direction == "up" and idx > 0:
            favs[idx], favs[idx - 1] = favs[idx - 1], favs[idx]
        elif direction == "down" and idx < n - 1:
            favs[idx], favs[idx + 1] = favs[idx + 1], favs[idx]
        elif direction == "bottom":
            item = favs.pop(idx); favs.append(item)
        self._commit_config(favs)
        self._fill_favorites(self._pages[0])

    # ── Category command CRUD ──────────────────────────────────────────────────

    def _add_user_cmd(self, fn_name: str):
        dlg = _CmdDialog(self)
        if dlg.exec():
            entry = {**dlg.get_result(), "function": fn_name, "source": "user",
                     "manufacturer": self._current_mfr}
            self._user.setdefault("added", []).append(entry)
            self._commit_config()
            self._rebuild_all()

    def _edit_cmd(self, cmd: dict, fn_name: str):
        dlg = _CmdDialog(self, name=cmd.get("name",""),
                         command=cmd.get("command",""),
                         description=cmd.get("description",""))
        if dlg.exec():
            self._hide_cmd(cmd, fn_name)
            # Keep read/capability commands and the full parameter schema.
            entry = {k: deepcopy(v) for k, v in cmd.items() if not k.startswith("_")}
            entry.update(dlg.get_result())
            entry.update(function=fn_name, source="user")
            if cmd.get("_src") == "vendor":
                entry["manufacturer"] = self._current_mfr
            self._user.setdefault("added", []).append(entry)
            self._commit_config()
            self._rebuild_all()

    def _remove_cmd(self, cmd: dict, fn_name: str):
        ans = QMessageBox.question(
            self, "Remove",
            f"Remove <b>{cmd.get('name','')}</b> "
            f"(<tt>{cmd.get('command','')}</tt>) from <b>{fn_name}</b>?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._hide_cmd(cmd, fn_name)
            self._commit_config()
            self._rebuild_all()

    def _hide_cmd(self, cmd: dict, fn_name: str):
        cstr = cmd.get("command", "")
        source = cmd.get("_src", cmd.get("source", "user"))
        if source == "user":
            original = {k: v for k, v in cmd.items() if not k.startswith("_")}
            added = self._user.get("added", [])
            if original in added:
                added.remove(original)
            return
        entry = {"function": fn_name, "command": cstr, "source": source}
        if source == "vendor":
            entry["manufacturer"] = self._current_mfr
        if entry not in self._user.setdefault("removed", []):
            self._user["removed"].append(entry)

    def _swap_cmds(self, src_cmd: str, dst_cmd: str, fn_name: str, all_cmds: list):
        if src_cmd == dst_cmd:
            return
        order = self._user.setdefault("order", {})
        if fn_name not in order:
            order[fn_name] = [c.get("command", "") for c in all_cmds]
        fn_order = order[fn_name]
        for cmd in (src_cmd, dst_cmd):
            if cmd not in fn_order:
                fn_order.append(cmd)
        si, di = fn_order.index(src_cmd), fn_order.index(dst_cmd)
        fn_order[si], fn_order[di] = fn_order[di], fn_order[si]
        self._commit_config()
        self._rebuild_all()

    def _save_all_config(self):
        """Explicit save — persists user ordering, added/removed commands, and favorites."""
        self._commit_config()
        # Flash the button to confirm save
        self._save_btn.setText("✓ Saved!")
        set_style(self._save_btn, "QPushButton{background:#1a3a1a;border:1px solid #3fb950;color:#3fb950;"
            "border-radius:4px;padding:0 10px;font-size:11px;}")
        QTimer.singleShot(1800, lambda: (
            self._save_btn.setText("💾 Save"),
            set_style(self._save_btn, "QPushButton{background:#21262d;border:1px solid #30363d;color:#8b949e;"
                "border-radius:4px;padding:0 8px;font-size:11px;}"
                "QPushButton:hover{background:#388bfd22;border-color:#388bfd;color:#e6edf3;}"),
        ))

    # ── Sidebar helpers ────────────────────────────────────────────────────────

    def _build_fn_to_page(self):
        self._fn_to_page = {}
        for i, (fn_name, _, _) in enumerate(FUNCTION_TABS):
            self._fn_to_page[fn_name] = self._pages[i]
        for i, fn_name in enumerate(self._user_categories):
            self._fn_to_page[fn_name] = self._pages[len(FUNCTION_TABS) + i]

    def _build_sidebar_items(self):
        renames    = self._user.get("sidebar_renames", {})
        saved_order = self._user.get("sidebar_order", [])
        all_names  = [t[0] for t in FUNCTION_TABS] + list(self._user_categories)
        if saved_order:
            visible = [fn for fn in saved_order if fn in all_names]
            for fn in all_names:
                if fn not in visible:
                    visible.append(fn)
        else:
            visible = all_names
        self._cat_list.blockSignals(True)
        self._cat_list.clear()
        for fn_name in visible:
            if fn_name in self._user.get("hidden_categories", []):
                continue
            item = QListWidgetItem(renames.get(fn_name, fn_name))
            item.setData(Qt.ItemDataRole.UserRole, fn_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._cat_list.addItem(item)
        self._cat_list.blockSignals(False)

    def _on_cat_changed(self, row: int):
        item = self._cat_list.item(row)
        if item:
            fn_name = item.data(Qt.ItemDataRole.UserRole)
            page = self._fn_to_page.get(fn_name)
            if page:
                self._stack.setCurrentWidget(page)
        self._param_bar.hide()

    def _on_cat_reordered(self, *_):
        order = [self._cat_list.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self._cat_list.count())]
        self._user["sidebar_order"] = order
        self._commit_config()

    def _on_cat_renamed(self, item: QListWidgetItem):
        fn_name = item.data(Qt.ItemDataRole.UserRole)
        if fn_name is None:
            return
        display = item.text().strip()
        if not display:
            renames = self._user.get("sidebar_renames", {})
            self._cat_list.blockSignals(True)
            item.setText(renames.get(fn_name, fn_name))
            self._cat_list.blockSignals(False)
            return
        renames = self._user.get("sidebar_renames", {})
        if display == renames.get(fn_name, fn_name):
            return
        renames = self._user.setdefault("sidebar_renames", {})
        if display == fn_name:
            renames.pop(fn_name, None)
        else:
            renames[fn_name] = display
        self._commit_config()

    def _cat_context_menu(self, pos):
        item = self._cat_list.itemAt(pos)
        if not item:
            return
        row = self._cat_list.row(item)
        fn_name = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("✎  Rename").triggered.connect(
            lambda: self._cat_list.editItem(item)
        )
        renames = self._user.get("sidebar_renames", {})
        if fn_name in renames:
            menu.addAction("↺  Restore original name").triggered.connect(
                lambda f=fn_name, it=item: self._restore_cat_name(f, it)
            )
        menu.addSeparator()
        menu.addAction("✕  Remove from sidebar").triggered.connect(
            lambda: self._delete_category(row, fn_name)
        )
        menu.exec(self._cat_list.mapToGlobal(pos))

    def _restore_cat_name(self, fn_name: str, item):
        renames = self._user.get("sidebar_renames", {})
        renames.pop(fn_name, None)
        self._commit_config()
        self._cat_list.blockSignals(True)
        item.setText(fn_name)
        self._cat_list.blockSignals(False)

    def _add_category(self):
        name, ok = QInputDialog.getText(self, "New Category", "Category name:")
        if not ok or not name.strip():
            return
        fn_name = name.strip()
        if fn_name in self._fn_to_page:
            QMessageBox.warning(self, "Exists", f"Category '{fn_name}' already exists.")
            return
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        search_idx = self._stack.count() - 1
        self._stack.insertWidget(search_idx, sa)
        self._pages.append(sa)
        self._fn_to_page[fn_name] = sa
        self._fill_page(sa, fn_name, [])
        item = QListWidgetItem(fn_name)
        item.setData(Qt.ItemDataRole.UserRole, fn_name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._cat_list.addItem(item)
        self._user_categories.append(fn_name)
        self._user["user_categories"] = self._user_categories
        self._commit_config()
        self._cat_list.setCurrentRow(self._cat_list.count() - 1)

    def _delete_category(self, row: int, fn_name: str):
        item = self._cat_list.item(row)
        if not item:
            return
        ans = QMessageBox.question(
            self, "Remove Category",
            f"Remove '{item.text()}' from the sidebar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        if fn_name not in self._user_categories:
            self._user.setdefault("hidden_categories", []).append(fn_name)
        self._cat_list.takeItem(row)
        if fn_name in self._user_categories:
            self._user_categories.remove(fn_name)
            self._user["user_categories"] = self._user_categories
            self._fn_to_page.pop(fn_name, None)
        order = self._user.get("sidebar_order", [])
        if fn_name in order:
            order.remove(fn_name)
            self._user["sidebar_order"] = order
        self._commit_config()

    def _swap_favs(self, src_cmd: str, dst_cmd: str):
        if src_cmd == dst_cmd:
            return
        favs = _load_favorites()
        si = next((i for i, f in enumerate(favs) if f.get("command") == src_cmd), -1)
        di = next((i for i, f in enumerate(favs) if f.get("command") == dst_cmd), -1)
        if si == -1 or di == -1:
            return
        favs[si], favs[di] = favs[di], favs[si]
        self._commit_config(favs)
        self._fill_favorites(self._pages[0])


# ── Dialog ─────────────────────────────────────────────────────────────────────

class _CmdDialog(QDialog):
    def __init__(self, parent=None, name="", command="", description="",
                 label_hint="Label shown on button", cmd_hint="e.g. AT+CEREG?"):
        super().__init__(parent)
        self.setWindowTitle("Command")
        self.setMinimumWidth(380)
        lay  = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit(name)
        self._name.setPlaceholderText(label_hint)
        form.addRow("Name:", self._name)

        self._cmd = QLineEdit(command)
        self._cmd.setPlaceholderText(cmd_hint)
        set_style(self._cmd, "font-family:'Cascadia Code','Consolas',monospace; font-size:12px;")
        form.addRow("Command:", self._cmd)

        self._desc = QLineEdit(description)
        self._desc.setPlaceholderText("Short description (shown in tooltip)")
        form.addRow("Description:", self._desc)

        lay.addLayout(form)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _validate(self):
        if not self._cmd.text().strip():
            QMessageBox.warning(self, "Missing", "Please enter an AT command.")
            return
        if not self._name.text().strip():
            self._name.setText(self._cmd.text().strip())
        self.accept()

    def get_result(self) -> dict:
        return {
            "name":        self._name.text().strip(),
            "command":     self._cmd.text().strip(),
            "description": self._desc.text().strip(),
        }


# ── Two-line command button ────────────────────────────────────────────────────

class _CmdButton(QFrame):
    """
    A two-line clickable frame displayed as a command button:
      Line 1 (top):    AT command in monospace — the primary info users need
      Line 2 (below):  Short description in small muted text — context at a glance
    Full description available as tooltip (hover).
    Supports left-click (clicked signal) and right-click (customContextMenuRequested).
    """
    clicked        = pyqtSignal()
    move_requested = pyqtSignal(str, str)   # (source_raw_cmd, target_raw_cmd)

    # Per-variant color palette
    _PAL = {
        "cmd_3gpp":  dict(bg="#0d1f33", border="#1f6feb44",
                          hbg="#1a3a5c", hborder="#58a6ff",
                          cmd="#79c0ff", desc="#3a5a80"),
        "cmd_vendor":dict(bg="#221600", border="#e3b34133",
                          hbg="#3d2a00", hborder="#e3b341",
                          cmd="#e3b341", desc="#6e5000"),
        "cmd_user":  dict(bg="#0a1f12", border="#3fb95033",
                          hbg="#1a4731", hborder="#56d364",
                          cmd="#56d364", desc="#2a6040"),
        "fav":       dict(bg="#1a1800", border="#ffd70033",
                          hbg="#2d2500", hborder="#ffd700",
                          cmd="#e8c800", desc="#6a5a00"),
    }

    def __init__(self, command: str, name: str = "", description: str = "",
                 variant: str = "cmd_3gpp", has_params: bool = False,
                 raw_command: str = "", parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setAcceptDrops(True)
        self._raw_command = raw_command or command
        self._drag_start  = None
        self._is_dragging = False
        self._orig_style  = ""

        # ── Hard size constraints so buttons NEVER push the splitter ──────────
        self.setMinimumWidth(0)
        self.setMaximumWidth(999)          # grid cell will set actual width
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        p   = self._PAL.get(variant, self._PAL["cmd_3gpp"])
        oid = f"cmdbtn_{id(self)}"
        self.setObjectName(oid)

        # Truncate display text — full command always in tooltip
        _MAX_CMD  = 19
        _MAX_DESC = 22
        cmd_display = command[:_MAX_CMD] + "…" if len(command) > _MAX_CMD else command

        # Line 1: AT command + optional ⚙ params indicator on the right
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(3)

        cmd_lbl = QLabel(cmd_display)
        set_style(cmd_lbl, f"color:{p['cmd']};"
            "font-family:'Cascadia Code','Consolas','Courier New',monospace;"
            "font-size:11px; font-weight:600;"
            "background:transparent; border:none; padding:0;")
        cmd_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        cmd_lbl.setWordWrap(False)
        cmd_lbl.setMinimumWidth(0)           # allow label to be smaller than text
        top_row.addWidget(cmd_lbl, 1)

        if has_params:
            p_ind = QLabel("⚙")
            set_style(p_ind, "color:#e3b341; font-size:9px;"
                "background:transparent; border:none; padding:0;")
            p_ind.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            top_row.addWidget(p_ind)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(7, 5, 7, 4)
        lay.setSpacing(1)
        lay.addLayout(top_row)

        # Line 2: short description — truncated strictly
        short = ""
        if description:
            short = description[:_MAX_DESC] + "…" if len(description) > _MAX_DESC else description
        elif name and name != command:
            short = name[:_MAX_DESC] + "…" if len(name) > _MAX_DESC else name

        if short:
            desc_lbl = QLabel(short)
            set_style(desc_lbl, f"color:{p['desc']}; font-size:9px;"
                "background:transparent; border:none; padding:0;")
            desc_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            desc_lbl.setWordWrap(False)
            desc_lbl.setMinimumWidth(0)
            lay.addWidget(desc_lbl)

        # Frame stylesheet with hover
        set_style(self, f"""
            QFrame#{oid} {{
                background: {p['bg']};
                border: 1px solid {p['border']};
                border-radius: 4px;
            }}
            QFrame#{oid}:hover {{
                background: {p['hbg']};
                border-color: {p['hborder']};
            }}
        """)
        self._orig_style = self.property("_theme_style")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start  = event.pos()
            self._is_dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._drag_start is None:
            return
        if (event.pos() - self._drag_start).manhattanLength() < 8:
            return
        self._is_dragging = True
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(self._raw_command)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(self._drag_start)
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_start  = None
        self._is_dragging = False

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._is_dragging:
            self.clicked.emit()
        self._drag_start  = None
        self._is_dragging = False
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() and event.source() is not self:
            event.acceptProposedAction()
            self._apply_drop_highlight(True)

    def dragLeaveEvent(self, event):
        self._apply_drop_highlight(False)

    def dropEvent(self, event):
        self._apply_drop_highlight(False)
        if event.mimeData().hasText() and event.source() is not self:
            self.move_requested.emit(event.mimeData().text(), self._raw_command)
            event.acceptProposedAction()

    def _apply_drop_highlight(self, on: bool):
        oid = self.objectName()
        if on:
            set_style(self, f"QFrame#{oid} {{ background: #1a3a5c;"
                f" border: 2px solid #58a6ff; border-radius: 4px; }}")
        else:
            set_style(self, self._orig_style)

    def contextMenuEvent(self, event):
        self.customContextMenuRequested.emit(event.pos())


# ── Inline parameter bar ───────────────────────────────────────────────────────

class _ParamBar(QFrame):
    """
    Action bar shown below the command grid when a command with get/caps/params
    is clicked. Shows [📖 Get] [🔍 Caps] action buttons and/or Set input fields.
    """
    send_requested = pyqtSignal(str)
    _COLS = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fields:   dict = {}
        self._template  = ""
        self._get_cmd   = ""
        self._caps_cmd  = ""
        self._setup_ui()
        self.hide()

    def _setup_ui(self):
        set_style(self, "QFrame { background:#0d1117; border-top:2px solid #1f6feb66; }")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # Header: title + [Get] [Caps] + preview + close
        hdr = QHBoxLayout()
        hdr.setSpacing(6)

        self._title = QLabel()
        set_style(self._title, "color:#c9d1d9; font-size:11px; font-weight:700;"
            "background:transparent; border:none;")
        hdr.addWidget(self._title)

        def _action_btn(text, color):
            b = QPushButton(text)
            b.setFixedHeight(22)
            set_style(b, f"QPushButton{{background:#161b22;border:1px solid {color}44;"
                f"color:{color};border-radius:3px;padding:0 8px;font-size:10px;}}"
                f"QPushButton:hover{{background:{color}22;border-color:{color};}}")
            return b

        self._get_btn  = _action_btn("\U0001f4d6 Get",  "#79c0ff")
        self._caps_btn = _action_btn("\U0001f50d Caps", "#8b949e")
        self._get_btn.clicked.connect(self._on_get)
        self._caps_btn.clicked.connect(self._on_caps)
        hdr.addWidget(self._get_btn)
        hdr.addWidget(self._caps_btn)

        hdr.addWidget(_sep_v())

        self._preview_lbl = QLabel()
        set_style(self._preview_lbl, "color:#79c0ff; font-family:\'Cascadia Code\',\'Consolas\',monospace;"
            "font-size:10px; background:transparent; border:none;")
        self._preview_lbl.setWordWrap(False)
        hdr.addWidget(self._preview_lbl, 1)

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(22, 22)
        set_style(close_btn, "QPushButton{background:transparent;border:none;color:#484f58;font-size:12px;}"
            "QPushButton:hover{color:#f85149;}")
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        root.addLayout(hdr)

        # Fields grid
        self._fields_w = QWidget()
        self._fields_lay = QGridLayout(self._fields_w)
        self._fields_lay.setContentsMargins(0, 0, 0, 0)
        self._fields_lay.setHorizontalSpacing(14)
        self._fields_lay.setVerticalSpacing(6)
        for c in range(self._COLS):
            self._fields_lay.setColumnStretch(c, 1)
        root.addWidget(self._fields_w)

        # Set button row
        send_row = QHBoxLayout()
        send_row.addStretch()
        self._send_btn = QPushButton("\U0001f4e4  Set")
        self._send_btn.setMinimumWidth(100)
        set_style(self._send_btn, "QPushButton{background:#1f6feb;border:none;color:#fff;"
            "border-radius:4px;padding:5px 16px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}")
        self._send_btn.clicked.connect(self._on_send)
        send_row.addWidget(self._send_btn)
        root.addLayout(send_row)

    # Public API

    def load(self, name: str, template: str, params: list,
             get_cmd: str = "", caps_cmd: str = ""):
        self._template = template
        self._get_cmd  = get_cmd
        self._caps_cmd = caps_cmd
        self._fields   = {}
        self._title.setText(f"\u2699  {name}")

        self._get_btn.setVisible(bool(get_cmd))
        self._caps_btn.setVisible(bool(caps_cmd))
        has_set = bool(params) and bool(template and "{" in template)
        self._fields_w.setVisible(has_set)
        self._send_btn.setVisible(has_set)
        self._preview_lbl.setVisible(has_set)

        while self._fields_lay.count():
            item = self._fields_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, p in enumerate(params):
            pid     = p.get("id", p.get("name", ""))
            label   = p.get("label", pid)
            default = str(p.get("default", ""))
            ptype   = p.get("type", "text")
            options = p.get("options", [])
            hint    = p.get("hint", "")

            cell = QWidget()
            cl   = QVBoxLayout(cell)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(2)

            lbl_w = QLabel(label)
            set_style(lbl_w, "color:#8b949e; font-size:10px;"
                "background:transparent; border:none;")
            cl.addWidget(lbl_w)

            if ptype == "select" and options:
                w = QComboBox()
                w.setEditable(True)            # user can type any custom value
                w.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                w.addItems([str(o) for o in options])
                idx = w.findText(default)
                if idx >= 0:
                    w.setCurrentIndex(idx)
                else:
                    w.setCurrentText(default)  # custom default not in list
                set_style(w.lineEdit(), "font-family:'Cascadia Code','Consolas',monospace;"
                    "font-size:11px; padding:2px 5px;")
                w.currentTextChanged.connect(self._update_preview)
            else:
                w = QLineEdit(default)
                if hint:
                    w.setPlaceholderText(hint)
                set_style(w, "font-family:\'Cascadia Code\',\'Consolas\',monospace;"
                    "font-size:11px; padding:2px 5px;")
                w.textChanged.connect(self._update_preview)

            cl.addWidget(w)
            self._fields[pid] = w
            self._fields_lay.addWidget(cell, i // self._COLS, i % self._COLS)

        if has_set:
            self._update_preview()
        self.show()

    # Helpers

    def _assemble(self) -> str:
        result = self._template
        for pid, w in self._fields.items():
            raw = w.currentText() if isinstance(w, QComboBox) else w.text()
            # Only predefined select options use the "value=label" convention.
            # Free-text APNs, passwords, etc. may legitimately contain '='.
            val = (raw.split("=", 1)[0].strip()
                   if isinstance(w, QComboBox) and w.findText(raw) >= 0 else raw)
            result = result.replace(f"{{{pid}}}", val)
        # Optional trailing parameters can be left blank (e.g. CERT deletion).
        return result.rstrip(",")

    def _update_preview(self):
        self._preview_lbl.setText(self._assemble())

    def _on_get(self):
        self.send_requested.emit(self._get_cmd)
        self.hide()

    def _on_caps(self):
        self.send_requested.emit(self._caps_cmd)
        self.hide()

    def _on_send(self):
        command = self._assemble()
        try:
            placeholders = {field for _, field, _, _ in Formatter().parse(self._template)
                            if field is not None}
        except ValueError:
            QMessageBox.warning(self, "Invalid template", "Check the braces in the command template.")
            return
        if placeholders - self._fields.keys():
            QMessageBox.warning(self, "Missing parameters",
                                "This command has placeholders without parameter fields. "
                                "Restore its original template or define its parameters first.")
            return
        self.send_requested.emit(command)
        self.hide()


def _sep_v() -> QFrame:
    """Thin vertical separator line for use inside HBoxLayouts."""
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    set_style(f, "color: #21262d;")
    f.setFixedWidth(1)
    return f
