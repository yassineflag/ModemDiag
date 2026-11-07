"""
ui/automation_panel.py
Unified Automation panel — replaces both DiagnosticPanel and MacroEditor.

• Lists ALL sequences from macros/ AND diagnostics/ folders in one sidebar.
• Run shared steps plus the selected vendor's tagged steps.
• Per-step result column shows live output during run.
• Bottom panel shows full parameters (Expect / Timeout / Delay / On Failure)
  for the currently selected step and allows inline editing.
• Editable name + description at the top.
• Every sequence is editable. Bundled diagnostic suites are read-only inside a
  packaged binary, so saving one there writes a copy to the user macros dir,
  which then overrides the bundled file of the same name.
"""
from modemdiag.ui.theme import set_background, set_html, set_style, set_tooltip
import os
import re
import shutil
from pathlib import Path
import yaml
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QGridLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QPlainTextEdit, QSpinBox, QComboBox, QInputDialog,
    QFileDialog, QMessageBox, QProgressBar, QFrame, QCheckBox, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal

from modemdiag.core.paths import get_data_dir, diagnostics_dir, macros_dir

MACROS_DIR = str(macros_dir())
DIAG_DIR   = str(diagnostics_dir())

_COL_CMD  = 0
_COL_DESC = 1
_COL_RES  = 2
_NCOLS    = 3
_BUNDLED_EXAMPLES = {"example_lte_check.yaml", "example_simcom_mqtt.yaml",
                     "example_quectel_certificate.yaml", "example_espressif_tcp.yaml"}


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


def _filename_for(name: str) -> str:
    """'LTE Check / v2' → 'lte_check_v2.yaml'"""
    stem = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return (stem or "automation") + ".yaml"


def _is_bundled(path: str) -> bool:
    resolved = Path(path).resolve()
    return (resolved.parent == Path(DIAG_DIR).resolve()
            or (resolved.parent == (get_data_dir() / "macros").resolve()
                and resolved.name in _BUNDLED_EXAMPLES))


