"""Radio overview with cell exploration, signal history and service context."""
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QComboBox, QListWidget, QListWidgetItem, QTabWidget, QFrame, QSizePolicy,
)

from modemdiag.core.network_status import NetworkSnapshot, NOT_REPORTED
from modemdiag.core.network_history import NetworkHistory, stronger_neighbour, cell_name
from modemdiag.ui.network_visuals import SignalBars, CellLandscape, SignalTimeline
from modemdiag.ui.theme import set_style


class DashboardPanel(QWidget):
    refresh_requested = pyqtSignal()
    terminal_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._busy = False
        self._allowed = True
        self._snapshot = NetworkSnapshot()
        self._history = NetworkHistory()
        self._selected_cell = -1
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(10000)
        self._timer.timeout.connect(self._poll)
        self._live.toggled.connect(self._set_live)
        self.set_connected(False)

    def _label(self, text="", muted=False):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if muted:
            label.setObjectName("muted")
        return label

    def _card(self, title):
        card = QGroupBox(title)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 10)
        box.setSpacing(5)
        return card, box

    def _headline(self, size=20):
        label = self._label("—")
        set_style(label, f"font-size:{size}px; font-weight:600; color:#58a6ff;")
        return label

    def _summary_card(self, title):
        card = QFrame()
        card.setObjectName("summaryCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        set_style(card, "QFrame#summaryCard { background:#161b22; border:1px solid #30363d; border-radius:5px; }")
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 7, 10, 7)
        box.setSpacing(2)
        caption = self._label(title, True)
        set_style(caption, "font-size:10px; color:#8b949e; font-weight:600;")
        box.addWidget(caption)
        return card, box

    def _table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(28)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(150)
        return table

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        set_style(content, "QGroupBox { margin-top:10px; padding:8px 8px 6px 8px; }")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 10, 18, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = self._label("Radio overview")
        set_style(title, "font-size:22px; font-weight:600; color:#e6edf3;")
        header.addWidget(title, 1)
        self._terminal_toggle = QPushButton("Terminal")
        self._terminal_toggle.setCheckable(True)
        self._terminal_toggle.setObjectName("small")
        self._terminal_toggle.toggled.connect(self.terminal_toggled)
        header.addWidget(self._terminal_toggle)
        self._live = QCheckBox("Live · 10 s")
        self._live.setToolTip("Refresh every 10 seconds while this tab is visible. Pauses during automation or manual commands.")
        header.addWidget(self._live)
        self._refresh = QPushButton("Refresh")
        self._refresh.setObjectName("primary")
        self._refresh.clicked.connect(self.refresh_requested)
        header.addWidget(self._refresh)
        layout.addLayout(header)
        self._status = self._label(muted=True)
        layout.addWidget(self._status)

        summary = QHBoxLayout()
        self._health = self._label("Waiting for modem information")
        set_style(self._health, "font-size:15px; font-weight:600; color:#8b949e;")
        summary.addWidget(self._health, 1)
        self._session = self._label("No samples yet", True)
        self._session.setWordWrap(False)
        summary.addWidget(self._session)
        layout.addLayout(summary)

        heroes = QGridLayout()
        heroes.setSpacing(6)
        network, box = self._summary_card("NETWORK")
        self._operator = self._headline(16)
        self._network_hint = self._label(muted=True)
        box.addWidget(self._operator)
        box.addWidget(self._network_hint)
        heroes.addWidget(network, 0, 0)
        radio, box = self._summary_card("RADIO")
        self._technology = self._headline(16)
        self._radio_hint = self._label(muted=True)
        box.addWidget(self._technology)
        box.addWidget(self._radio_hint)
        heroes.addWidget(radio, 0, 1)
        signal, box = self._summary_card("RECEIVED POWER")
        power_row = QHBoxLayout()
        power_row.setContentsMargins(0, 0, 0, 0)
        self._power = self._headline(16)
        self._signal_bars = SignalBars()
        self._signal_bars.setFixedSize(84, 22)
        self._power_hint = self._label(muted=True)
        power_row.addWidget(self._power, 1)
        power_row.addWidget(self._signal_bars)
        box.addLayout(power_row)
        box.addWidget(self._power_hint)
        heroes.addWidget(signal, 0, 2)
        data, box = self._summary_card("DATA SESSION")
        self._ip = self._headline(15)
        self._data_hint = self._label(muted=True)
        box.addWidget(self._ip)
        box.addWidget(self._data_hint)
        heroes.addWidget(data, 0, 3)
        for column in range(4):
            heroes.setColumnStretch(column, 1)
        layout.addLayout(heroes)

        visuals = QGridLayout()
        visuals.setColumnStretch(0, 3)
        visuals.setColumnStretch(1, 2)
        landscape, box = self._card("CELL LANDSCAPE")
        self._cell_note = self._label("No cell information yet.", True)
        box.addWidget(self._cell_note)
        self._landscape = CellLandscape()
        self._landscape.cell_selected.connect(self._select_cell)
        box.addWidget(self._landscape, 1)
        self._selection_title = self._label("Select a cell to inspect its radio details")
        set_style(self._selection_title, "font-size:14px; font-weight:600; color:#e6edf3;")
        self._selection_meta = self._label("Cell positions are schematic; location and distance are not measured.", True)
        box.addWidget(self._selection_title)
        box.addWidget(self._selection_meta)
        self._neighbour_note = self._label("Click a cell or use the arrow keys to explore.", True)
        box.addWidget(self._neighbour_note)
        visuals.addWidget(landscape, 0, 0)

        trace, box = self._card("SIGNAL OVER TIME")
        trace_controls = QHBoxLayout()
        self._trace_stats = self._label("Waiting for measurements", True)
        trace_controls.addWidget(self._trace_stats, 1)
        self._trace_metric = QComboBox()
        self._trace_metric.addItems(["RSRP", "RSSI", "RSRQ", "SINR"])
        self._trace_metric.currentTextChanged.connect(self._update_trace)
        trace_controls.addWidget(self._trace_metric)
        box.addLayout(trace_controls)
        self._timeline = SignalTimeline()
        box.addWidget(self._timeline, 1)
        grid = QGridLayout()
        self._signals = {}
        for index, key in enumerate(("RSSI", "RSRP", "RSRQ", "SINR")):
            row, column = divmod(index, 2)
            pair = QVBoxLayout()
            pair.setSpacing(3)
            pair.addWidget(self._label(key, True))
            value = self._label("—")
            set_style(value, "font-size:16px; font-weight:600; color:#e6edf3;")
            pair.addWidget(value)
            grid.addLayout(pair, row, column)
            self._signals[key] = value
        box.addLayout(grid)
        hint = self._label("Last 120 refreshes · gaps mean no measurement · ≈ marks quantized estimates", True)
        box.addWidget(hint)
        visuals.addWidget(trace, 0, 1)
        layout.addLayout(visuals)

        services, box = self._card("SERVICE READINESS")
        grid = QGridLayout()
        self._services = {}
        labels = (("SIM", "SIM"), ("Attachment", "Packet attach"), ("EPS / LTE", "EPS · LTE"),
                  ("5GS", "5GS · 5G"), ("IMS", "IMS"), ("CS", "CS · calls"), ("PS", "PS · GPRS/UMTS"))
        for column, (key, text) in enumerate(labels):
            grid.addWidget(self._label(text, True), 0, column)
            value = self._label("—")
            grid.addWidget(value, 1, column)
            grid.setColumnStretch(column, 1)
            self._services[key] = value
        box.addLayout(grid)
        layout.addWidget(services)

        detail_row = QGridLayout()
        detail_row.setColumnStretch(0, 3)
        detail_row.setColumnStretch(1, 2)
        details, box = self._card("RADIO & DATA DETAILS")
        tabs = QTabWidget()
        self._cells = self._table(["Role", "Radio", "Cell ID", "TAC/LAC", "PCI/PSC", "Channel", "Band", "PLMN", "RSRP", "RSRQ", "SINR", "RSSI"])
        self._cells.currentCellChanged.connect(lambda row, *_: self._select_cell(row))
        self._contexts = self._table(["CID", "State", "Protocol", "APN", "IP addresses"])
        tabs.addTab(self._cells, "Cells")
        tabs.addTab(self._contexts, "Data contexts")
        box.addWidget(tabs)
        self._device = self._label(muted=True)
        self._device.setMaximumHeight(38)
        box.addWidget(self._device)
        detail_row.addWidget(details, 0, 0)
        activity, box = self._card("NETWORK EVENTS")
        self._events = QListWidget()
        self._events.setMinimumHeight(180)
        self._events.setWordWrap(True)
        box.addWidget(self._events)
        box.addWidget(self._label("Observed at refresh time · cell changes are not a complete handover log", True))
        detail_row.addWidget(activity, 0, 1)
        layout.addLayout(detail_row)
        self._availability = self._label(muted=True)
        layout.addWidget(self._availability)
        layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll)

    def set_connected(self, connected):
        self._connected = connected
        self._busy = False
        self._live.setChecked(False)
        self._history = NetworkHistory()
        self.show_snapshot(NetworkSnapshot(), timestamp=False)
        self._device.setText("Click Refresh to read modem information." if connected else "Connect a modem, then click Refresh.")
        self._status.setText("Connected · ready to refresh" if connected else "Disconnected · connect a modem to start")
        self._update_controls()

    def set_execution_enabled(self, allowed):
        self._allowed = allowed
        if not allowed:
            self._live.setChecked(False)
        self._update_controls()

    def set_busy(self, busy, message=""):
        self._busy = busy
        if message:
            self._status.setText(message)
        self._update_controls()

    def _update_controls(self):
        available = self._connected and self._allowed
        self._refresh.setEnabled(available and not self._busy)
        self._live.setEnabled(available)

    def _set_live(self, live):
        if live and self._connected and self._allowed:
            self._timer.start()
            self._poll()
        else:
            self._timer.stop()

    def _poll(self):
        if self.isVisible() and self._connected and self._allowed and not self._busy:
            self.refresh_requested.emit()

    def _update_trace(self, *_):
        metric = self._trace_metric.currentText()
        self._timeline.set_data(self._history.samples, metric)
        values = [sample.values[metric] for sample in self._history.samples if sample.values.get(metric) is not None]
        unit = "dBm" if metric in ("RSRP", "RSSI") else "dB"
        self._trace_stats.setText(f"Min {min(values):g} · Max {max(values):g} {unit}" if values else "Waiting for measurements")
        samples = list(self._history.samples)
        duration = int((samples[-1].at - samples[0].at).total_seconds()) if len(samples) > 1 else 0
        self._session.setText(f"{len(samples)} samples · {duration // 60}m {duration % 60:02d}s · {self._history.cell_changes} cell changes")

    def _select_cell(self, index):
        if not 0 <= index < len(self._snapshot.cells):
            self._selected_cell = -1
            self._selection_title.setText("Select a cell to inspect its radio details")
            self._selection_meta.setText("Schematic view · tower location and distance are not measured.")
            return
        self._selected_cell = index
        self._landscape.set_selected(index)
        cell = self._snapshot.cells[index]
        self._selection_title.setText(f"{cell.role} · {cell_name(cell)} · RSRP {cell.rsrp.text}")
        self._selection_meta.setText(f"Cell {cell.cell_id} · TAC/LAC {cell.area} · Band {cell.band} · Channel {cell.channel}\n"
                                     f"PLMN {cell.plmn} · {cell.duplex} · {cell.state}\n"
                                     f"RSRQ {cell.rsrq.text} · SINR {cell.sinr.text} · RSSI {cell.rssi.text}")
        self._selection_meta.setToolTip(cell.source)
        if self._cells.currentRow() != index:
            self._cells.selectRow(index)

    def _show_health(self, snapshot):
        registered = any(value.startswith("Registered") for domain, value in snapshot.registrations.items() if domain != "IMS")
        if snapshot.sim not in ("READY", NOT_REPORTED):
            text, color = f"SIM · {snapshot.sim}", "#d29922"
        elif snapshot.functionality in ("Radio disabled", "Minimum functionality"):
            text, color = snapshot.functionality, "#d29922"
        elif snapshot.attached == "Attached":
            text, color = "● Packet service attached", "#3fb950"
        elif registered:
            text, color = "● Registered on the network", "#3fb950"
        elif "Searching" in snapshot.registrations.values():
            text, color = "● Searching for a network", "#d29922"
        elif "Registration denied" in snapshot.registrations.values():
            text, color = "● Network registration denied", "#f85149"
        else:
            text, color = "Waiting for network information", "#8b949e"
        if snapshot.sim == "READY":
            text += " · SIM ready"
        ims = snapshot.registrations["IMS"]
        if ims in ("Registered", "Not registered"):
            text += " · IMS " + ims.lower()
        self._health.setText(text)
        set_style(self._health, f"font-size:15px; font-weight:600; color:{color};")

    def show_snapshot(self, snapshot, timestamp=True, observed_at=None):
        self._snapshot = snapshot
        if timestamp:
            self._history.add(snapshot, observed_at)
            if len(self._history.samples) == 1 and snapshot.signals["RSRP"].value is None and snapshot.signals["RSSI"].value is not None:
                self._trace_metric.setCurrentText("RSSI")
        self._operator.setText(snapshot.operator)
        self._technology.setText(snapshot.technology)
        self._network_hint.setText(f"PLMN {snapshot.plmn}\n{snapshot.selection}")
        serving = [cell for cell in snapshot.cells if cell.role == "Serving"]
        bands = list(dict.fromkeys(cell.band for cell in serving if cell.band != "—"))
        states = list(dict.fromkeys(cell.state for cell in serving if cell.state != "—"))
        self._radio_hint.setText(("Band " + " + ".join(bands) if bands else "Band not reported") + "\n" +
                                 (" · ".join(states) if states else snapshot.functionality))
        primary = "RSRP" if snapshot.signals["RSRP"].value is not None else "RSSI"
        self._power.setText(snapshot.signals[primary].text)
        self._power_hint.setText(primary + " · received signal power")
        self._signal_bars.set_reading(primary, snapshot.signals[primary].value)
        for key, value in snapshot.signals.items():
            self._signals[key].setText(value.text)
        addresses = [address for context in snapshot.contexts if context.active is not False for address in context.addresses]
        active = [context for context in snapshot.contexts if context.active]
        known_states = [context for context in snapshot.contexts if context.active is not None]
        apns = list(dict.fromkeys(context.apn for context in (active or snapshot.contexts) if context.apn != "—"))
        self._ip.setText(addresses[0] if addresses else "No IP reported")
        self._ip.setToolTip("\n".join(addresses))
        context_state = (f"{len(active)} active context" + ("s" if len(active) != 1 else "")
                         if known_states else "Context state not reported")
        self._data_hint.setText(("APN " + ", ".join(apns) if apns else "APN not reported") + "\n" + context_state)
        self._show_health(snapshot)
        self._device.setText(snapshot.device)
        self._device.setToolTip(snapshot.device)
        statuses = {"SIM": snapshot.sim, "Attachment": snapshot.attached, **snapshot.registrations}
        for key, text in statuses.items():
            label = self._services[key]
            label.setText(text)
            color = ("#3fb950" if text in ("READY", "Attached") or text.startswith("Registered")
                     else "#8b949e" if text == NOT_REPORTED else "#d29922")
            set_style(label, f"color:{color}; font-weight:600;")
        self._cells.blockSignals(True)
        self._cells.setRowCount(len(snapshot.cells))
        for row, cell in enumerate(snapshot.cells):
            values = (cell.role, cell.technology, cell.cell_id, cell.area, cell.pci, cell.channel,
                      cell.band, cell.plmn, cell.rsrp.text, cell.rsrq.text, cell.sinr.text, cell.rssi.text)
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(cell.source)
                self._cells.setItem(row, column, item)
        self._cells.blockSignals(False)
        self._landscape.set_cells(snapshot.cells)
        self._select_cell(self._landscape.selected)
        neighbours = sum(cell.role == "Neighbour" for cell in snapshot.cells)
        self._cell_note.setText(f"{len(serving)} serving · {neighbours} neighbours · schematic, not a location map")
        comparison = stronger_neighbour(snapshot)
        self._neighbour_note.setText(
            f"Neighbour PCI {comparison[1].pci} is {comparison[0]:g} dB stronger on the same radio and channel."
            if comparison else "Solid links: serving cells · dotted links: reported neighbours · click to inspect")
        self._contexts.setRowCount(len(snapshot.contexts))
        for row, context in enumerate(snapshot.contexts):
            state = "Active" if context.active is True else "Inactive" if context.active is False else NOT_REPORTED
            for column, value in enumerate((context.cid, state, context.protocol, context.apn,
                                            ", ".join(context.addresses) or NOT_REPORTED)):
                self._contexts.setItem(row, column, QTableWidgetItem(value))
        self._events.clear()
        for event in self._history.events:
            item = QListWidgetItem(f"{event.at:%H:%M:%S}  {event.message}")
            item.setToolTip(event.message)
            self._events.addItem(item)
        if not self._history.events:
            self._events.addItem("Network changes will appear after refreshes.")
        self._update_trace()
        self._availability.setText(
            f"{len(snapshot.unavailable)} queries unavailable or timed out. Hover for the list; open Terminal for responses."
            if snapshot.unavailable else "Read-only modem observations · IP addresses do not establish Internet reachability")
        self._availability.setToolTip("\n".join(snapshot.unavailable))
        if timestamp:
            at = observed_at or datetime.now().astimezone()
            self._status.setText(f"Updated {at:%H:%M:%S} · Refresh or enable Live to follow the network")
