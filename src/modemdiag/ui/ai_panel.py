"""AI Diag beta: connection setup, diagnostic chat and reviewed AT plans."""
import json
import os
from html import escape

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QCheckBox, QGroupBox, QTextBrowser,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QSplitter, QDialog, QDialogButtonBox, QScrollArea,
)

from modemdiag.core.ai_client import AIClient
from modemdiag.core.ai_diagnostics import (
    AIConfig, DEFAULT_MODEL, MAX_ROUNDS, load_config, save_config,
    execution_steps, redact,
)
from modemdiag.ui.theme import set_style, set_background


class AIDiagPanel(QWidget):
    commands_requested = pyqtSignal(list, bool)
    stop_requested = pyqtSignal()
    terminal_toggled = pyqtSignal(bool)

    def __init__(self, settings_path, parent=None):
        super().__init__(parent)
        self._settings_path = settings_path
        self._client = AIClient(self)
        self._client.completed.connect(self._on_answer)
        self._client.failed.connect(self._on_error)
        self._context_provider = lambda *args: '{"modem_connected": false}'
        self._connected = False
        self._allowed = True
        self._executing = False
        self._controlling = False
        self._request_kind = ""
        self._pending_analysis = False
        self._messages = []
        self._proposals = []
        self._review_queue = []
        self._run_rows = []
        self._rounds = 0
        self._generation = 0
        self._session_config = None
        self._last_connection = None
        self._build_ui()
        self.reload_settings()

    def reload_settings(self):
        config = load_config(self._settings_path)
        self._provider.setCurrentIndex(max(0, self._provider.findData(config.provider)))
        self._model.setText(config.model)
        self._base.setText(config.base_url)
        self._provider_changed()
        # Restore custom preferences after provider defaults are applied.
        self._model.setText(config.model)
        if config.provider == "compatible":
            self._base.setText(config.base_url)
        self._new_chat()

    def _label(self, text="", muted=False):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        if muted:
            label.setObjectName("muted")
        return label

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        header = QHBoxLayout()
        title = self._label("AI diagnostics")
        set_style(title, "font-size:22px; font-weight:600; color:#e6edf3;")
        header.addWidget(title)
        badge = self._label("★  BETA TESTING")
        set_style(badge, "color:#d29922; font-size:11px; font-weight:700; padding:4px 10px; background:#332800; border-radius:6px;")
        header.addWidget(badge)
        header.addStretch()
        self._terminal_toggle = QPushButton("Terminal")
        self._terminal_toggle.setCheckable(True)
        self._terminal_toggle.setObjectName("small")
        self._terminal_toggle.toggled.connect(self.terminal_toggled)
        header.addWidget(self._terminal_toggle)
        self._clear = QPushButton("New chat")
        self._clear.setObjectName("small")
        self._clear.clicked.connect(self._new_chat)
        header.addWidget(self._clear)
        root.addLayout(header)
        root.addWidget(self._label("Describe a modem issue, gather evidence, and work through the next diagnostic steps.", True))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        sidebar = QScrollArea()
        sidebar.setWidgetResizable(True)
        sidebar.setFrameShape(QScrollArea.Shape.NoFrame)
        sidebar.setMinimumWidth(245)
        sidebar.setMaximumWidth(335)
        options = QWidget()
        left = QVBoxLayout(options)
        left.setContentsMargins(0, 4, 10, 0)
        left.setSpacing(10)
        connection = QGroupBox("AI CONNECTION")
        form = QFormLayout(connection)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self._provider = QComboBox()
        self._provider.addItem("OpenAI", "openai")
        self._provider.addItem("OpenAI-compatible / local", "compatible")
        self._provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow("Provider", self._provider)
        self._model = QLineEdit(DEFAULT_MODEL)
        self._model.setPlaceholderText("Model ID available to your account")
        form.addRow("Model", self._model)
        self._base = QLineEdit("https://api.openai.com/v1")
        self._base.setPlaceholderText("https://your-server.example/v1")
        form.addRow("API base URL", self._base)
        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText("Session only, or OPENAI_API_KEY")
        form.addRow("API key", self._key)
        self._key_link = QPushButton("Get an OpenAI API key ↗")
        self._key_link.setObjectName("small")
        self._key_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://platform.openai.com/api-keys")))
        form.addRow(self._key_link)
        self._test = QPushButton("Test AI connection")
        self._test.clicked.connect(self._test_connection)
        form.addRow(self._test)
        self._connection_status = self._label("Not tested · API authentication", True)
        form.addRow(self._connection_status)
        form.addRow(self._label("Keys stay in memory. ChatGPT subscription billing is separate from API access. Testing sends a small AI request.", True))
        left.addWidget(connection)

        control = QGroupBox("DIAGNOSTIC MODE")
        box = QVBoxLayout(control)
        self._mode = QComboBox()
        self._mode.addItem("Review & run commands", "review")
        self._mode.addItem("Chat only", "chat")
        self._mode.addItem("Auto diagnostics · read queries", "auto")
        box.addWidget(self._mode)
        box.addWidget(self._label("Auto runs up to 4 rounds / 24 approved read queries. Configuration changes remain in the review queue.", True))
        self._overview = QCheckBox("Share modem overview")
        self._overview.setChecked(True)
        box.addWidget(self._overview)
        self._recent = QCheckBox("Share recent terminal lines")
        box.addWidget(self._recent)
        preview = QPushButton("Preview shared context")
        preview.setObjectName("small")
        preview.clicked.connect(self._preview_context)
        box.addWidget(preview)
        box.addWidget(self._label("Conversation and selected command results are shared with your chosen provider. No chat is saved to disk.", True))
        left.addWidget(control)
        left.addStretch()
        sidebar.setWidget(options)
        splitter.addWidget(sidebar)

        workspace = QWidget()
        right = QVBoxLayout(workspace)
        right.setContentsMargins(6, 4, 0, 0)
        right.setSpacing(8)
        shortcuts = QHBoxLayout()
        for caption, prompt in (("No network", "My modem is not registering on the network. Help me find the cause."),
                                ("Poor signal", "Help me diagnose weak or unstable signal using the reported cell measurements."),
                                ("Data not working", "The modem registers but data does not work. Help me inspect packet attachment, APN and IP context.")):
            button = QPushButton(caption)
            button.setObjectName("small")
            button.clicked.connect(lambda checked=False, text=prompt: self._input.setPlainText(text))
            shortcuts.addWidget(button)
        shortcuts.addStretch()
        right.addLayout(shortcuts)
        self._chat = QTextBrowser()
        self._chat.document().setMaximumBlockCount(500)
        self._chat.setOpenLinks(False)
        self._chat.setMinimumHeight(130)
        right.addWidget(self._chat, 1)
        self._plan_box = QGroupBox("PROPOSED AT COMMANDS")
        plan_layout = QVBoxLayout(self._plan_box)
        self._plan = QTableWidget(0, 4)
        self._plan.setHorizontalHeaderLabels(["Run", "Command", "Access", "Reason / result"])
        self._plan.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._plan.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._plan.verticalHeader().hide()
        self._plan.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._plan.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._plan.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._plan.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._plan.setMinimumHeight(100)
        self._plan.setMaximumHeight(170)
        plan_layout.addWidget(self._plan)
        actions = QHBoxLayout()
        self._copy = QPushButton("Copy command")
        self._copy.setObjectName("small")
        self._copy.clicked.connect(self._copy_command)
        actions.addWidget(self._copy)
        actions.addStretch()
        self._run = QPushButton("Run selected && analyze")
        self._run.setObjectName("primary")
        self._run.clicked.connect(self._run_selected)
        actions.addWidget(self._run)
        plan_layout.addLayout(actions)
        plan_layout.addWidget(self._label("Read queries are preselected. Review changes are unchecked. Manual-only commands can be copied to the terminal.", True))
        right.addWidget(self._plan_box)
        self._input = QPlainTextEdit()
        self._input.setPlaceholderText("Describe the issue…  Ctrl+Enter to send")
        self._input.setFixedHeight(75)
        QShortcut(QKeySequence("Ctrl+Return"), self._input, activated=self._send)
        right.addWidget(self._input)
        footer = QHBoxLayout()
        self._status = self._label("Ready", True)
        footer.addWidget(self._status, 1)
        self._stop = QPushButton("Stop")
        self._stop.setObjectName("danger")
        set_style(self._stop, "QPushButton#danger:disabled {background:#161b22; color:#484f58; border:1px solid #21262d;}")
        self._stop.clicked.connect(lambda: self.stop("Stopped by user."))
        footer.addWidget(self._stop)
        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("primary")
        self._send_btn.clicked.connect(self._send)
        footer.addWidget(self._send_btn)
        right.addLayout(footer)
        splitter.addWidget(workspace)
        splitter.setSizes([285, 900])
        root.addWidget(splitter, 1)

    @property
    def is_controlling(self):
        return self._controlling or self._executing

    def set_context_provider(self, provider):
        self._context_provider = provider

    def set_connected(self, connected):
        changed = self._connected != connected
        self._connected = connected
        if changed:
            self.stop("Modem connection changed. Start a fresh diagnostic question.", quiet=True)
            self._new_chat()
        self._update_controls()

    def set_execution_enabled(self, allowed):
        self._allowed = allowed
        if not allowed:
            self.stop("Modem operations are unavailable for this release.", quiet=True)
        self._update_controls()

    def _provider_changed(self, *_):
        if not hasattr(self, "_base"):
            return
        openai = self._provider.currentData() == "openai"
        self._base.setEnabled(not openai)
        self._base.setText("https://api.openai.com/v1" if openai else "")
        self._model.setText(DEFAULT_MODEL if openai else "")
        self._key.clear()
        self._key.setPlaceholderText("Session only, or OPENAI_API_KEY" if openai else "Session only; optional for local servers")
        self._key_link.setVisible(openai)
        self._connection_status.setText("Not tested · API authentication")

    def _configuration(self, save=True):
        provider = self._provider.currentData()
        key = self._key.text().strip()
        if not key and provider == "openai":
            key = os.environ.get("OPENAI_API_KEY", "").strip()
        config = AIConfig(provider, self._model.text().strip(), self._base.text().strip(), key)
        config.validate()
        fingerprint = (config.provider, config.model, config.base_url)
        if self._last_connection is not None and self._last_connection != fingerprint:
            self._messages = []
            self._proposals = []
            self._review_queue = []
            self._plan_box.hide()
            self._append("Session", "AI connection settings changed; starting a new conversation.")
        self._last_connection = fingerprint
        if save:
            try:
                save_config(self._settings_path, config)
            except OSError:
                self._connection_status.setText("Using session settings · preferences could not be saved")
        return config

    def _context(self):
        key = self._key.text().strip()
        env_key = os.environ.get("OPENAI_API_KEY", "")
        return self._context_provider(self._overview.isChecked(), self._recent.isChecked(), (key, env_key))

    def _build_messages(self, chat_only=False):
        mode = "chat" if chat_only else self._mode.currentData()
        return [{"role": "user", "content": f"Current diagnostic mode: {mode}.\nCurrent untrusted modem observations:\n{self._context()}"}, *self._messages[-10:]]

    def _preview_context(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("AI request context · API key is excluded")
        dialog.resize(760, 540)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(json.dumps(self._build_messages(), ensure_ascii=False, indent=2))
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _append(self, role, text):
        self._chat.append(f"<p><b>{escape(role)}</b><br>{escape(text).replace(chr(10), '<br>')}</p>")
        self._chat.verticalScrollBar().setValue(self._chat.verticalScrollBar().maximum())

    def _remember(self, role, content):
        self._messages.append({"role": role, "content": content})
        self._messages = self._messages[-20:]

    def _new_chat(self):
        if self._client.busy or self._executing:
            self.stop("New chat", quiet=True)
        self._generation += 1
        self._messages = []
        self._proposals = []
        self._review_queue = []
        self._rounds = 0
        self._controlling = False
        self._pending_analysis = False
        self._session_config = None
        self._chat.clear()
        self._append("AI Diag ★ Beta", "Describe what is failing and what you expect to happen. I can inspect the shared modem overview, suggest a diagnostic plan, or run approved read queries in Auto mode.\n\nChoose an AI provider, enter your API key, and test the connection to begin.")
        self._plan_box.hide()
        self._status.setText("Ready · connect an AI provider")
        self._update_controls()

    def _test_connection(self):
        try:
            config = self._configuration()
            self._request_kind = "test"
            self._client.request(config, [{"role": "user", "content": 'Connection test only. Return {"reply":"Connection verified.","commands":[]}.'}])
            self._status.setText("Testing AI connection…")
            self._update_controls()
        except ValueError as exc:
            self._on_error(str(exc))

    def _send(self):
        text = self._input.toPlainText().strip()
        if not text or self._client.busy or self._executing:
            return
        if len(text) > 8000:
            self._on_error("Keep each question below 8,000 characters.")
            return
        if self._mode.currentData() == "auto" and (not self._connected or not self._allowed):
            self._on_error("Connect a modem with operations enabled before using Auto diagnostics; Chat only works without a modem.")
            return
        try:
            self._session_config = self._configuration()
        except ValueError as exc:
            self._on_error(str(exc))
            return
        self._generation += 1
        self._rounds = 0
        self._controlling = self._mode.currentData() == "auto"
        self._proposals = []
        self._review_queue = []
        self._plan_box.hide()
        self._input.clear()
        text = redact(text, (self._session_config.api_key, os.environ.get("OPENAI_API_KEY", "")))
        self._remember("user", text)
        self._append("You", text)
        self._request_analysis()

    def _request_analysis(self, chat_only=False):
        self._pending_analysis = False
        try:
            self._request_kind = "summary" if chat_only else "analysis"
            self._rounds += 1
            self._client.request(self._session_config, self._build_messages(chat_only))
            self._status.setText(f"AI is analyzing… · round {self._rounds}/{MAX_ROUNDS}" if self._controlling else "AI is analyzing…")
            self._update_controls()
        except ValueError as exc:
            self._on_error(str(exc))

    def _show_plan(self, proposals):
        self._proposals = proposals
        self._plan.setRowCount(len(proposals))
        for row, proposal in enumerate(proposals):
            item = QTableWidgetItem()
            if proposal["access"] != "manual":
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if proposal["access"] == "read" else Qt.CheckState.Unchecked)
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._plan.setItem(row, 0, item)
            access = {"read": "Read query", "review": "Review change", "manual": "Manual only"}[proposal["access"]]
            for column, value in enumerate((proposal["command"], access, proposal["reason"]), 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self._plan.setItem(row, column, item)
        self._plan_box.setVisible(bool(proposals))

    def _on_answer(self, plan):
        if self._request_kind == "test":
            self._connection_status.setText("✓ AI connection verified")
            self._status.setText("AI ready · describe a modem issue")
            self._request_kind = ""
            self._update_controls()
            return
        self._append("AI Diag", plan["reply"])
        self._remember("assistant", json.dumps(plan, ensure_ascii=False))
        proposals = [] if self._mode.currentData() == "chat" or self._request_kind == "summary" else plan["commands"]
        if self._mode.currentData() == "auto":
            for proposal in plan["commands"]:
                if proposal["access"] != "read" and not any(item["command"] == proposal["command"] for item in self._review_queue):
                    self._review_queue.append(proposal)
            self._review_queue = self._review_queue[-12:]
            proposals = [item for item in proposals if item["access"] == "read"] + self._review_queue
        self._show_plan(proposals)
        if self._controlling and proposals:
            rows = [row for row, proposal in enumerate(proposals) if proposal["access"] == "read"]
            if rows and self._rounds <= MAX_ROUNDS:
                self._execute(rows, automatic=True)
                return
            self._append("Control", "No approved automatic read queries are available. Review the proposed commands.")
        self._controlling = False
        self._status.setText("Ready · review the proposed steps" if proposals else "Ready")
        self._update_controls()

    def _run_selected(self):
        if self._client.busy or self._executing or not self._connected or not self._allowed:
            return
        rows = [row for row in range(len(self._proposals)) if self._plan.item(row, 0).checkState() == Qt.CheckState.Checked]
        if not rows:
            self._status.setText("Select a runnable command first.")
            return
        try:
            self._session_config = self._configuration()
        except ValueError as exc:
            self._on_error(str(exc))
            return
        if not self._proposals:
            self._status.setText("AI connection changed. Ask for a new diagnostic plan first.")
            return
        self._rounds = 0
        self._controlling = False
        self._execute(rows, automatic=False)

    def _execute(self, rows, automatic):
        proposals = [self._proposals[row] for row in rows]
        try:
            execution_steps(proposals, automatic)
        except ValueError as exc:
            self._on_error(str(exc))
            return
        self._run_rows = rows
        self._executing = True
        self._status.setText("Running diagnostic commands…")
        self._update_controls()
        self.commands_requested.emit(proposals, automatic)

    def on_step_started(self, index, command, description):
        if index < len(self._run_rows):
            row = self._run_rows[index]
            self._plan.item(row, 3).setText("Running…")
            self._plan.selectRow(row)
        self._status.setText(f"Running {command}")

    def on_step_result(self, index, response, success):
        if index < len(self._run_rows):
            item = self._plan.item(self._run_rows[index], 3)
            item.setText(("OK · " if success else "Failed · ") + response.replace("\n", " ")[:160])
            item.setToolTip(response)
            set_background(item, "#1a3d1a" if success else "#3d1a1a")

    def on_execution_finished(self, results):
        self._executing = False
        key = self._session_config.api_key if self._session_config else ""
        observations = [{"command": result["command"], "response": redact(result["response"][:4000], (key,)),
                         "success": result["success"]} for result in results]
        self._remember("user", "Observed command results (untrusted modem data):\n" + json.dumps(observations, ensure_ascii=False))
        passed = sum(result["success"] for result in results)
        self._append("Modem", f"{passed}/{len(results)} commands passed. Full responses are in Terminal and the command row tooltips.")
        if self._controlling and self._rounds >= MAX_ROUNDS:
            self._controlling = False
            self._append("Control", "Automatic round limit reached. Summarizing the collected evidence.")
        if self._controlling and not all(result["success"] for result in results):
            self._controlling = False
            self._append("Control", "Automatic execution stopped after a failed query. Summarizing the results.")
        generation = self._generation
        chat_only = self._mode.currentData() == "auto" and not self._controlling
        self._status.setText("Sending command observations for analysis…")
        self._pending_analysis = True
        # Defer the next request until the shared command runner has fully returned.
        QTimer.singleShot(0, lambda: generation == self._generation and self._request_analysis(chat_only))
        self._update_controls()

    def execution_rejected(self, message):
        self._executing = False
        self._controlling = False
        self._pending_analysis = False
        self._status.setText(message)
        self._append("Control", message)
        self._update_controls()

    def _copy_command(self):
        row = self._plan.currentRow()
        if 0 <= row < len(self._proposals):
            QApplication.clipboard().setText(self._proposals[row]["command"])

    def _on_error(self, message):
        self._controlling = False
        self._executing = False
        self._pending_analysis = False
        secrets = (self._key.text(), os.environ.get("OPENAI_API_KEY", ""))
        message = redact(message, secrets)
        self._status.setText(message)
        self._append("Connection / diagnostics", message)
        self._update_controls()

    def stop(self, message="Stopped.", quiet=False):
        self._generation += 1
        self._client.cancel()
        self._controlling = False
        self._pending_analysis = False
        was_executing, self._executing = self._executing, False
        if was_executing:
            self.stop_requested.emit()
        self._status.setText(message)
        if not quiet:
            self._append("Control", message)
        self._update_controls()

    def _update_controls(self):
        busy = self._client.busy or self._executing or self._controlling or self._pending_analysis
        for widget in (self._provider, self._model, self._key, self._test, self._mode, self._input, self._overview, self._recent):
            widget.setEnabled(not busy)
        self._base.setEnabled(not busy and self._provider.currentData() == "compatible")
        self._send_btn.setEnabled(not busy)
        self._clear.setEnabled(not busy)
        self._stop.setEnabled(busy)
        self._run.setEnabled(not busy and self._connected and self._allowed and bool(self._proposals))
        self._plan.setEnabled(not busy)