class AutomationPanel(QWidget):
    """
    Signals:
        run_suite_requested(steps: list)  – list of step dicts
        stop_requested()
    """
    run_suite_requested = pyqtSignal(list)
    stop_requested      = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps: list[dict]    = []      # current sequence's full step data
        self._current_path: str     = ""
        self._dirty: bool           = False
        self._running: bool         = False
        self._execution_enabled = True
        self._current_step_idx: int = -1      # row index stored on selection
        self._run_rows: list[int]   = []      # runner step index → table row
        self._suite_metadata: dict = {}
        self._build_ui()
        self._refresh_list()

    # ═══════════════════════════════════════════════════ BUILD ══
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── LEFT sidebar ──────────────────────────────────────────────────────
        left = QWidget()
        left.setMinimumWidth(190)
        left.setMaximumWidth(270)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 4, 8)
        ll.setSpacing(6)

        cap = QLabel("Automation")
        cap.setObjectName("caption")
        ll.addWidget(cap)

        # Search
        self._list_search = QLineEdit()
        self._list_search.setPlaceholderText("Filter…")
        self._list_search.setFixedHeight(24)
        self._list_search.setClearButtonEnabled(True)
        self._list_search.textChanged.connect(self._filter_list)
        ll.addWidget(self._list_search)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.currentRowChanged.connect(self._on_selected)
        ll.addWidget(self._list, 1)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        for label, tip, slot in [
            ("＋", "New automation",            self._new),
            ("⧉",  "Duplicate as new copy",     self._duplicate),
            ("🗑",  "Delete selected",           self._delete),
            ("↑",  "Import YAML file",            self._import_file),
        ]:
            b = QPushButton(label)
            b.setObjectName("small")
            set_tooltip(b, tip)
            b.setFixedHeight(24)
            b.clicked.connect(slot)
            btns.addWidget(b)
        ll.addLayout(btns)
        splitter.addWidget(left)

        # ── RIGHT editor ──────────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 8, 8, 8)
        rl.setSpacing(6)

        # Name + run controls in one compact row
        header = QHBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Automation name…")
        self._name_edit.textChanged.connect(self._mark_dirty)
        header.addWidget(self._name_edit, 1)

        header.addWidget(QLabel("Run for:"))
        self._run_vendor = QComboBox()
        self._run_vendor.addItem("Common only", "")
        set_tooltip(self._run_vendor, "Untagged steps always run. Select a vendor to include its tagged steps.")
        header.addWidget(self._run_vendor)

        self._run_btn = QPushButton("▶  Run")
        self._run_btn.setObjectName("primary")
        self._run_btn.setEnabled(False)
        self._run_btn.setFixedHeight(30)
        self._run_btn.clicked.connect(self._on_run)
        header.addWidget(self._run_btn)

        self._stop_btn = QPushButton("⏹")
        self._stop_btn.setObjectName("danger")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setFixedHeight(30)
        self._stop_btn.setFixedWidth(36)
        self._stop_btn.clicked.connect(self._on_stop)
        header.addWidget(self._stop_btn)
        rl.addLayout(header)

        # Description — multi-line, compact
        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setPlaceholderText("Description (optional)…")
        self._desc_edit.setFixedHeight(54)
        set_style(self._desc_edit, "QPlainTextEdit{border:1px solid #21262d; border-radius:4px;"
            "padding:4px; font-size:12px; color:#8b949e;}"
            "QPlainTextEdit:focus{border-color:#30363d;}")
        self._desc_edit.textChanged.connect(self._mark_dirty)
        rl.addWidget(self._desc_edit)

        # Step table — Command / Description / Result
        self._table = QTableWidget(0, _NCOLS)
        self._table.setHorizontalHeaderLabels(["Command", "Description", "Result"])
        # Fixed-but-draggable Command/Description; Result takes the rest.
        # (ResizeToContents on Result let long responses squeeze the others.)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_CMD,  QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(_COL_DESC, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(_COL_RES,  QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(_COL_CMD,  150)
        self._table.setColumnWidth(_COL_DESC, 160)
        self._table.verticalHeader().setDefaultSectionSize(26)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellChanged.connect(self._on_cell_changed)
        self._table.currentCellChanged.connect(lambda row, *_: self._on_row_selected(row))
        rl.addWidget(self._table, 1)

        # Step action bar
        step_bar = QHBoxLayout()
        step_bar.setSpacing(4)
        for label, tip, slot in [
            ("＋ Step",  "Add step",       self._add_step),
            ("−",        "Remove step",     self._remove_step),
            ("↑",        "Move up",         self._move_up),
            ("↓",        "Move down",       self._move_down),
        ]:
            b = QPushButton(label)
            b.setObjectName("small")
            set_tooltip(b, tip)
            b.setFixedHeight(24)
            b.clicked.connect(slot)
            step_bar.addWidget(b)
        step_bar.addStretch()

        self._save_btn = QPushButton("💾 Save")
        self._save_btn.setFixedHeight(24)
        self._save_btn.clicked.connect(self._save)
        step_bar.addWidget(self._save_btn)

        exp_btn = QPushButton("↓ Export")
        exp_btn.setObjectName("small")
        exp_btn.setFixedHeight(24)
        exp_btn.clicked.connect(self._export)
        step_bar.addWidget(exp_btn)
        rl.addLayout(step_bar)

        # ── Step parameter panel — 2x2 grid ─────────────────────────────────
        self._param_frame = QFrame()
        self._param_frame.setFrameShape(QFrame.Shape.StyledPanel)
        set_style(self._param_frame, "QFrame{background:#161b22; border:1px solid #21262d;"
            "border-radius:6px;}")
        pf = QGridLayout(self._param_frame)
        pf.setContentsMargins(12, 10, 12, 10)
        pf.setHorizontalSpacing(14)
        pf.setVerticalSpacing(8)

        def _lbl(t):
            l = QLabel(t)
            set_style(l, "color:#8b949e; font-size:11px; background:transparent;")
            return l

        # Row 0: Expect  |  On Failure
        pf.addWidget(_lbl("Expect:"), 0, 0)
        self._p_expect = QLineEdit()
        self._p_expect.setPlaceholderText("OK")
        self._p_expect.setMinimumWidth(110)
        self._p_expect.textChanged.connect(self._param_changed)
        pf.addWidget(self._p_expect, 0, 1)

        pf.addWidget(_lbl("On Failure:"), 0, 2)
        self._p_failure = QComboBox()
        self._p_failure.setEditable(True)
        self._p_failure.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._p_failure.addItems(["continue", "stop", "retry"])
        self._p_failure.setMinimumWidth(100)
        self._p_failure.currentTextChanged.connect(self._param_changed)
        pf.addWidget(self._p_failure, 0, 3)

        # Row 1: Timeout  |  Delay
        pf.addWidget(_lbl("Timeout (ms):"), 1, 0)
        self._p_timeout = QSpinBox()
        self._p_timeout.setRange(100, 120000)
        self._p_timeout.setValue(5000)
        self._p_timeout.setSingleStep(500)
        self._p_timeout.setMinimumWidth(100)
        self._p_timeout.valueChanged.connect(self._param_changed)
        pf.addWidget(self._p_timeout, 1, 1)

        pf.addWidget(_lbl("Delay (ms):"), 1, 2)
        self._p_delay = QSpinBox()
        self._p_delay.setRange(0, 30000)
        self._p_delay.setValue(300)
        self._p_delay.setSingleStep(100)
        self._p_delay.setMinimumWidth(100)
        self._p_delay.valueChanged.connect(self._param_changed)
        pf.addWidget(self._p_delay, 1, 3)
        pf.addWidget(_lbl("Match:"), 2, 0)
        self._p_match = QComboBox()
        self._p_match.addItems(["Contains", "Regex"])
        self._p_match.currentTextChanged.connect(self._param_changed)
        pf.addWidget(self._p_match, 2, 1)
        self._advanced_toggle = QCheckBox("Completion / payload")
        pf.addWidget(self._advanced_toggle, 2, 2, 1, 2)
        self._advanced = QWidget()
        advanced = QGridLayout(self._advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        self._p_wait = QLineEdit()
        self._p_fail = QLineEdit()
        self._p_prompt = QLineEdit()
        self._p_final = QLineEdit("OK")
        self._p_payload = QPlainTextEdit()
        self._p_payload.setMaximumHeight(64)
        self._p_encoding = QComboBox()
        self._p_encoding.addItems(["utf-8", "hex"])
        self._p_ending = QComboBox()
        self._p_ending.addItems(["none", "ctrl-z", "crlf"])
        for row, (label, field) in enumerate((("Completion regex:", self._p_wait),
                                             ("Failure regex:", self._p_fail),
                                             ("Payload prompt:", self._p_prompt),
                                             ("Final success line:", self._p_final),
                                             ("Payload:", self._p_payload))):
            advanced.addWidget(_lbl(label), row, 0)
            advanced.addWidget(field, row, 1, 1, 3)
            field.textChanged.connect(self._param_changed)
        advanced.addWidget(_lbl("Encoding:"), 5, 0)
        advanced.addWidget(self._p_encoding, 5, 1)
        advanced.addWidget(_lbl("Ending:"), 5, 2)
        advanced.addWidget(self._p_ending, 5, 3)
        self._p_encoding.currentTextChanged.connect(self._param_changed)
        self._p_ending.currentTextChanged.connect(self._param_changed)
        set_tooltip(self._p_wait, "Wait for this regex after command OK, e.g. an asynchronous completion URC.")
        set_tooltip(self._p_fail, "A matching response line fails the step, including failure URCs after OK.")
        set_tooltip(self._p_prompt, "Send payload only after this prompt. Use {length} in the command for its byte count.")
        set_tooltip(self._p_payload, "Exact text or hex bytes; no newline is appended unless selected. "
                    "Stopping while a modem awaits input may require leaving its input mode manually.")
        self._advanced_toggle.toggled.connect(self._advanced.setVisible)
        self._advanced.hide()
        pf.addWidget(self._advanced, 3, 0, 1, 4)

        # stretch the last column so the grid doesn't bunch up
        pf.setColumnStretch(4, 1)

        self._param_scroll = QScrollArea()
        self._param_scroll.setWidgetResizable(True)
        self._param_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._param_scroll.setMinimumHeight(0)
        self._param_scroll.setMaximumHeight(170)
        self._param_scroll.setWidget(self._param_frame)
        self._advanced_toggle.toggled.connect(
            lambda expanded: self._param_scroll.setMaximumHeight(300 if expanded else 170))
        self._param_scroll.hide()
        rl.addWidget(self._param_scroll)

        # Progress + summary
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setFixedHeight(5)
        self._progress.setTextVisible(False)
        rl.addWidget(self._progress)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        set_style(self._status_lbl, "font-size:12px;")
        rl.addWidget(self._status_lbl)

        splitter.addWidget(right)
        splitter.setSizes([230, 620])
        root.addWidget(splitter)

    # ═══════════════════════════════════════════════════ PUBLIC SLOTS ══
    def set_execution_enabled(self, enabled: bool):
        self._execution_enabled = enabled
        self._run_btn.setEnabled(enabled and not self._running and bool(self._current_path))

    def on_step_started(self, index: int, command: str, desc: str):
        row = self._row_for(index)
        if row is not None:
            self._set_result(row, "⏳ Running…", "#1a3a5c")
        set_html(self._status_lbl, f"<span style='color:#79c0ff'>Step {index+1}: {command}</span>")
        self._progress.setValue(index + 1)

    def on_step_result(self, index: int, response: str, success: bool):
        row = self._row_for(index)
        if row is not None:
            short = response.strip()[:60].replace("\n", " ")
            icon  = "✅" if success else "❌"
            color = "#1a3d1a" if success else "#3d1a1a"
            self._set_result(row, f"{icon} {short}", color)

    def on_suite_finished(self, all_passed: bool, results: list):
        self._running = False
        self._run_btn.setEnabled(self._execution_enabled)
        self._stop_btn.setEnabled(False)
        self._progress.setVisible(False)
        passed = sum(1 for r in results if r["success"])
        total  = len(results)
        color  = "#3fb950" if all_passed else "#d29922" if passed > 0 else "#f85149"
        set_html(self._status_lbl, f"<span style='color:{color}; font-weight:600'>"
            f"Finished — {passed}/{total} steps passed</span>")

    # ═══════════════════════════════════════════════════ LIST ══
    def _refresh_list(self):
        self._list.clear()
        user_files = set(os.listdir(MACROS_DIR))
        # Diagnostic suites (unless overridden by a user copy of the same file)
        if os.path.isdir(DIAG_DIR) and DIAG_DIR != MACROS_DIR:
            for fname in sorted(os.listdir(DIAG_DIR)):
                if fname.endswith(".yaml") and fname not in user_files:
                    path = os.path.join(DIAG_DIR, fname)
                    data = _load_yaml(path)
                    if "name" not in data:
                        continue
                    icon = data.get("icon", "🔬")
                    item = QListWidgetItem(f"{icon} {data['name']}")
                    item.setData(Qt.ItemDataRole.UserRole, path)
                    self._list.addItem(item)
        # User macros
        for fname in sorted(os.listdir(MACROS_DIR)):
            if fname.endswith(".yaml"):
                path = os.path.join(MACROS_DIR, fname)
                data = _load_yaml(path)
                if "name" not in data:
                    continue
                icon = data.get("icon", "⚙")
                item = QListWidgetItem(f"{icon} {data['name']}")
                item.setData(Qt.ItemDataRole.UserRole, path)
                self._list.addItem(item)

    def _filter_list(self, text: str):
        q = text.strip().lower()
        for i in range(self._list.count()):
            it = self._list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _on_selected(self, row: int):
        item = self._list.item(row)
        if not item:
            return
        if self._dirty:
            ans = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Discard them?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            )
            if ans != QMessageBox.StandardButton.Discard:
                self._list.blockSignals(True)
                for i in range(self._list.count()):
                    if self._list.item(i).data(Qt.ItemDataRole.UserRole) == self._current_path:
                        self._list.setCurrentRow(i)
                        break
                self._list.blockSignals(False)
                return
        path = item.data(Qt.ItemDataRole.UserRole)
        data = _load_yaml(path)
        self._current_path = path
        self._load(data)
        self._run_btn.setEnabled(self._execution_enabled)
        self._dirty = False

    def _load(self, data: dict):
        self._suite_metadata = {key: value for key, value in data.items()
                                if key not in {"name", "description", "steps"}}
        self._table.blockSignals(True)
        self._name_edit.blockSignals(True)
        self._desc_edit.blockSignals(True)

        self._name_edit.setText(data.get("name", ""))
        self._desc_edit.setPlainText(data.get("description", ""))

        steps = data.get("steps", [])
        self._steps = []
        self._table.setRowCount(0)

        for step in steps:
            if not isinstance(step, dict):
                continue
            s = {
                **step,
                "command":    step.get("command", ""),
                "description": step.get("description", ""),
                "expect":     step.get("expect", "OK"),
                "timeout_ms": int(step.get("timeout_ms", 5000)),
                "delay_ms":   int(step.get("delay_ms", 300)),
                "on_failure": step.get("on_failure", "continue"),
            }
            self._steps.append(s)
            self._append_row(s)

        vendors = sorted({s.get("manufacturer", data.get("manufacturer", ""))
                          for s in self._steps} - {""})
        self._run_vendor.clear()
        self._run_vendor.addItem("Common only", "")
        for vendor in vendors:
            self._run_vendor.addItem(vendor, vendor)
        if len(vendors) > 1:
            self._run_vendor.addItem("All steps", "*")
        elif vendors:
            self._run_vendor.setCurrentIndex(1)

        self._table.blockSignals(False)
        self._name_edit.blockSignals(False)
        self._desc_edit.blockSignals(False)
        self._param_scroll.setVisible(False)
        set_html(self._status_lbl, "")

    # ═══════════════════════════════════════════════════ CRUD ══
    def _new(self):
        name, ok = QInputDialog.getText(self, "New Automation", "Name:")
        if not ok or not name.strip():
            return
        path = self._new_path(name)
        if not path:
            return
        _save_yaml(path, {"name": name.strip(), "description": "", "steps": []})
        self._refresh_list()
        self._select_by_path(path)

    def _duplicate(self):
        item = self._list.currentItem()
        if not item:
            return
        src  = item.data(Qt.ItemDataRole.UserRole)
        data = _load_yaml(src)
        name, ok = QInputDialog.getText(
            self, "Duplicate", "New name:", text=data.get("name", "") + " (copy)")
        if not ok or not name.strip():
            return
        path = self._new_path(name)
        if not path:
            return
        data["name"] = name.strip()
        _save_yaml(path, data)
        self._refresh_list()
        self._select_by_path(path)

    def _delete(self):
        item = self._list.currentItem()
        if not item:
            return
        if _is_bundled(item.data(Qt.ItemDataRole.UserRole)):
            QMessageBox.information(self, "Bundled automation",
                                    "Bundled examples are kept as defaults. "
                                    "Duplicate or save a local copy to customize them.")
            return
        ans = QMessageBox.question(
            self, "Delete", f"Delete '{item.text().strip()}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            os.remove(item.data(Qt.ItemDataRole.UserRole))
            self._refresh_list()
            self._table.setRowCount(0)
            self._steps.clear()
            self._current_path = ""
            self._run_btn.setEnabled(False)
            self._param_scroll.setVisible(False)
            self._status_lbl.clear()

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import YAML", MACROS_DIR, "YAML files (*.yaml *.yml)")
        if path:
            dest = os.path.join(MACROS_DIR, os.path.basename(path))
            shutil.copy2(path, dest)
            self._refresh_list()
            self._select_by_path(dest)

    def _export(self):
        if not self._current_path:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Automation",
            os.path.basename(self._current_path),
            "YAML files (*.yaml)")
        if path:
            shutil.copy2(self._current_path, path)

    def _new_path(self, name: str) -> str:
        """Path for a new automation file, or "" if the user declines to overwrite."""
        path = os.path.join(MACROS_DIR, _filename_for(name))
        if os.path.exists(path):
            ans = QMessageBox.question(
                self, "Already Exists",
                f"'{os.path.basename(path)}' already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return ""
        return path

    def _select_by_path(self, path: str):
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == path:
                self._list.setCurrentRow(i)
                return

    # ═══════════════════════════════════════════════════ STEP EDITING ══
    def _append_row(self, step: dict):
        row = self._table.rowCount()
        self._table.insertRow(row)
        command_item = QTableWidgetItem(step.get("command", ""))
        command_item.setFlags(command_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        command_item.setCheckState(Qt.CheckState.Checked if step.get("enabled", True)
                                   else Qt.CheckState.Unchecked)
        self._table.setItem(row, _COL_CMD, command_item)
        self._table.setItem(row, _COL_DESC, QTableWidgetItem(step.get("description", "")))
        result_item = QTableWidgetItem("")
        result_item.setFlags(result_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, _COL_RES, result_item)

    def _on_cell_changed(self, row: int, col: int):
        if row >= len(self._steps):
            return
        item = self._table.item(row, col)
        if not item:
            return
        if col == _COL_CMD:
            self._steps[row]["command"] = item.text()
            self._steps[row]["enabled"] = item.checkState() == Qt.CheckState.Checked
        elif col == _COL_DESC:
            self._steps[row]["description"] = item.text()
        self._mark_dirty()

    def _on_row_selected(self, row: int):
        if row < 0 or row >= len(self._steps):
            self._param_scroll.setVisible(False)
            return
        s = self._steps[row]
        self._param_frame.blockSignals(True)
        self._p_expect.blockSignals(True)
        self._p_timeout.blockSignals(True)
        self._p_delay.blockSignals(True)
        self._p_failure.blockSignals(True)
        advanced_fields = (self._p_match, self._p_wait, self._p_fail, self._p_prompt, self._p_final,
                           self._p_payload, self._p_encoding, self._p_ending)
        for field in advanced_fields:
            field.blockSignals(True)

        self._p_expect.setText(s.get("expect_regex", s.get("expect", "OK")))
        self._p_match.setCurrentText("Regex" if s.get("expect_regex") else "Contains")
        self._p_wait.setText(s.get("wait_for", ""))
        self._p_fail.setText(s.get("fail_on", ""))
        self._p_prompt.setText(s.get("prompt", ""))
        self._p_final.setText(s.get("final_ok", "OK"))
        self._p_payload.setPlainText(s.get("payload", ""))
        self._p_encoding.setCurrentText(s.get("payload_encoding", "utf-8"))
        self._p_ending.setCurrentText(s.get("payload_ending", "none"))
        self._advanced_toggle.setChecked(any(key in s for key in ("prompt", "wait_for", "fail_on", "payload", "final_ok")))
        self._p_timeout.setValue(int(s.get("timeout_ms", 5000)))
        self._p_delay.setValue(int(s.get("delay_ms", 300)))
        fi = self._p_failure.findText(s.get("on_failure", "continue"))
        self._p_failure.setCurrentIndex(max(fi, 0))

        self._p_expect.blockSignals(False)
        self._p_timeout.blockSignals(False)
        self._p_delay.blockSignals(False)
        self._p_failure.blockSignals(False)
        for field in advanced_fields:
            field.blockSignals(False)
        self._param_frame.blockSignals(False)

        self._current_step_idx = row
        self._param_scroll.setVisible(True)

    def _param_changed(self, *_):
        row = self._current_step_idx
        if row < 0 or row >= len(self._steps):
            return
        step = self._steps[row]
        if self._p_final.text().strip() not in ("", "OK"):
            step["final_ok"] = self._p_final.text().strip()
        else:
            step.pop("final_ok", None)
        if self._p_match.currentText() == "Regex":
            step["expect_regex"] = self._p_expect.text().strip()
        else:
            step.pop("expect_regex", None)
            step["expect"] = self._p_expect.text().strip() or "OK"
        for key, field in (("wait_for", self._p_wait), ("fail_on", self._p_fail), ("prompt", self._p_prompt)):
            value = field.text().strip()
            if value:
                step[key] = value
            else:
                step.pop(key, None)
        if self._p_prompt.text().strip() or self._p_payload.toPlainText():
            step["payload"] = self._p_payload.toPlainText()
            step["payload_encoding"] = self._p_encoding.currentText()
            step["payload_ending"] = self._p_ending.currentText()
        else:
            for key in ("payload", "payload_encoding", "payload_ending"):
                step.pop(key, None)
        self._steps[row]["timeout_ms"] = self._p_timeout.value()
        self._steps[row]["delay_ms"]   = self._p_delay.value()
        self._steps[row]["on_failure"] = self._p_failure.currentText().strip() or "continue"
        self._mark_dirty()

    def _add_step(self):
        s = {"command": "", "description": "",
             "expect": "OK", "timeout_ms": 5000, "delay_ms": 300, "on_failure": "continue"}
        self._steps.append(s)
        self._table.blockSignals(True)
        self._append_row(s)
        self._table.blockSignals(False)
        self._table.setCurrentCell(len(self._steps) - 1, _COL_CMD)
        self._mark_dirty()

    def _remove_step(self):
        row = self._table.currentRow()
        if row < 0:
            return
        self._table.removeRow(row)
        self._steps.pop(row)
        self._mark_dirty()

    def _move_up(self):
        row = self._table.currentRow()
        if row > 0:
            self._steps[row], self._steps[row-1] = self._steps[row-1], self._steps[row]
            self._reload_table()
            self._table.setCurrentCell(row - 1, _COL_CMD)

    def _move_down(self):
        row = self._table.currentRow()
        if 0 <= row < len(self._steps) - 1:
            self._steps[row], self._steps[row+1] = self._steps[row+1], self._steps[row]
            self._reload_table()
            self._table.setCurrentCell(row + 1, _COL_CMD)

    def _reload_table(self):
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for s in self._steps:
            self._append_row(s)
        self._table.blockSignals(False)

    def _save(self):
        if not self._current_path:
            return
        data = {
            **self._suite_metadata,
            "name":        self._name_edit.text().strip(),
            "description": self._desc_edit.toPlainText().strip(),
            "steps":       self._steps,
        }
        # Keep bundled defaults unchanged, including when running from source.
        if _is_bundled(self._current_path):
            destination = Path(MACROS_DIR) / Path(self._current_path).name
            if _is_bundled(str(destination)):
                destination = destination.with_stem(destination.stem + "_custom")
            if destination.exists():
                answer = QMessageBox.question(
                    self, "Overwrite local copy?",
                    f"A local copy named '{destination.name}' exists. Replace it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self._current_path = str(destination)
        _save_yaml(self._current_path, data)
        self._dirty = False
        self._refresh_list()
        self._list.blockSignals(True)
        self._select_by_path(self._current_path)
        self._list.blockSignals(False)

    # ═══════════════════════════════════════════════════ RUN / STOP ══
    def _on_run(self):
        if not self._execution_enabled:
            return
        # Keep the original table rows when blank or other-vendor steps are skipped.
        vendor = self._run_vendor.currentData()
        self._run_rows = [i for i, s in enumerate(self._steps)
                          if s.get("command", "").strip()
                          and s.get("enabled", True)
                          and (vendor == "*" or s.get("manufacturer", self._suite_metadata.get(
                              "manufacturer", "")) in ("", vendor))]
        steps = [self._steps[i] for i in self._run_rows]
        if not steps:
            QMessageBox.warning(self, "No matching steps",
                                "Choose a vendor with commands or add a command step before running.")
            return
        # Clear result column
        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_RES)
            if item:
                item.setText("")
                set_background(item, "#161b22")
        self._table.blockSignals(False)

        self._running = True
        self._progress.setMaximum(len(steps))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        set_html(self._status_lbl, "<span style='color:#79c0ff'>Starting…</span>")
        self.run_suite_requested.emit(steps)

    def _on_stop(self):
        self.stop_requested.emit()
        self._running = False
        self._run_btn.setEnabled(self._execution_enabled)
        self._stop_btn.setEnabled(False)
        self._progress.setVisible(False)
        set_html(self._status_lbl, "<span style='color:#d29922'>Stopped by user</span>")

    # ═══════════════════════════════════════════════════ HELPERS ══
    def _mark_dirty(self, *_):
        self._dirty = True

    def _row_for(self, index: int) -> int | None:
        if 0 <= index < len(self._run_rows):
            row = self._run_rows[index]
            if row < self._table.rowCount():
                return row
        return None

    def _set_result(self, row: int, text: str, bg_hex: str):
        self._table.blockSignals(True)
        item = self._table.item(row, _COL_RES)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, _COL_RES, item)
        item.setText(text)
        for col in range(_NCOLS):
            it = self._table.item(row, col)
            if it:
                set_background(it, bg_hex)
        self._table.blockSignals(False)
